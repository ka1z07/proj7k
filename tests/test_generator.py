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
from proj7k.generator import patterns
from proj7k.generator.grid import DOWNBEAT, build_grid
from proj7k.generator.patterns import (MASKS_BY_SIZE, PatternModel, PatternSampler, default_model, mirror, pattern_stats,
                                       popcount)
from proj7k.generator.phrases import group_measures, harmonize
from proj7k.generator.onset import FRAME_S, frame_time
from proj7k.generator.tempo import CHART_SYNC_MS
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


@pytest.mark.parametrize("bpm, offset_s", [(128.0, 1.234), (138.12, 0.77), (170.0, 0.5), (200.0, 0.31)])
def test_tempo_and_beat_phase_are_found(bpm, offset_s):
    samples, _ = drum_song(bpm=bpm, offset_s=offset_s, measures=32, seed=int(bpm))
    est = estimate_tempo(onset_envelope(Audio(samples, SR, "synth")))
    assert est.bpm == pytest.approx(bpm, abs=0.05)
    beat_ms = 60000.0 / bpm
    # A chart is timed CHART_SYNC_MS before the envelope's beat (where the attack starts to be heard).
    phase_err = (est.offset_ms + CHART_SYNC_MS - offset_s * 1000.0 + beat_ms / 2) % beat_ms - beat_ms / 2
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


def test_grid_counts_beats_and_measures_across_sections(song):
    audio, _ = song
    env = onset_envelope(audio)
    beat = 60000.0 / 170.0
    timing = [TimingPoint(time=500.0, beat_length=beat), TimingPoint(time=500.0 + 30 * beat, beat_length=beat)]
    grid = build_grid(env, timing)
    for p in grid:
        assert p.slot == round((p.beat % 4) * 48)  # every measure starts on a multiple of 4 beats
        assert (p.level == DOWNBEAT) == (p.slot == 0)
    # The song starts on beat 3 of the measure before the first timing point; the first section's 30
    # beats end mid-measure (count 34), so the second section starts a new measure at count 36.
    assert grid[0].beat == 3 and grid[0].bar == 0
    second = next(i for i, p in enumerate(grid) if p.time_ms >= 500.0 + 30 * beat - 1e-6)
    assert grid[second].beat == 36 and grid[second].bar == grid[second - 1].bar + 1


def test_measures_that_sound_alike_get_one_rhythm(song):
    audio, _ = song
    env = onset_envelope(audio)
    grid = build_grid(env, [TimingPoint(time=500.0, beat_length=60000.0 / 170.0)])
    groups = group_measures(grid)
    # The drum song repeats one groove under a random melody: its measures fall into one group.
    assert np.mean([g >= 0 for p, g in zip(grid, groups) if p.time_ms > 600.0]) > 0.9
    # A place picked in most of a group's measures is picked in all of them; a place picked in only one
    # is dropped, unless the music is clearly louder there (a fill).
    sizes = [0] * len(grid)
    members = sorted({p.bar for p, g in zip(grid, groups) if g == groups[-1]})
    third = members[2]
    for i, p in enumerate(grid):
        if p.bar in members[1:] and p.slot == 48:
            sizes[i] = 2
        if p.bar == third and p.slot == 12:
            sizes[i] = 1
    out = harmonize(grid, groups, sizes)
    for i, p in enumerate(grid):
        if groups[i] == groups[-1] and p.slot == 48 and not p.silent:
            assert out[i] == 2
        if p.bar == third and p.slot == 12:
            assert out[i] == 0


# --- the pattern model ----------------------------------------------------------------------------------------


def test_the_shipped_pattern_model_is_mirror_symmetric_and_normalised():
    model = default_model()
    assert model.meta["charts"] > 1000
    for n in (1, 2, 3):
        keys = patterns.contexts(n, 3, 0b0000100, 3, 0b0000010)
        masks, p = model.distribution(keys, n)
        assert set(masks) == set(MASKS_BY_SIZE[n]) and p.sum() == pytest.approx(1.0)
        mkeys = patterns.contexts(n, 3, mirror(0b0000100), 3, mirror(0b0000010))
        mmasks, mp = model.distribution(mkeys, n)
        mirrored = dict(zip(mmasks, mp))
        assert np.allclose(p, [mirrored[mirror(m)] for m in masks])


def test_sampled_keys_read_like_charts_people_write():
    # A plain 1/4 stream at 170 BPM with a chord on every beat.
    beat = 60000.0 / 170.0
    rows = [(i * beat / 4, 2 if i % 4 == 0 else 1) for i in range(800)]
    beats = [i / 4 for i in range(800)]
    masks = PatternSampler(default_model()).sample(rows, np.random.default_rng(1), beats=beats)
    assert [popcount(m) for m in masks] == [n for _, n in rows]
    assert masks == PatternSampler(default_model()).sample(rows, np.random.default_rng(1), beats=beats)
    stats = pattern_stats([(t, m) for (t, _), m in zip(rows, masks)], beat)
    # Ranked charts: about a fifth of three single notes are stairs and a tenth are trills; the old
    # greedy assignment made 2-3 % stairs and about 1 % trills.
    assert stats["roll"] > 0.10 and stats["trill"] > 0.04
    assert stats["key_repeat"] > 0.05
    counts = collections.Counter(c for m in masks for c in range(7) if m >> c & 1)
    assert min(counts.values()) > 0.5 * max(counts.values())


def test_a_repeated_rhythm_can_repeat_its_keys(monkeypatch):
    monkeypatch.setattr(patterns, "REPEAT_SPANS", ((4, 0.0, 1.0), (2, 0.0, 0.0), (1, 0.0, 0.0)))
    rows = [(i * 100.0, 1) for i in range(32)]
    masks = PatternSampler(default_model()).sample(rows, np.random.default_rng(0), beats=[i / 2 for i in range(32)])
    # Every 4-beat stretch (8 rows) after the first mirrors the one before it.
    for k in range(1, 4):
        assert masks[8 * k:8 * k + 8] == [mirror(m) for m in masks[8 * k - 8:8 * k]]


def test_a_model_trains_counts_and_round_trips(tmp_path):
    model = PatternModel()
    model.add_chart([(i * 100.0, 1 << (i % 7)) for i in range(50)])
    assert sum(model.levels[3]["1"].values()) == 100  # every row, as written and mirrored
    path = tmp_path / "m.json.gz"
    model.save(path, meta={"charts": 1})
    again = PatternModel.load(path)
    assert again.levels == model.levels and again.meta == {"charts": 1}
    rows = [(i * 100.0, 1 << (i % 7)) for i in range(20)]
    assert again.log_prob(rows) == pytest.approx(model.log_prob(rows))
    # Trained on rolls: the next roll step is by far the likeliest.
    masks, p = model.distribution(patterns.contexts(1, patterns.gap_bucket(100), 1 << 3, patterns.gap_bucket(100), 1 << 2), 1)
    assert masks[int(np.argmax(p))] == 1 << 4


# --- the closed loop ------------------------------------------------------------------------------------------


# Alike measures share their rhythm draws (ADR-0032): a measure whose loop is a little different from its
# cluster's first measure can take a row where only that one has the attack, so a few rows miss.
@pytest.mark.parametrize("target, on_attack", [(2.0, 0.88), (4.42, 0.75)])
def test_generated_chart_lands_on_the_target(song, target, on_attack):
    audio, attacks = song
    result = generate(audio, GeneratorOptions(target_stars=target))
    assert result.stars == pytest.approx(target, abs=0.1)
    assert not result.warnings
    assert result.timing_source == "auto" and result.tempo.bpm == pytest.approx(170.0, abs=0.05)
    # The star the result reports is the engine's reading of the chart it wrote.
    assert evaluate_osu(dump_osu_7k(result.beatmap)).total_stars == pytest.approx(result.stars, abs=1e-9)
    # Notes sit on the song's attacks; a busy target also fills some quiet grid points (a stream).
    attack_ms = np.array(attacks) * 1000.0 - CHART_SYNC_MS
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
    ref.extra_sections["Metadata"] = {"Title": "T", "Artist": "A", "BeatmapID": "123", "BeatmapSetID": "456"}
    (ref_dir / "ref.osu").write_text(dump_osu_7k(ref), encoding="utf-8")
    assert main(["--timing-from", str(ref_dir / "ref.osu"), "--target-sr", "2.5", "--output-dir", str(out),
                 "--no-package", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["timing_source"] == "reference" and report["offset_ms"] == 512.0 and report["osz_file"] is None
    assert report["title"].startswith("A - T [Gen 2.50")
    # A local set of its own: keeping the reference's set ID would make osu!lazer delete the original on import.
    meta = parse_osu_7k(Path(report["output_file"]).read_text(encoding="utf-8")).extra_sections["Metadata"]
    assert meta["BeatmapID"] == "0" and meta["BeatmapSetID"] == "-1"

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
    human = generate(Audio(samples, SR, "synth"), GeneratorOptions(target_stars=3.0, bpm=170.0, offset_ms=500.0 - CHART_SYNC_MS)).beatmap
    human.audio_filename = "song.wav"
    (folder / "A - T [Human].osu").write_text(dump_osu_7k(human), encoding="utf-8")
    out = tmp_path / "eval.json"
    assert evaluate_main(["--songs", str(tmp_path / "Songs"), "--output", str(out)]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert summary["charts"] == 1 and summary["failed"] == 0
    assert summary["precision_median"] > 0.9 and summary["recall_median"] > 0.9
    assert summary["bpm_exact"] == 1.0 and summary["phase_within_10ms"] == 1.0


# --- song structure (ADR-0031) --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def verse_chorus():
    """16 measures of a quiet verse (kick and snare, soft) and 16 of a loud chorus (the full groove)."""
    bpm = 170.0
    verse, _ = drum_song(bpm=bpm, offset_s=0.5, measures=16, seed=5, melody=False)
    chorus, _ = drum_song(bpm=bpm, offset_s=0.0, measures=16, seed=6)
    beat_s = 60.0 / bpm
    cut = int(round((0.5 + 16 * 4 * beat_s) * SR))
    song = np.concatenate([0.25 * verse[:cut], chorus]).astype(np.float32)
    return Audio(song, SR, "synth"), 500.0, 60000.0 / bpm


def test_sections_follow_the_song_and_the_chorus_is_louder(verse_chorus):
    from proj7k.generator.structure import find_sections

    audio, offset, beat = verse_chorus
    timing = [TimingPoint(time=offset, beat_length=beat)]
    env = onset_envelope(audio)
    grid = build_grid(env, timing)
    sections = find_sections(env, grid)
    starts = [s.first_bar for s in sections]
    assert any(abs(b - 16) <= 1 for b in starts)  # a boundary where the chorus comes in
    verse = [s for s in sections if s.last_bar <= 16]
    chorus = [s for s in sections if s.first_bar >= 15]
    assert verse and chorus
    assert min(s.energy for s in chorus) > max(s.energy for s in verse)


def test_the_loud_part_gets_more_notes_per_beat(verse_chorus):
    audio, offset, beat = verse_chorus
    result = generate(audio, GeneratorOptions(target_stars=3.0, timing=[TimingPoint(time=offset, beat_length=beat)]))
    split = offset + 16 * 4 * beat
    rows = sorted(_rows(result.beatmap))
    verse = sum(1 for t in rows if offset <= t < split)
    chorus = sum(1 for t in rows if split <= t < split + 16 * 4 * beat)
    assert chorus > 1.3 * verse


def test_each_part_gets_one_style_and_neighbours_differ():
    from proj7k.generator.structure import Section, section_styles

    grid = [type("P", (), {"bar": b})() for b in range(24)]
    sections = [Section(0, 3, 0, 0.2), Section(4, 11, 1, 0.9), Section(12, 15, 0, 0.2), Section(16, 23, 1, 0.9)]
    styles = section_styles(grid, sections, seed=4)
    assert len({styles[b] for b in range(0, 4)} | {styles[b] for b in range(12, 16)}) == 1
    assert styles[0] != styles[4] and styles[4] == styles[16]
    assert styles[0] in patterns.STYLES and styles[4] in patterns.STYLES


def test_a_style_leans_the_keys():
    rows = [(i * 60000.0 / 180 / 2, 2 if i % 2 == 0 else 1) for i in range(600)]

    def jack_rate(style):
        m = PatternSampler(default_model()).sample(rows, np.random.default_rng(1), styles=[style] * len(rows))
        return np.mean([bool(a & b) for a, b in zip(m, m[1:])])

    assert jack_rate("jack") > 3 * jack_rate("plain")


# --- rhythm model (ADR-0032) ------------------------------------------------------------------------------------


def test_rhythm_places_cover_every_beat_at_twelve_places_and_rows_label_them():
    from proj7k.generator import rhythm as R

    timing = [TimingPoint(time=1000.0, beat_length=500.0), TimingPoint(time=5000.0, beat_length=400.0)]
    pl = R.places(timing, 7000.0)
    first = pl.time_ms < 5000.0
    assert int(first.sum()) == 10 * len(R.SLOTS)          # 0 .. 5000 ms at 500 ms per beat
    assert int((~first).sum()) == 5 * len(R.SLOTS)        # 5000 .. 7000 ms at 400 ms per beat
    assert np.all(np.diff(pl.time_ms) > 0)
    y, missed = R.labels(pl, [1000.0, 1125.0, 1166.0, 5200.0, 1031.0], [1, 2, 5, 1, 1])
    assert missed == 1                                    # 1031 ms is a 1/16, not a place
    assert sorted(y[y > 0].tolist()) == [1, 1, 2, 4]


def test_rhythm_pick_matches_the_asked_density_and_one_grid_per_beat():
    from proj7k.generator import rhythm as R

    pl = R.places([TimingPoint(time=0.0, beat_length=400.0)], 60000.0)
    rng = np.random.default_rng(0)
    probs = rng.dirichlet(np.ones(R.CLASSES), size=len(pl))
    sizes = R.pick(probs, pl, rng.random(len(pl)), rng.random(len(pl)), np.zeros(len(pl), bool), rows_per_s=6.0)
    assert abs((sizes > 0).sum() / 60.0 - 6.0) < 0.6
    slots = np.array(R.SLOTS)[pl.slot]
    on_8th = np.isin(slots, (6, 12, 18, 30, 36, 42)) & (sizes > 0)
    on_6th = np.isin(slots, (8, 16, 32, 40)) & (sizes > 0)
    assert not (set(pl.beat[on_8th]) & set(pl.beat[on_6th]))
