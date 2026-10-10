"""
Tests for proj7k.generator (ADR-0027): decoding, the onset envelope, the tempo estimate, the beat grid,
column assignment, the star closed loop and the command line, on synthetic songs whose every attack
time is known (`generator_synth`).
"""

import collections
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from generator_synth import SR, drum_song, write_wav
from proj7k.engine import evaluate_osu
from proj7k.generator import Audio, AudioDecodeError, GeneratorOptions, estimate_tempo, generate, load_audio, onset_envelope
from proj7k.generator.cli import main
from proj7k.generator.columns import Row, assign_columns
from proj7k.generator.grid import DOWNBEAT, build_grid
from proj7k.generator.onset import FRAME_S, frame_time
from proj7k.parser import TimingPoint, dump_osu_7k, parse_osu_7k


@pytest.fixture(scope="module")
def song():
    samples, attacks = drum_song(bpm=170.0, offset_s=0.5, measures=24, seed=3)
    return Audio(samples, SR, "synth"), attacks


def _rows(beatmap):
    rows = collections.defaultdict(list)
    for o in beatmap.hit_objects:
        rows[o.time].append(o.column)
    return rows


# --- audio ----------------------------------------------------------------------------------------------------


def test_wav_decodes_and_resamples_to_the_analysis_rate(tmp_path):
    samples, _ = drum_song(bpm=150.0, measures=2, sr=44100)
    write_wav(tmp_path / "a.wav", samples, sr=44100)
    audio = load_audio(tmp_path / "a.wav")
    assert audio.sample_rate == SR and audio.source == "wave"
    assert abs(audio.duration_s - len(samples) / 44100) < 0.01
    assert np.abs(audio.samples).max() <= 1.0


def test_a_missing_or_unreadable_file_is_reported(tmp_path):
    with pytest.raises(AudioDecodeError):
        load_audio(tmp_path / "nope.mp3")
    (tmp_path / "junk.wav").write_bytes(b"not audio at all")
    with pytest.raises(AudioDecodeError):
        load_audio(tmp_path / "junk.wav")


# --- onset and tempo ------------------------------------------------------------------------------------------


def test_onset_peaks_land_on_the_attacks(song):
    audio, attacks = song
    env = onset_envelope(audio)
    errors = [frame_time(env.peak(a, 0.04)) - a for a in attacks]
    # Within one 10 ms frame of the attack, after the measured lag is taken out.
    assert abs(np.median(errors)) <= FRAME_S / 2
    assert np.percentile(np.abs(errors), 90) <= 1.5 * FRAME_S


@pytest.mark.parametrize("bpm, offset_s", [(128.0, 1.234), (170.0, 0.5), (200.0, 0.31)])
def test_tempo_and_beat_phase_are_found(bpm, offset_s):
    samples, _ = drum_song(bpm=bpm, offset_s=offset_s, measures=32, seed=int(bpm))
    est = estimate_tempo(onset_envelope(Audio(samples, SR, "synth")))
    assert est.bpm == pytest.approx(bpm, abs=0.05)
    beat_ms = 60000.0 / bpm
    phase_err = (est.offset_ms - offset_s * 1000.0 + beat_ms / 2) % beat_ms - beat_ms / 2
    assert abs(phase_err) <= 10.0
    assert est.steady and est.confidence > 0.5


def test_tempo_needs_some_music():
    with pytest.raises(ValueError, match="BPM"):
        estimate_tempo(onset_envelope(Audio(np.zeros(SR * 10, dtype=np.float32), SR, "synth")))


# --- grid and columns -----------------------------------------------------------------------------------------


def test_grid_follows_every_timing_section_and_finds_the_attacks(song):
    audio, attacks = song
    env = onset_envelope(audio)
    beat = 60000.0 / 170.0
    timing = [TimingPoint(time=500.0, beat_length=beat), TimingPoint(time=500.0 + 32 * beat, beat_length=beat / 2 * 2)]
    grid = build_grid(env, timing)
    times = np.array([p.time_ms for p in grid])
    assert np.all(np.diff(times) > 0)
    assert grid[0].time_ms < 500.0  # the first section reaches back to the start of the song
    downbeats = [p for p in grid if p.level == DOWNBEAT]
    assert downbeats and all(abs(((p.time_ms - 500.0) / (4 * beat)) - round((p.time_ms - 500.0) / (4 * beat))) < 1e-6 for p in downbeats)
    attack_ms = np.array(attacks) * 1000.0
    strong = [p for p in grid if p.strength > 0.3]
    assert np.mean([np.min(np.abs(attack_ms - p.time_ms)) < 2.0 for p in strong]) > 0.95


def test_columns_spread_load_and_avoid_fast_jacks():
    rows = [Row(time_ms=i * 90.0, size=1 + (i % 3 == 0), brightness=(i * 37 % 11) / 10) for i in range(400)]
    cols = assign_columns(rows, seed=1)
    assert [len(c) for c in cols] == [r.size for r in rows]
    assert all(len(set(c)) == len(c) and all(0 <= x < 7 for x in c) for c in cols)
    counts = collections.Counter(x for c in cols for x in c)
    assert max(counts.values()) < 1.6 * min(counts.values())
    assert not any(set(a) & set(b) for a, b in zip(cols, cols[1:]))  # no same-column repeat 90 ms apart
    assert cols == assign_columns(rows, seed=1)
    assert cols != assign_columns(rows, seed=2)


# --- the closed loop ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target, on_attack", [(2.0, 0.95), (4.42, 0.8)])
def test_generated_chart_lands_on_the_target(song, target, on_attack):
    audio, attacks = song
    result = generate(audio, GeneratorOptions(target_stars=target))
    assert result.stars == pytest.approx(target, abs=0.1)
    assert not result.warnings
    assert result.timing_source == "auto" and result.tempo.bpm == pytest.approx(170.0, abs=0.05)
    # The star the result reports is the engine's reading of the chart it wrote.
    assert evaluate_osu(dump_osu_7k(result.beatmap)).total_stars == pytest.approx(result.stars, abs=1e-9)
    # Notes sit on the song's attacks; a busy target also fills some quiet grid points (a stream).
    attack_ms = np.array(attacks) * 1000.0
    times = sorted(_rows(result.beatmap))
    assert np.mean([np.min(np.abs(attack_ms - t)) <= 20.0 for t in times]) > on_attack


def test_more_stars_means_more_notes_and_the_seed_changes_only_columns(song):
    audio, _ = song
    easy = generate(audio, GeneratorOptions(target_stars=2.0))
    hard = generate(audio, GeneratorOptions(target_stars=4.42))
    assert hard.n_notes > easy.n_notes
    again = generate(audio, GeneratorOptions(target_stars=2.0))
    assert dump_osu_7k(again.beatmap) == dump_osu_7k(easy.beatmap)
    other = generate(audio, GeneratorOptions(target_stars=2.0, seed=5))
    assert sorted(_rows(other.beatmap)) == sorted(_rows(easy.beatmap)) or abs(other.stars - 2.0) <= 0.1
    assert dump_osu_7k(other.beatmap) != dump_osu_7k(easy.beatmap)


def test_a_sparse_song_says_how_far_it_can_go():
    samples, _ = drum_song(bpm=90.0, measures=12, melody=False)
    result = generate(Audio(samples, SR, "synth"), GeneratorOptions(target_stars=9.0, bpm=90.0, offset_ms=500.0))
    assert result.stars < 9.0 and result.timing_source == "manual"
    assert any("最高只能生成" in w for w in result.warnings)


def test_reference_timing_is_used_as_is(song):
    audio, _ = song
    beat = 60000.0 / 170.0
    timing = [TimingPoint(time=500.0, beat_length=beat), TimingPoint(time=500.0 + 48 * beat, beat_length=beat)]
    result = generate(audio, GeneratorOptions(target_stars=3.0, timing=timing))
    assert result.timing_source == "reference" and result.tempo is None
    assert abs(result.onset_shift_ms) <= 5.0  # the timing is the song's own: nothing to shift
    assert [tp.time for tp in result.beatmap.timing_points] == [500.0, 500.0 + 48 * beat]


def test_a_late_timing_is_read_through_the_shift(song):
    audio, attacks = song
    beat = 60000.0 / 170.0
    # Timing 30 ms early against the music, as a chart timed against a decoder with a different delay.
    result = generate(audio, GeneratorOptions(target_stars=2.0, timing=[TimingPoint(time=470.0, beat_length=beat)]))
    assert result.onset_shift_ms == pytest.approx(30.0, abs=4.0)
    assert result.stars == pytest.approx(2.0, abs=0.1)


# --- command line ---------------------------------------------------------------------------------------------


def test_cli_writes_osu_and_osz_and_borrows_a_reference(tmp_path, capsys):
    samples, _ = drum_song(bpm=170.0, offset_s=0.5, measures=16, seed=4)
    write_wav(tmp_path / "song.wav", samples)
    out = tmp_path / "out"
    assert main(["--audio", str(tmp_path / "song.wav"), "--target-dan", "0th", "--title", "T", "--artist", "A",
                 "--output-dir", str(out), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["timing_source"] == "auto" and report["bpm"] == pytest.approx(170.0, abs=0.05)
    osz = Path(report["osz_file"])
    with zipfile.ZipFile(osz) as z:
        names = sorted(z.namelist())
    assert names == sorted([Path(report["output_file"]).name, "song.wav"])
    chart = parse_osu_7k(Path(report["output_file"]).read_text(encoding="utf-8"))
    assert chart.audio_filename == "song.wav" and chart.version.startswith("Gen 0th")

    # The generated chart, beside its audio, is itself a reference: same timing, same title, a new difficulty.
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "song.wav").write_bytes((tmp_path / "song.wav").read_bytes())
    ref = chart
    ref.timing_points = [TimingPoint(time=512.0, beat_length=60000.0 / 170.0)]
    (ref_dir / "ref.osu").write_text(dump_osu_7k(ref), encoding="utf-8")
    assert main(["--timing-from", str(ref_dir / "ref.osu"), "--target-sr", "2.5", "--output-dir", str(out),
                 "--no-package", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["timing_source"] == "reference" and report["offset_ms"] == 512.0 and report["osz_file"] is None
    assert report["title"].startswith("A - T [Gen 2.50")

    assert main(["--target-dan", "3rd", "--output-dir", str(out)]) == 1
    assert "缺少音频" in capsys.readouterr().err


# --- evaluation against human charts --------------------------------------------------------------------------


def test_evaluation_scores_a_songs_folder(tmp_path, capsys):
    from proj7k.generator.evaluate import bpm_match, main as evaluate_main, row_match

    assert row_match([0.0, 100.0, 200.0], [0.0, 105.0, 400.0]) == (pytest.approx(2 / 3), pytest.approx(2 / 3))
    assert bpm_match(170.0, 170.3) == "exact" and bpm_match(85.0, 170.0) == "octave" and bpm_match(150.0, 170.0) == "wrong"

    samples, _ = drum_song(bpm=170.0, offset_s=0.5, measures=16, seed=6)
    folder = tmp_path / "Songs" / "1 A - T"
    folder.mkdir(parents=True)
    write_wav(folder / "song.wav", samples)
    # The "human" chart is a generated one at the same timing: the evaluation must find its own rows again.
    human = generate(Audio(samples, SR, "synth"), GeneratorOptions(target_stars=3.0, bpm=170.0, offset_ms=500.0)).beatmap
    human.audio_filename = "song.wav"
    (folder / "A - T [Human].osu").write_text(dump_osu_7k(human), encoding="utf-8")
    out = tmp_path / "eval.json"
    assert evaluate_main(["--songs", str(tmp_path / "Songs"), "--output", str(out)]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert summary["charts"] == 1 and summary["failed"] == 0
    assert summary["precision_median"] > 0.9 and summary["recall_median"] > 0.9
    assert summary["bpm_exact"] == 1.0 and summary["phase_within_10ms"] == 1.0
