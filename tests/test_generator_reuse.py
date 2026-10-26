"""
Tests for phrase reuse (ADR-0045): a song made of two phrases that alternate charts every repeat of a
phrase with the notes of its first pass.
"""

import numpy as np
import pytest

from generator_synth import SR, drum_song, write_wav
from proj7k.generator import legal, reuse
from proj7k.parser import HitObject, NoteType, TimingPoint

BPM = 150.0
BEAT_MS = 60000.0 / BPM


def phrase(seed):
    samples, _ = drum_song(bpm=BPM, offset_s=0.0, measures=reuse.PHRASE_BARS, seed=seed)
    return samples[: int(round(reuse.PHRASE_BARS * 4 * BEAT_MS / 1000 * SR))]


@pytest.fixture()
def song(tmp_path):
    a, b = phrase(1), phrase(7)
    path = tmp_path / "song.wav"
    write_wav(path, np.concatenate([a, b, a, b, a, np.zeros(SR)]))
    return path


def random_chart(seed, end_ms):
    rng = np.random.default_rng(seed)
    objs, t = [], 0.0
    while t < end_ms:
        for c in rng.choice(7, size=int(rng.integers(1, 4)), replace=False):
            if rng.random() < 0.2:
                objs.append(HitObject(column=int(c), time=round(t), note_type=NoteType.LN, end_time=round(t + BEAT_MS)))
            else:
                objs.append(HitObject(column=int(c), time=round(t), note_type=NoteType.RICE))
        t += BEAT_MS / 2 * int(rng.integers(1, 3))
    return legal.sanitize(objs, [TimingPoint(time=0.0, beat_length=BEAT_MS, uninherited=True)])[0]


def rows(objs, start, end):
    return sorted((round(o.time - start), o.column, None if o.end_time is None else round(o.end_time - start))
                  for o in objs if start <= o.time < end)


def test_each_repeat_is_charted_as_the_first_pass(song):
    timing = [TimingPoint(time=0.0, beat_length=BEAT_MS, uninherited=True)]
    phrase_ms = reuse.PHRASE_BARS * 4 * BEAT_MS
    chart = random_chart(0, 5 * phrase_ms)
    out, copied, total = reuse.reuse_phrases(chart, timing, song)
    assert copied == 3 and total >= 5
    for k, src in ((2, 0), (3, 1), (4, 0)):
        assert rows(out, k * phrase_ms, (k + 1) * phrase_ms) == rows(out, src * phrase_ms, (src + 1) * phrase_ms)
    # The first passes keep the model's notes.
    assert rows(out, 0, 2 * phrase_ms) == rows(chart, 0, 2 * phrase_ms)


def test_a_song_with_no_repeats_is_left_alone(tmp_path):
    path = tmp_path / "song.wav"
    write_wav(path, np.concatenate([phrase(s) for s in (1, 2, 3, 4)] + [np.zeros(SR)]))
    timing = [TimingPoint(time=0.0, beat_length=BEAT_MS, uninherited=True)]
    chart = random_chart(1, 4 * reuse.PHRASE_BARS * 4 * BEAT_MS)
    out, copied, _ = reuse.reuse_phrases(chart, timing, path)
    assert copied == 0
    assert rows(out, 0, 1e9) == rows(chart, 0, 1e9)


def test_the_mapperatorinator_backend_reuses_phrases(song, tmp_path):
    from proj7k.generator.generate import GeneratorOptions, generate_from_file
    from proj7k.parser import Beatmap7K, dump_osu_7k

    def model(audio, out, difficulty, seed, extra):
        bm = Beatmap7K(title="T", artist="A", creator="M", version="M", hit_objects=random_chart(seed, 5 * 8 * BEAT_MS),
                       timing_points=[TimingPoint(time=0.0, beat_length=BEAT_MS, uninherited=True)])
        out.mkdir(parents=True, exist_ok=True)
        path = out / "model.osu"
        path.write_text(dump_osu_7k(bm), encoding="utf-8")
        return path

    logs = []
    options = GeneratorOptions(target_stars=1.0, backend="mapperatorinator", model_runner=model,
                               model_runs=1, tolerance=50.0, log=logs.append)
    generate_from_file(song, options)
    assert any("乐句照前面" in m for m in logs)
    logs.clear()
    options.model_reuse = False
    generate_from_file(song, options)
    assert not any("乐句" in m for m in logs)
