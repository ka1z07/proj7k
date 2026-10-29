"""
Tests for generation projects (ADR-0047 phase 1): sections cut from the timing, sections written again by the model
(notes outside untouched), locks, anchors (same and mirrored, following their source), undo and redo, with a
stand-in for Mapperatorinator that honours `add_to_beatmap` / `start_time` / `end_time`.
"""

import zipfile
from pathlib import Path

import pytest

from proj7k.generator import project as P
from proj7k.generator.cli import run_generate
from proj7k.generator.generate import GeneratorOptions
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k, parse_osu_7k

BEAT = 60000.0 / 160          # a measure is 1500 ms, a section of 8 measures 12 s
START, END = 1000.0, 61000.0


def _arg(extra, key):
    for e in extra:
        if e.startswith(key + "="):
            return e.split("=", 1)[1].strip("'")
    return None


def model(calls):
    """Rows every 1/round(difficulty) beat; each seed shifts the columns. With add_to_beatmap it rewrites only the
    asked stretch of the chart it was given, as the real model does."""
    def run(audio, out, difficulty, seed, extra):
        calls.append((difficulty, seed, list(extra)))
        step = BEAT / max(1, round(difficulty))
        lo, hi = START, END
        keep = []
        timing = [TimingPoint(time=START, beat_length=BEAT, uninherited=True)]
        if _arg(extra, "add_to_beatmap") == "true":
            given = parse_osu_7k(Path(_arg(extra, "beatmap_path")).read_text(encoding="utf-8"))
            lo, hi = float(_arg(extra, "start_time")), float(_arg(extra, "end_time"))
            keep = [o for o in given.hit_objects if not lo <= o.time <= hi]
            timing = given.timing_points
        objs, t, k = list(keep), START, 0
        while t < END:
            if lo <= t <= hi:
                objs.append(HitObject(column=(3 * k + seed) % 7, time=round(t), note_type=NoteType.RICE))
            k += 1
            t += step
        bm = Beatmap7K(title="T", artist="A", creator="Mapperatorinator", version="M", timing_points=timing,
                       hit_objects=sorted(objs, key=lambda o: (o.time, o.column)), audio_filename="audio.mp3")
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"model{len(calls)}.osu"
        path.write_text(dump_osu_7k(bm), encoding="utf-8")
        return path
    return run


@pytest.fixture()
def project(tmp_path):
    song = tmp_path / "song.mp3"
    song.write_bytes(b"not decoded")
    calls = []
    options = GeneratorOptions(target_stars=1.0, tolerance=50.0, model_runs=1, model_runner=model(calls),
                               model_reuse=False, title="T", artist="A")
    run = run_generate(options, tmp_path / "out", audio=song)
    return run, calls


def notes_in(chart, s):
    return sorted((o.time, o.column) for o in chart.hit_objects if s.start_ms <= o.time < s.end_ms)


def test_generation_writes_a_project_with_sections_of_eight_bars(project):
    run, _ = project
    p = run.project
    assert p.path.name.endswith(P.SUFFIX) and p.path.is_file()
    loaded = P.Project.load(p.path)
    assert len(loaded.takes) == 1 and loaded.takes[0].note == "generate" and loaded.takes[0].osz
    secs = loaded.sections
    assert secs[0].start_ms <= START and all(b.start_ms == a.end_ms for a, b in zip(secs, secs[1:]))
    assert all(abs((s.end_ms - s.start_ms) - 8 * 4 * BEAT) < 1e-6 for s in secs[:-1])
    assert secs[-1].end_ms > max(o.time for o in run.result.beatmap.hit_objects)
    d = run.to_dict()["project"]
    assert len(d["sections"]) == len(secs) and d["sections"][1]["notes"] > 0 and not d["can_undo"]


def test_bars_follow_every_timing_point():
    timing = [TimingPoint(time=500.0, beat_length=500.0, uninherited=True),
              TimingPoint(time=4500.0, beat_length=250.0, meter=3, uninherited=True)]
    assert P.bar_starts(timing, 7000.0) == [500.0, 2500.0, 4500.0, 5250.0, 6000.0, 6750.0]
    beats = P.Beats(timing)
    assert beats.beat(4500.0) == 8.0 and beats.beat(5000.0) == 10.0 and beats.time(10.0) == 5000.0


def test_rewriting_a_section_leaves_the_rest_alone(project):
    run, calls = project
    p = run.project
    before = p.chart()
    calls.clear()
    P.rewrite(p, [1], target_sr=None, candidates=2, runner=model(calls))
    after = p.chart()
    assert len(p.takes) == 2 and p.current == 1 and after.version.endswith(" v2")
    assert all("add_to_beatmap=true" in c[2] for c in calls) and len(calls) == 2
    for i, s in enumerate(p.sections):
        if i != 1:
            assert notes_in(after, s) == notes_in(before, s)
    assert notes_in(after, p.sections[1]) != notes_in(before, p.sections[1])
    assert (p.folder / p.takes[1].osz).is_file() and "BeatmapSetID:-1" in (p.folder / p.takes[1].osu).read_text("utf-8")


def test_a_rewrite_aims_at_the_sections_target(project):
    run, calls = project
    p = run.project
    calls.clear()
    P.rewrite(p, [2], target_sr=4.0, candidates=3, runner=model(calls))
    assert calls[0][0] == pytest.approx(4.0 * 1.15)
    # Each candidate's difficulty is steered by the last one's stars, toward the target.
    assert len({round(c[0], 2) for c in calls}) > 1


def test_locked_and_anchored_sections_refuse_a_rewrite(project):
    run, calls = project
    p = run.project
    P.lock(p, [0])
    with pytest.raises(ValueError, match="锁定|locked"):
        P.rewrite(p, [0], runner=model(calls))
    with pytest.raises(ValueError):
        P.rewrite(p, [1, 3], runner=model(calls))
    assert P.Project.load(p.path).sections[0].locked


def test_an_anchored_section_is_charted_as_its_source_and_follows_it(project):
    run, calls = project
    p = run.project
    P.anchor(p, [3], 1, mirror=True)
    chart = p.chart()
    src, dst = p.sections[1], p.sections[3]
    shift = dst.start_ms - src.start_ms
    assert notes_in(chart, dst) == sorted((t + shift, 6 - c) for t, c in notes_in(chart, src))
    P.rewrite(p, [1], candidates=1, seed=5, runner=model(calls))
    chart = p.chart()
    assert notes_in(chart, dst) == sorted((t + shift, 6 - c) for t, c in notes_in(chart, src))
    with pytest.raises(ValueError):
        P.anchor(p, [1], 3)   # its source can't be anchored onto the section tied to it
    P.anchor(p, [3], None)
    assert p.sections[3].anchor is None


def test_undo_and_redo_move_between_takes(project):
    run, calls = project
    p = run.project
    P.rewrite(p, [1], candidates=1, runner=model(calls))
    first = notes_in(p.chart(0), p.sections[1])
    P.move(p, -1)
    assert p.current == 0 and notes_in(p.chart(), p.sections[1]) == first
    P.move(p, -1)
    assert p.current == 0
    P.move(p, 1)
    assert p.current == 1 and P.to_dict(p)["can_undo"] and not P.to_dict(p)["can_redo"]


def test_the_song_comes_back_out_of_the_osz_when_its_file_is_gone(project, tmp_path):
    run, _ = project
    p = run.project
    Path(p.audio).unlink()
    audio = p.audio_path(tmp_path / "work")
    assert audio.read_bytes() == b"not decoded"
    with zipfile.ZipFile(p.folder / p.takes[0].osz) as z:
        assert p.audio_filename in z.namelist()


def test_command_line(project, capsys):
    run, _ = project
    path = str(run.project.path)
    assert P.main([path, "lock", "1", "2"]) == 0
    assert P.main([path, "anchor", "4", "--to", "2"]) == 0
    out = capsys.readouterr().out
    assert "2/2" in out
    assert P.main([path, "undo"]) == 0 and P.main([path, "show"]) == 0
    assert P.main([path, "rewrite", "1"]) == 1
    assert "锁定" in capsys.readouterr().err
