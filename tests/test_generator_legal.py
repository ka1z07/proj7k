"""
Tests for legal.py: every chart the generator hands out is playable (no duplicate notes, no note inside a
hold, seven columns, holds with a length), whichever backend wrote it.
"""

from proj7k.generator import legal
from proj7k.generator.generate import GeneratorOptions, generate_from_file
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k, parse_osu_7k

BEAT = 400.0
TIMING = [TimingPoint(time=0.0, beat_length=BEAT, uninherited=True)]


def tap(col, t):
    return HitObject(column=col, time=t, note_type=NoteType.RICE)


def hold(col, t, end):
    return HitObject(column=col, time=t, note_type=NoteType.LN, end_time=end)


def kinds(objs):
    return sorted(p.kind for p in legal.problems(objs))


def test_problems_names_each_way_a_chart_cant_be_played():
    objs = [tap(0, 1000), tap(0, 1000.2),            # one note twice (as written: both 1000 ms)
            hold(1, 1000, 2000), tap(1, 1500),        # a note inside a hold
            hold(2, 1000, 3000), hold(2, 3000, 3400),  # a head on the tail
            hold(3, 1000, 1000),                      # a hold with no length
            tap(4, 1000), tap(4, 1001)]               # legal: one millisecond apart
    assert kinds(objs) == ["duplicate", "empty_hold", "inside_hold", "inside_hold"]
    assert legal.problems([tap(0, 0), hold(0, 100, 900), tap(0, 1000)]) == []


def test_sanitize_leaves_a_legal_chart_alone():
    objs = [tap(0, 0), hold(0, 100, 900), tap(0, 1000), hold(3, 0, 5000), tap(6, 10)]
    out, changed = legal.sanitize(objs, TIMING)
    assert changed == 0
    assert sorted((o.column, o.time, o.end_time) for o in out) == sorted((o.column, o.time, o.end_time) for o in objs)
    assert [(o.time, o.column) for o in out] == sorted((o.time, o.column) for o in out)


def test_duplicates_keep_the_longest_note():
    out, changed = legal.sanitize([tap(2, 1000), hold(2, 1000.3, 1800), tap(2, 999.8)], TIMING)
    assert changed == 2 and len(out) == 1
    assert out[0].note_type == NoteType.LN and (out[0].time, out[0].end_time) == (1000, 1800)


def test_a_note_under_a_hold_cuts_the_hold_before_it():
    out, changed = legal.sanitize([hold(1, 1000, 3000), tap(1, 2000), tap(1, 2600)], TIMING)
    assert changed == 1
    assert [(o.note_type, o.time, o.end_time) for o in out] == [
        (NoteType.LN, 1000, 2000 - legal.MIN_GAP * BEAT), (NoteType.RICE, 2000, None), (NoteType.RICE, 2600, None)]
    assert legal.problems(out) == []


def test_a_hold_cut_too_short_becomes_a_tap():
    out, changed = legal.sanitize([hold(1, 1000, 3000), tap(1, 1060)], TIMING)
    assert changed == 1
    assert [(o.note_type, o.time) for o in out] == [(NoteType.RICE, 1000), (NoteType.RICE, 1060)]


def test_a_hold_with_no_length_and_a_column_off_the_keys():
    out, changed = legal.sanitize([hold(0, 500, 500.2), tap(7, 500), tap(-1, 600)], TIMING)
    assert changed == 3
    assert [(o.column, o.note_type) for o in out] == [(0, NoteType.RICE)]


def test_a_hold_inside_a_hold_and_a_chain_of_overlaps():
    objs = [hold(5, 0, 4000), hold(5, 1000, 3000), tap(5, 2000), tap(5, 2000), hold(5, 3500, 5000)]
    out, _ = legal.sanitize(objs, TIMING)
    assert legal.problems(out) == []
    assert len(out) == 4


def test_the_written_file_has_no_overlaps_left():
    # Two heads 0.4 ms apart become one millisecond in the file: that is where the duplicate is judged.
    out, _ = legal.sanitize([tap(0, 100.4), tap(0, 99.6), hold(3, 200, 900), tap(3, 899.6)], TIMING)
    written = parse_osu_7k(dump_osu_7k(Beatmap7K(timing_points=TIMING, hit_objects=out)))
    assert legal.problems(written.hit_objects) == []


def test_mapperatorinator_overlaps_never_reach_the_chart(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"not decoded")

    def overlapping(audio, out, difficulty, seed, extra):
        objs = []
        for k in range(120):
            t = 1000.0 + k * BEAT / 2
            objs.append(tap(k % 7, t))
            if k % 5 == 0:
                objs.append(tap(k % 7, t))                          # window seam: the same note twice
            if k % 7 == 3:
                objs.append(hold((k + 1) % 7, t, t + 4 * BEAT))     # a hold the next notes land in
        bm = Beatmap7K(creator="Mapperatorinator", version="M", timing_points=TIMING, hit_objects=objs)
        assert legal.problems(objs)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "beatmap.osu"
        path.write_text(dump_osu_7k(bm), encoding="utf-8")
        return path

    logs = []
    options = GeneratorOptions(target_stars=1.0, backend="mapperatorinator", model_runner=overlapping, model_runs=1,
                               log=logs.append)
    result = generate_from_file(song, options)
    assert legal.problems(result.beatmap.hit_objects) == []
    assert any("重叠" in m or "overlapping" in m for m in logs)


def test_proj7k_charts_with_long_notes_are_legal_as_generated():
    from generator_synth import SR, drum_song
    from proj7k.generator.audio import Audio
    from proj7k.generator.generate import generate

    samples, _ = drum_song(bpm=170.0, measures=12, seed=5)
    for inverse in (False, True):
        result = generate(Audio(samples, SR, "synth"),
                          GeneratorOptions(target_stars=6.0, ln_ratio=0.4, whole_inverse=inverse, seed=5))
        assert any(o.note_type == NoteType.LN for o in result.beatmap.hit_objects)
        assert legal.problems(result.beatmap.hit_objects) == []
