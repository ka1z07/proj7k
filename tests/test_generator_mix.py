"""
Tests for the technique mix (ADR-0047 phase 3): parsing it, the generator keeping the run (and a section rewrite
the candidate) whose pools lean the way it asks, and the downscaler deleting the pools it weights down first.
"""

from pathlib import Path

import pytest

from proj7k.engine import evaluate_notes
from proj7k.generator import mix
from proj7k.generator import project as P
from proj7k.generator.cli import run_generate
from proj7k.generator.generate import GeneratorOptions, generate_from_file
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k, parse_osu_7k

BEAT = 60000.0 / 160
START, END = 1000.0, 61000.0


def _arg(extra, key):
    for e in extra:
        if e.startswith(key + "="):
            return e.split("=", 1)[1].strip("'")
    return None


def styled_runner(calls):
    """Even seeds write jacks (each row on the column before), odd seeds stairs; the same rows either way. With
    add_to_beatmap it rewrites only the asked stretch, as the real model does."""
    def run(audio, out, difficulty, seed, extra):
        calls.append((difficulty, seed, list(extra)))
        lo, hi, keep = START, END, []
        if _arg(extra, "add_to_beatmap") == "true":
            given = parse_osu_7k(Path(_arg(extra, "beatmap_path")).read_text(encoding="utf-8"))
            lo, hi = float(_arg(extra, "start_time")), float(_arg(extra, "end_time"))
            keep = [o for o in given.hit_objects if not lo <= o.time <= hi]
        objs, t, k = list(keep), START, 0
        while t < END:
            if lo <= t <= hi:
                col = (k // 4) % 7 if seed % 2 == 0 else k % 7
                objs.append(HitObject(column=col, time=round(t), note_type=NoteType.RICE))
            k += 1
            t += BEAT / 4
        bm = Beatmap7K(title="T", artist="A", creator="Mapperatorinator", version="M",
                       timing_points=[TimingPoint(time=START, beat_length=BEAT, uninherited=True)],
                       hit_objects=sorted(objs, key=lambda o: (o.time, o.column)), audio_filename="audio.mp3")
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"model{len(calls)}.osu"
        path.write_text(dump_osu_7k(bm), encoding="utf-8")
        return path
    return run


def jackiness(objs):
    notes = [(o.column, o.time / 1000.0, None) for o in objs]
    p = evaluate_notes(notes)
    return p.skills["rc_jack"].stars / p.total_stars


@pytest.fixture()
def song(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(b"not decoded")
    return path


def test_parse_reads_pairs_short_names_and_clamps():
    assert mix.parse(None) is None and mix.parse("") is None and mix.parse({}) is None
    assert mix.parse("rc_jack=1, ln_release=-1") == {"rc_jack": 1.0, "ln_release": -1.0}
    assert mix.parse("jack=3;stream=-0.5;tech=0") == {"rc_jack": 1.0, "rc_stamina": -0.5}
    assert mix.parse({"LN General": 1}) == {"ln_general": 1.0}
    with pytest.raises(ValueError):
        mix.parse("rice=1")
    with pytest.raises(ValueError):
        mix.parse("rc_jack")


def test_lean_is_the_weighted_share_of_the_pools():
    objs = [HitObject(column=0, time=1000 + 100 * i, note_type=NoteType.RICE) for i in range(200)]
    p = evaluate_notes([(o.column, o.time / 1000.0, None) for o in objs])
    share = p.skills["rc_jack"].stars / p.total_stars
    assert mix.lean(p, {"rc_jack": 1.0}) == pytest.approx(share)
    assert mix.lean(p, {"rc_jack": -0.5}) == pytest.approx(-0.5 * share)
    assert mix.lean(p, None) == 0.0


@pytest.mark.parametrize("weight", [1.0, -1.0])
def test_generation_keeps_the_run_that_fits_the_mix(song, weight):
    calls = []
    # jacks 4.15★, stairs 2.78★: both within the tolerance, so the mix decides
    options = GeneratorOptions(target_stars=3.5, tolerance=1.0, model_runs=4, model_runner=styled_runner(calls),
                               model_reuse=False, pool_mix={"rc_jack": weight})
    result = generate_from_file(song, options)
    assert len(calls) == 4   # a mix runs every run, not just the first near enough
    kept_seed = int(result.beatmap.tags.split("seed")[1].split()[0])
    assert (kept_seed % 2 == 0) == (weight > 0)


def test_without_a_mix_the_first_run_near_enough_is_kept(song):
    calls = []
    options = GeneratorOptions(target_stars=3.5, tolerance=1.0, model_runs=4, model_runner=styled_runner(calls),
                               model_reuse=False)
    generate_from_file(song, options)
    assert len(calls) == 1


def test_a_rewrite_picks_the_candidate_that_fits_the_mix(song, tmp_path):
    calls = []
    options = GeneratorOptions(target_stars=3.0, tolerance=1.0, model_runs=1, model_runner=styled_runner(calls),
                               model_reuse=False, title="T", artist="A", seed=1, pool_mix={"rc_jack": 1.0})
    run = run_generate(options, tmp_path / "out", audio=song)
    p = run.project
    assert P.Project.load(p.path).pool_mix == {"rc_jack": 1.0}
    s = p.sections[2]
    before = jackiness([o for o in p.chart().hit_objects if s.start_ms <= o.time < s.end_ms])
    # stairs (2.2★ here) are nearer 2.6★ than jacks (3.3★); the mix tips it to the jacks
    P.rewrite(p, [2], target_sr=2.6, candidates=2, seed=1, runner=styled_runner(calls))
    after = jackiness([o for o in p.chart().hit_objects if s.start_ms <= o.time < s.end_ms])
    assert after > before   # seed 2 (jacks) beat seed 1 (stairs, as generated)
    # an empty mix turns it off: the stairs of seed 1 come back
    P.rewrite(p, [2], target_sr=2.6, candidates=2, seed=1, pool_mix={}, runner=styled_runner(calls))
    assert jackiness([o for o in p.chart().hit_objects if s.start_ms <= o.time < s.end_ms]) == pytest.approx(before)


def test_the_downscaler_deletes_the_pools_weighted_down_first(tmp_path):
    from proj7k.downscaler.pipeline import DownscaleOptions, downscale_beatmap

    # Half the chart jacks, half stairs, so either can go.
    objs, t, k = [], START, 0
    while t < END:
        col = (k // 4) % 7 if (t // 6000) % 2 == 0 else k % 7
        objs.append(HitObject(column=col, time=round(t), note_type=NoteType.RICE))
        k += 1
        t += BEAT / 4
    chart = Beatmap7K(title="T", artist="A", creator="c", version="v",
                      timing_points=[TimingPoint(time=START, beat_length=BEAT, uninherited=True)], hit_objects=objs)
    stars = float(evaluate_notes([(o.column, o.time / 1000.0, None) for o in objs]).total_stars)
    jacks = {o.time for o in objs if (o.time // 6000) % 2 == 0}
    kept = {}
    for w in (1.0, -1.0):
        res = downscale_beatmap(chart, DownscaleOptions(target_dan=None, target_sr=stars * 0.75,
                                                        preserve_technique=False, skill_weights={"rc_jack": w}))
        left = res.downscaled_beatmap.hit_objects
        kept[w] = sum(o.time in jacks for o in left) / max(1, sum(o.time not in jacks for o in left))
    assert kept[1.0] > kept[-1.0]


def test_the_dashboard_hands_the_pages_mix_to_the_generator(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from proj7k.dashboard import actions
    from proj7k.generator import cli
    from proj7k.generator import mapperatorinator as mt

    seen = {}

    def run_generate(options, output_dir, **kw):
        seen["mix"] = options.pool_mix
        raise RuntimeError("stop")

    monkeypatch.setattr(cli, "run_generate", run_generate)
    monkeypatch.setattr(mt, "install", lambda log=None, home=None: None)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"")
    config = SimpleNamespace(realm_path=None, lazer_files_dir=None, generated_dir=tmp_path)
    for value, expected in (("rc_jack=1,ln_release=-1", {"rc_jack": 1.0, "ln_release": -1.0}), ("", None)):
        with pytest.raises(RuntimeError):
            actions.generate_chart(config, {"target_sr": "5", "audio": str(audio), "mix": value}, None)
        assert seen["mix"] == expected
