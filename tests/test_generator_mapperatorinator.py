"""
Tests for the Mapperatorinator backend (ADR-0040): the overrides handed to its inference.py, the
requirements it installs, and the loop that brings its runs to the target star rating, with a stand-in
for the model that writes a stream whose density follows the difficulty it is asked for.
"""

import os
import zipfile
from pathlib import Path

import pytest

from proj7k.engine import evaluate_osu
from proj7k.generator import mapperatorinator as mt
from proj7k.generator.cli import main
from proj7k.generator.generate import GeneratorOptions, generate_from_file
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k, parse_osu_7k


def stream_runner(calls):
    """Stands in for the model: a 1/4 stream at 160 BPM whose rows per beat grow with the difficulty."""
    def run(audio, out, difficulty, seed, extra):
        calls.append((difficulty, seed, list(extra)))
        beat = 60000.0 / 160
        step = beat / max(1, round(difficulty))
        objs, t, col = [], 1000.0, 0
        while t < 61000.0:
            objs.append(HitObject(column=col % 7, time=round(t), note_type=NoteType.RICE))
            col += 3
            t += step
        bm = Beatmap7K(title="Model Title", artist="Model Artist", creator="Mapperatorinator", version="Mapperatorinator",
                       timing_points=[TimingPoint(time=1000.0, beat_length=beat, uninherited=True)], hit_objects=objs,
                       audio_filename="audio.mp3")
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"beatmap{len(calls)}.osu"
        path.write_text(dump_osu_7k(bm), encoding="utf-8")
        return path
    return run


@pytest.fixture()
def song(tmp_path):
    path = tmp_path / "song.mp3"
    path.write_bytes(b"not decoded: the stand-in never reads it")
    return path


def test_overrides_quote_paths_and_ask_for_7k(tmp_path):
    audio = tmp_path / "a b's (c) [d]=e.mp3"
    out = mt.overrides(audio, tmp_path / "out", 5.237, 3, hold_note_ratio=1.4, title="T")
    assert "gamemode=3" in out and "keycount=7" in out and "difficulty=5.24" in out and "seed=3" in out
    assert "hold_note_ratio=1.00" in out and "title='T'" in out
    audio_arg = next(o for o in out if o.startswith("audio_path="))
    assert audio_arg.startswith("audio_path='") and audio_arg.endswith("'") and "\\'s" in audio_arg
    assert not any(o.startswith("hold_note_ratio") for o in mt.overrides(audio, tmp_path, 5, 0))
    assert "output_type=[TIMING,MAP]" in out and "parallel=true" not in out
    borrowed = mt.overrides(audio, tmp_path, 5, 0, timing_from=tmp_path / "ref.osu", fast=True)
    assert "in_context=[TIMING]" in borrowed and "output_type=[MAP]" in borrowed
    assert f"parallel_chains={mt.FAST_CHAINS}" in borrowed and "parallel=true" not in borrowed
    assert any(o.startswith("beatmap_path='") for o in borrowed)


def test_requirements_leave_out_the_desktop_ui():
    text = "accelerate==1.12.0\n# comment\npywebview[qt]==6.1\npyqtwebengine==5.15.7\nslider @ git+https://x/slider.git\n" \
           "audioop-lts; python_version>='3.13'\n"
    assert mt.requirements(text) == ["accelerate==1.12.0", "slider @ git+https://x/slider.git",
                                     "audioop-lts; python_version>='3.13'"]


def test_next_difficulty_is_bounded():
    assert mt.next_difficulty(4.0, 2.0, 8.0) == pytest.approx(4.0 * mt.MAX_STEP)
    assert mt.next_difficulty(4.0, 8.0, 7.0) == pytest.approx(3.5)
    assert mt.next_difficulty(11.0, 1.0, 9.0) == mt.MAX_DIFFICULTY
    assert mt.next_difficulty(0.6, 9.0, 0.5) == mt.MIN_DIFFICULTY
    assert mt.next_difficulty(3.0, 0.0, 5.0) == pytest.approx(3.0 * mt.MAX_STEP)


def test_runs_until_the_engine_stars_reach_the_target(song, tmp_path):
    calls = []
    runner = stream_runner(calls)
    target = float(evaluate_osu(runner(song, tmp_path / "probe", 4.0, 0, [])
                                .read_text(encoding="utf-8")).total_stars)
    calls.clear()
    options = GeneratorOptions(target_stars=target, target_label="probe", seed=7, backend="mapperatorinator",
                               model_runner=runner, model_runs=5)
    result = generate_from_file(song, options)
    assert abs(result.stars - target) <= options.tolerance
    assert result.timing_source == "model" and result.iterations == len(calls) >= 1
    assert [c[1] for c in calls] == list(range(7, 7 + len(calls)))
    bm = result.beatmap
    assert bm.title == "Model Title" and bm.artist == "Model Artist" and bm.audio_filename == "song.mp3"
    assert bm.version.startswith("Mapperatorinator probe") and "mapperatorinator-v32" in bm.tags
    assert "BeatmapSetID:-1" in dump_osu_7k(bm)


def test_keeps_the_closest_run_and_warns_when_out_of_reach(song):
    calls = []
    options = GeneratorOptions(target_stars=40.0, backend="mapperatorinator", model_runner=stream_runner(calls),
                               model_runs=3, timing=[TimingPoint(time=0.0, beat_length=500.0, uninherited=True)])
    result = generate_from_file(song, options)
    assert len(calls) == 1  # 40★ asks for the ceiling at once, and the ceiling falls short
    assert any("±" in w for w in result.warnings) and any("timing" in w for w in result.warnings)


def test_long_note_share_becomes_its_hold_note_condition(song):
    calls = []
    generate_from_file(song, GeneratorOptions(target_stars=1.0, backend="mapperatorinator", ln_ratio=0.3,
                                              model_runner=stream_runner(calls), model_runs=1))
    assert "hold_note_ratio=0.30" in calls[0][2]
    calls.clear()
    generate_from_file(song, GeneratorOptions(target_stars=1.0, backend="mapperatorinator", model_hold_ratio=0.0,
                                              ln_ratio=0.3, model_runner=stream_runner(calls), model_runs=1))
    assert "hold_note_ratio=0.00" in calls[0][2]


def test_unknown_backend_is_refused(song):
    with pytest.raises(ValueError):
        generate_from_file(song, GeneratorOptions(target_stars=3.0, backend="nope"))


def test_run_model_needs_the_install(tmp_path):
    with pytest.raises(mt.ModelUnavailable):
        mt.run_model(tmp_path / "a.mp3", tmp_path / "out", 3.0, 0, home=tmp_path / "none")


def test_command_line_backend(song, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(mt, "install", lambda log=None, home=None: None)
    original = mt.generate_from_file
    monkeypatch.setattr(mt, "generate_from_file",
                        lambda audio, options, runner=None, **kw: original(audio, options, runner=stream_runner(calls), **kw))
    out = tmp_path / "gen"
    code = main(["--audio", str(song), "--target-sr", "2.0", "--backend", "mapperatorinator", "--model-runs", "2",
                 "--model-arg", "descriptors=['tech']", "--output-dir", str(out)])
    assert code == 0, capsys.readouterr().err
    assert "descriptors=['tech']" in calls[0][2]
    assert not any(a.startswith("hold_note_ratio") for a in calls[0][2])  # no --ln: the model chooses
    report = capsys.readouterr().out
    assert "Mapperatorinator 生成" in report
    osz = next(out.glob("*.osz"))
    with zipfile.ZipFile(osz) as z:
        names = z.namelist()
    assert "song.mp3" in names and any(n.endswith(".osu") for n in names)
    chart = parse_osu_7k(next(out.glob("*.osu")).read_text(encoding="utf-8"))
    assert chart.circle_size == 7 and chart.hit_objects


def test_model_processes_start_without_the_parent_interpreters_variables(monkeypatch):
    monkeypatch.setenv("__PYVENV_LAUNCHER__", r'"C:\proj7k\.venv\Scripts\python.exe"')
    monkeypatch.setenv("PYTHONPATH", "src")
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\proj7k\.venv")
    monkeypatch.setenv("PROJ7K_KEEP", "1")
    env = mt.child_env(PYTHONUTF8="1")
    assert not {"__PYVENV_LAUNCHER__", "PYTHONPATH", "VIRTUAL_ENV"} & set(env)
    assert env["PROJ7K_KEEP"] == "1" and env["PYTHONUTF8"] == "1"


def test_a_venv_whose_python_cant_be_seen_is_pointed_at_one_that_can(tmp_path, monkeypatch):
    # uv's default %APPDATA% install, written from inside an MSIX app, is invisible to the dashboard outside it.
    home = tmp_path / "mt"
    mt.venv_python(home).parent.mkdir(parents=True)
    mt.venv_python(home).write_bytes(b"")
    (home / "repo").mkdir()
    (home / "repo" / "inference.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(mt, "patch", lambda home=None, log=None: None)
    (home / "repo" / "requirements.txt").write_text("", encoding="utf-8")
    (home / ".installed").write_text(mt.COMMIT, encoding="utf-8")
    cfg = home / ".venv" / "pyvenv.cfg"
    cfg.write_text(f"home = {tmp_path / 'gone'}\nversion_info = 3.10.22\n", encoding="utf-8")
    assert not mt.available(home)

    seen = mt.python_dir(home) / "cpython-3.10.22" / ("python.exe" if os.name == "nt" else "bin/python3")
    seen.parent.mkdir(parents=True)
    seen.write_bytes(b"")
    runs = []
    monkeypatch.setattr(mt, "_run", lambda cmd, log, cwd=None, **env: runs.append((list(map(str, cmd)), env)))
    monkeypatch.setattr(mt, "_uv", lambda: ["uv"])
    monkeypatch.setattr(mt, "_managed_python", lambda uv, log, home: str(seen))
    monkeypatch.setattr(mt.shutil, "which", lambda name: name)
    mt.install(log=lambda message: None, home=home)
    assert f"home = {seen.parent}" in cfg.read_text(encoding="utf-8").splitlines()
    assert "version_info = 3.10.22" in cfg.read_text(encoding="utf-8")
    assert not any(cmd[1:2] == ["venv"] for cmd, _ in runs)  # its packages are kept
    assert mt.available(home)


def test_a_run_a_little_too_hard_is_trimmed_to_the_target(song, tmp_path):
    calls = []
    hard = stream_runner(calls)
    probe = float(evaluate_osu(hard(song, tmp_path / "probe", 3.0, 0, []).read_text(encoding="utf-8")).total_stars)
    raw = float(evaluate_osu(hard(song, tmp_path / "probe", 4.0, 0, []).read_text(encoding="utf-8")).total_stars)
    assert probe < raw <= probe + mt.TRIM_MAX
    calls.clear()

    def always_four(audio, out, difficulty, seed, extra):
        return hard(audio, out, 4.0, seed, extra)

    options = GeneratorOptions(target_stars=probe + 0.3, backend="mapperatorinator", model_runner=always_four,
                               model_runs=4)
    result = generate_from_file(song, options)
    assert len(calls) == 1
    assert abs(result.stars - options.target_stars) <= options.tolerance
    assert result.n_notes < len(parse_osu_7k((tmp_path / "probe" / "beatmap2.osu").read_text(encoding="utf-8")).hit_objects)


def _trim_landing(monkeypatch, offset):
    """Stands in for the downscaler: a trim that lands `offset` stars from the target."""
    class Profile:
        def __init__(self, stars):
            self.total_stars = stars

    def landing(chart, target, tolerance, log):
        return chart, Profile(target + offset)
    monkeypatch.setattr(mt, "trim", landing)


def _fixed_stars_run(song, tmp_path):
    """A runner that always writes the same chart, whatever difficulty it is asked for, and that chart's stars."""
    asked = []
    chart = stream_runner([])(song, tmp_path / "fixed", 4.0, 0, [])

    def run(audio, out, difficulty, seed, extra):
        asked.append(difficulty)
        return chart
    return run, asked, float(evaluate_osu(chart.read_text(encoding="utf-8")).total_stars)


def test_a_trim_that_lands_close_is_kept(song, tmp_path, monkeypatch):
    _trim_landing(monkeypatch, -0.15)
    run, asked, raw = _fixed_stars_run(song, tmp_path)
    options = GeneratorOptions(target_stars=raw - 0.5, backend="mapperatorinator", model_runner=run, model_runs=4)
    result = generate_from_file(song, options)
    assert len(asked) == 1
    assert not any("最接近" in w or "closest" in w for w in result.warnings)


def test_the_next_run_is_steered_by_what_the_model_wrote_not_the_trim(song, tmp_path, monkeypatch):
    _trim_landing(monkeypatch, -0.6)
    run, asked, raw = _fixed_stars_run(song, tmp_path)
    options = GeneratorOptions(target_stars=raw - 0.5, backend="mapperatorinator", model_runner=run, model_runs=2)
    generate_from_file(song, options)
    # The model wrote above the target, so it is asked for less, though the trim undershot.
    assert len(asked) == 2 and asked[1] < asked[0]


def holey_runner(calls, holey_runs, refill_works):
    """The stand-in, with no notes from 20 to 40 s on the runs in `holey_runs` (counted from 1, refills aside);
    a refill writes the stretch when `refill_works`, else hands the chart back as it was."""
    stream = stream_runner(calls)
    runs = []

    def run(audio, out, difficulty, seed, extra):
        if "add_to_beatmap=true" in extra and not refill_works:
            calls.append((difficulty, seed, list(extra)))
            return Path(next(o for o in extra if o.startswith("beatmap_path="))[len("beatmap_path='"):-1])
        path = stream(audio, out, difficulty, seed, extra)
        if "add_to_beatmap=true" not in extra:
            runs.append(seed)
            if len(runs) in holey_runs:
                bm = parse_osu_7k(path.read_text(encoding="utf-8"))
                bm.hit_objects = [o for o in bm.hit_objects if not 20000 <= o.time < 40000]
                path.write_text(dump_osu_7k(bm), encoding="utf-8")
        return path
    return run


def _target(song):
    chart = parse_osu_7k(stream_runner([])(song, song.parent / "probe", 1.0, 0, []).read_text(encoding="utf-8"))
    assert mt.holes(chart) == 0.0
    return float(evaluate_osu(dump_osu_7k(chart)).total_stars) / mt.AIM


def test_an_empty_stretch_is_written_again_around_the_notes_it_has(song):
    calls = []
    options = GeneratorOptions(target_stars=_target(song), backend="mapperatorinator",
                               model_runner=holey_runner(calls, {1}, refill_works=True), model_runs=3, tolerance=5.0)
    result = generate_from_file(song, options)
    assert len(calls) == 2
    refill = calls[1][2]
    assert "add_to_beatmap=true" in refill and "in_context=[TIMING]" in refill and "parallel=true" not in refill
    start = int(next(o for o in refill if o.startswith("start_time="))[len("start_time="):])
    end = int(next(o for o in refill if o.startswith("end_time="))[len("end_time="):])
    assert 19000 < start < 20000 and 39000 < end <= 40000
    assert mt.holes(result.beatmap) == 0.0


def test_a_run_that_stays_empty_is_passed_over(song):
    calls = []
    options = GeneratorOptions(target_stars=_target(song), backend="mapperatorinator",
                               model_runner=holey_runner(calls, {1}, refill_works=False), model_runs=3, tolerance=5.0)
    result = generate_from_file(song, options)
    assert len(calls) == 3  # the holey run, its refill, then a whole one
    assert mt.holes(result.beatmap) == 0.0
    assert not any("空白" in w for w in result.warnings)


def test_a_reference_break_is_left_alone(song):
    calls = []
    reference = parse_osu_7k(stream_runner([])(song, song.parent / "ref", 1.0, 0, []).read_text(encoding="utf-8"))
    reference.hit_objects = [o for o in reference.hit_objects if not 20000 <= o.time < 40000]
    assert mt.refill_spans(reference, reference) == []
    assert len(mt.refill_spans(reference)) == 1


def test_borrowed_timing_hands_over_the_reference_without_its_online_id(song):
    calls = []
    reference = Beatmap7K(title="Ref", artist="A", creator="Mapper", version="Hard",
                          timing_points=[TimingPoint(time=1000.0, beat_length=375.0, uninherited=True)],
                          hit_objects=[HitObject(column=0, time=1000, note_type=NoteType.RICE)],
                          extra_sections={"Metadata": {"BeatmapID": "123", "BeatmapSetID": "45"}})
    options = GeneratorOptions(target_stars=1.0, backend="mapperatorinator", model_runner=stream_runner(calls),
                               model_runs=1, reference=reference, timing=reference.timing_points, model_fast=True)
    result = generate_from_file(song, options)
    extra = calls[0][2]
    assert "in_context=[TIMING]" in extra and f"parallel_chains={mt.FAST_CHAINS}" in extra
    path = Path(next(o for o in extra if o.startswith("beatmap_path="))[len("beatmap_path='"):-1])
    text = path.read_text(encoding="utf-8")
    assert "BeatmapID:0" in text and "BeatmapSetID:-1" in text and "TimingPoints" in text
    assert reference.extra_sections["Metadata"]["BeatmapID"] == "123"
    assert not any("timing" in w for w in result.warnings)
    assert result.timing_source == "reference"


def test_stops_once_the_difficulty_condition_is_at_its_ceiling(song):
    calls = []
    options = GeneratorOptions(target_stars=60.0, backend="mapperatorinator", model_runner=stream_runner(calls),
                               model_runs=8)
    result = generate_from_file(song, options)
    assert calls[-1][0] == mt.MAX_DIFFICULTY and len(calls) < 8
    assert any(f"{mt.MAX_DIFFICULTY:g}★" in w for w in result.warnings)


def ln_stream_runner(calls):
    """The stand-in, with 10–20 s written as one 1/4-beat long note per row, one column at a time, unless asked
    for no long notes."""
    stream = stream_runner(calls)

    def run(audio, out, difficulty, seed, extra):
        path = stream(audio, out, difficulty, seed, extra)
        if "hold_note_ratio=0.00" in extra:
            return path
        bm = parse_osu_7k(path.read_text(encoding="utf-8"))
        beat = 60000.0 / 160
        stream_notes = [HitObject(column=k % 7, time=round(10000 + k * beat / 4), note_type=NoteType.LN,
                                  end_time=round(10000 + (k + 1) * beat / 4 - 10)) for k in range(int(10000 / (beat / 4)))]
        bm.hit_objects = [o for o in bm.hit_objects if not 10000 <= o.time < 20000] + stream_notes
        path.write_text(dump_osu_7k(bm), encoding="utf-8")
        return path
    return run


def test_a_long_note_stream_is_written_again_without_long_notes(song):
    calls = []
    options = GeneratorOptions(target_stars=1.0, backend="mapperatorinator", model_runner=ln_stream_runner(calls),
                               model_runs=1, tolerance=50.0)
    raw = parse_osu_7k(ln_stream_runner([])(song, song.parent / "probe", 1.0, 0, []).read_text(encoding="utf-8"))
    assert mt.ln_streams(raw) and mt.ln_streams(raw)[0][0] == 10000
    result = generate_from_file(song, options)
    assert len(calls) == 2
    remap = calls[1][2]
    assert "add_to_beatmap=true" in remap and "hold_note_ratio=0.00" in remap
    assert not mt.ln_streams(parse_osu_7k(dump_osu_7k(result.beatmap)))


def test_holds_shorter_than_a_third_of_a_beat_are_taps():
    beat = 400.0
    chart = Beatmap7K(timing_points=[TimingPoint(time=0.0, beat_length=beat, uninherited=True)],
                      hit_objects=[HitObject(column=0, time=0, note_type=NoteType.LN, end_time=100),
                                   HitObject(column=1, time=0, note_type=NoteType.LN, end_time=200)])
    kinds = [o.note_type for o in mt.short_holds_to_taps(chart)]
    assert kinds == [NoteType.RICE, NoteType.LN]


def test_the_dashboard_asks_the_model_for_no_long_notes_on_a_rice_chart(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from proj7k.dashboard import actions
    from proj7k.generator import cli

    seen = {}

    def run_generate(options, output_dir, **kw):
        seen["ratio"] = options.model_hold_ratio
        raise RuntimeError("stop")

    monkeypatch.setattr(cli, "run_generate", run_generate)
    monkeypatch.setattr(mt, "install", lambda log=None, home=None: None)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"")
    config = SimpleNamespace(realm_path=None, lazer_files_dir=None, generated_dir=tmp_path)
    for ln, expected in (("0", 0.0), ("0.35", 0.35)):
        with pytest.raises(RuntimeError):
            actions.generate_chart(config, {"target_sr": "5", "audio": str(audio), "backend": "mapperatorinator",
                                            "ln_ratio": ln}, None)
        assert seen["ratio"] == expected


def _pinned_files(tmp_path, nl="\r\n"):
    repo = mt.repo_dir(tmp_path)
    for name in {name for name, _, _ in mt.patches.PATCHES}:
        lines = ["# head"] + [line for n, line, _ in mt.patches.PATCHES if n == name] + ["# tail"]
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(nl.join(lines).encode("utf-8"))
    return repo


def test_the_pinned_source_is_patched_once_keeping_its_line_endings(tmp_path):
    repo = _pinned_files(tmp_path)
    assert not mt.patched(tmp_path)
    mt.patch(tmp_path)
    assert mt.patched(tmp_path)
    text = (repo / "osuT5/osuT5/inference/processor.py").read_bytes().decode("utf-8")
    assert "\n" not in text.replace("\r\n", "") and "def generate_chains(" in text and "self.generate_chains" in text
    before = {name: (repo / name).read_bytes() for name, _, _ in mt.patches.PATCHES}
    mt.patch(tmp_path)
    assert {name: (repo / name).read_bytes() for name, _, _ in mt.patches.PATCHES} == before
    (repo / "config.py").write_text("changed upstream\n", encoding="utf-8")
    (repo / "osuT5/osuT5/inference/processor.py").write_text("changed upstream\n", encoding="utf-8")
    with pytest.raises(mt.ModelUnavailable):
        mt.patch(tmp_path)


def test_a_checkout_with_an_older_patch_is_restored_first(tmp_path, monkeypatch):
    repo = _pinned_files(tmp_path, "\n")
    config = (repo / "config.py").read_text(encoding="utf-8")
    (repo / "config.py").write_text(config + "\n    parallel_keep: float = 1.0  # proj7k old\n", encoding="utf-8")
    restored = []

    def run(cmd, log, cwd=None, **env):
        restored.append([str(c) for c in cmd])
        _pinned_files(tmp_path, "\n")
    monkeypatch.setattr(mt, "_run", run)
    mt.patch(tmp_path)
    assert restored and restored[0][3:6] == ["checkout", "--quiet", mt.COMMIT]
    assert mt.patched(tmp_path) and "parallel_keep" not in (repo / "config.py").read_text(encoding="utf-8")
