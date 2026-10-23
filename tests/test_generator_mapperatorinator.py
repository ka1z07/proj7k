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
    assert len(calls) == 3
    assert [round(c[0], 2) for c in calls] == sorted(round(c[0], 2) for c in calls)  # asks for more each time
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
