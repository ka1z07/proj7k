"""
Tests for the generator's audio reading (ADR-0027), which phrase reuse (ADR-0045) stands on: decoding, the
onset envelope and the beat grid, on synthetic songs whose every attack time is known (`generator_synth`).
The model's own loop is in `test_generator_mapperatorinator`.
"""

import numpy as np
import pytest

from generator_synth import SR, drum_song, write_wav
from proj7k.generator import Audio, AudioDecodeError, load_audio, onset_envelope
from proj7k.generator.grid import DOWNBEAT, build_grid
from proj7k.generator.onset import FRAME_S, frame_time
from proj7k.parser import TimingPoint


@pytest.fixture(scope="module")
def song():
    samples, attacks = drum_song(bpm=170.0, offset_s=0.5, measures=24, seed=3)
    return Audio(samples, SR, "synth"), attacks


# --- audio ----------------------------------------------------------------------------------------------------


def test_wav_decodes_and_resamples_to_the_analysis_rate(tmp_path):
    samples, _ = drum_song(bpm=150.0, measures=2, sr=44100)
    write_wav(tmp_path / "a.wav", samples, sr=44100)
    audio = load_audio(tmp_path / "a.wav")
    assert audio.sample_rate == SR and audio.source == "wave"
    assert abs(audio.duration_s - len(samples) / 44100) < 0.01
    assert np.abs(audio.samples).max() <= 1.0


def test_miniaudio_reads_a_file_whose_name_is_not_ascii(tmp_path):
    pytest.importorskip("miniaudio")
    samples, _ = drum_song(bpm=150.0, measures=2, sr=44100)
    write_wav(tmp_path / "a.wav", samples, sr=44100)
    named = tmp_path / "tricot - おとずれ.mp3"  # not a .wav suffix, so the wave reader is skipped
    (tmp_path / "a.wav").rename(named)
    audio = load_audio(named)
    assert audio.source == "miniaudio"
    assert abs(audio.duration_s - len(samples) / 44100) < 0.01


def test_a_missing_or_unreadable_file_is_reported(tmp_path):
    with pytest.raises(AudioDecodeError):
        load_audio(tmp_path / "nope.mp3")
    (tmp_path / "junk.wav").write_bytes(b"not audio at all")
    with pytest.raises(AudioDecodeError):
        load_audio(tmp_path / "junk.wav")


# --- onset ------------------------------------------------------------------------------------------


def test_onset_peaks_land_on_the_attacks(song):
    audio, attacks = song
    env = onset_envelope(audio)
    errors = [frame_time(env.peak(a, 0.04)) - a for a in attacks]
    # Within one 10 ms frame of the attack, after the measured lag is taken out.
    assert abs(np.median(errors)) <= FRAME_S / 2
    assert np.percentile(np.abs(errors), 90) <= 1.5 * FRAME_S


# --- grid -----------------------------------------------------------------------------------------


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
