"""
The generator takes its song from a chart of any mode or key count (a 4K or osu! chart, an .osz): its audio, timing,
title and background, never its notes. The chart editor and readback still want 7K.
"""

import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from proj7k.downscaler.locator import BeatmapUrlInfo, locate_beatmap_in_lazer
from proj7k.generator import cli
from proj7k.generator import mapperatorinator as mt
from proj7k.parser import parse_osu_7k

from test_generator_mapperatorinator import stream_runner


def chart_text(mode: int, keys: int, version: str = "Hard") -> str:
    objects = [f"{int((c % keys) * 512 / keys + 256 / keys)},192,{1000 + 375 * c},1,0,0:0:0:0:" for c in range(16)]
    return "\n".join([
        "osu file format v14", "",
        "[General]", "AudioFilename: song.mp3", f"Mode: {mode}", "",
        "[Metadata]", "Title:Song", "Artist:Band", "Creator:Mapper", f"Version:{version}",
        "BeatmapID:123", "BeatmapSetID:45", "",
        "[Difficulty]", f"CircleSize:{keys}", "OverallDifficulty:8", "",
        "[Events]", '0,0,"bg.jpg",0,0', "",
        "[TimingPoints]", "1000,375,4,1,0,100,1,0", "4000,-50,4,1,0,100,0,0", "",
        "[HitObjects]", *objects, "",
    ])


def write_set(folder: Path, charts: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in charts.items():
        (folder / name).write_text(text, encoding="utf-8")
    (folder / "song.mp3").write_bytes(b"audio")
    (folder / "bg.jpg").write_bytes(b"bg")
    return folder


def test_a_4k_chart_is_read_only_when_asked():
    with pytest.raises(ValueError):
        parse_osu_7k(chart_text(3, 4))
    bm = parse_osu_7k(chart_text(3, 4), any_mode=True)
    assert (bm.mode, bm.circle_size) == (3, 4) and len(bm.hit_objects) == 16


@pytest.mark.parametrize("mode,keys,source", [(3, 4, "4K"), (0, 4, "osu!"), (1, 5, "taiko"), (2, 5, "catch"), (3, 7, "7K")])
def test_a_reference_of_any_mode_gives_its_song(tmp_path, mode, keys, source):
    folder = write_set(tmp_path / "set", {"a.osu": chart_text(mode, keys)})
    ref = cli.resolve_reference(str(folder / "a.osu"), any_mode=True)
    assert ref.source == source
    assert ref.audio_path == folder / "song.mp3" and ref.bg_path == folder / "bg.jpg"
    assert (ref.beatmap.mode, ref.beatmap.circle_size) == (3, 7)
    assert [tp.time for tp in ref.beatmap.timing_points if tp.uninherited] == [1000.0]
    assert ref.beatmap.title == "Song" and ref.beatmap.artist == "Band"
    # Another mode's notes are not 7K notes: only a 7K reference keeps them.
    assert bool(ref.beatmap.hit_objects) == (source == "7K")


def test_without_any_mode_a_reference_must_be_7k(tmp_path):
    folder = write_set(tmp_path / "set", {"a.osu": chart_text(3, 4)})
    with pytest.raises(ValueError):
        cli.resolve_reference(str(folder / "a.osu"))


def test_an_osz_is_unpacked_and_its_mania_chart_preferred(tmp_path):
    osz = tmp_path / "Band - Song.osz"
    with zipfile.ZipFile(osz, "w") as z:
        z.writestr("a [Normal].osu", chart_text(0, 4, "Normal"))
        z.writestr("b [4K Hard].osu", chart_text(3, 4, "4K Hard"))
        z.writestr("song.mp3", b"audio")
        z.writestr("bg.jpg", b"bg")
        z.writestr("../escape.txt", b"never written outside")
    ref = cli.resolve_reference(str(osz), any_mode=True)
    assert ref.source == "4K" and ref.osu_path.name == "b [4K Hard].osu"
    assert ref.audio_path is not None and ref.audio_path.read_bytes() == b"audio"
    assert ref.bg_path is not None and ref.bg_filename == "bg.jpg"
    assert not (ref.osu_path.parent.parent / "escape.txt").exists()


def test_the_command_line_generates_7k_from_a_4k_osz(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(mt, "install", lambda log=None, home=None: None)
    original = mt.generate_from_file
    monkeypatch.setattr(mt, "generate_from_file",
                        lambda audio, options, runner=None, **kw: original(audio, options, runner=stream_runner(calls), **kw))
    osz = tmp_path / "song.osz"
    with zipfile.ZipFile(osz, "w") as z:
        z.writestr("a [4K].osu", chart_text(3, 4, "4K"))
        z.writestr("song.mp3", b"audio")
    out = tmp_path / "gen"
    code = cli.main(["--audio", str(osz), "--target-sr", "2.0", "--model-runs", "1", "--output-dir", str(out)])
    assert code == 0, capsys.readouterr().err
    report = capsys.readouterr().out
    assert "借用参考谱面" in report and "4K" in report
    extra = calls[0][2]
    reference_osu = Path(next(o for o in extra if o.startswith("beatmap_path="))[len("beatmap_path='"):-1])
    handed = parse_osu_7k(reference_osu.read_text(encoding="utf-8"))  # 7K mania, timing only
    assert handed.circle_size == 7 and not handed.hit_objects
    chart = parse_osu_7k(next(out.glob("*.osu")).read_text(encoding="utf-8"))
    assert chart.circle_size == 7 and chart.mode == 3 and chart.hit_objects and chart.title == "Song"
    with zipfile.ZipFile(next(out.glob("*.osz"))) as z:
        assert "song.mp3" in z.namelist()


def test_the_dashboard_takes_a_dropped_chart_and_the_games_non_7k_selection(tmp_path, monkeypatch):
    from proj7k.dashboard import actions

    seen = []

    def resolve(spec, realm_path=None, files_dir=None, any_mode=False):
        seen.append((spec, any_mode))
        raise RuntimeError("stop")

    monkeypatch.setattr(cli, "resolve_reference", resolve)
    config = SimpleNamespace(realm_path=None, lazer_files_dir=None, generated_dir=tmp_path)
    osz = tmp_path / "song.osz"
    osz.write_bytes(b"")
    with pytest.raises(RuntimeError):
        actions.generate_chart(config, {"target_sr": "5", "audio": str(osz)}, None)
    assert seen[-1] == (str(osz), True)
    with pytest.raises(RuntimeError):
        actions.generate_chart(config, {"target_sr": "5", "game_title": "Song", "game_artist": "Band",
                                        "game_difficulty": "4K Hard"}, None)
    info, any_mode = seen[-1]
    assert any_mode and isinstance(info, BeatmapUrlInfo)
    assert (info.title, info.artist, info.difficulty) == ("Song", "Band", "4K Hard")
    with pytest.raises(RuntimeError):
        actions.generate_chart(config, {"target_sr": "5", "reference": "0123456789abcdef0123456789abcdef"}, None)
    assert seen[-1] == ("0123456789abcdef0123456789abcdef", True)


def test_lazer_is_searched_by_title_and_difficulty(tmp_path):
    files_dir = tmp_path / "files"
    h = "e6" * 32
    path = files_dir / h[0] / h[:2] / h
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(chart_text(3, 4), encoding="utf-8")
    client = MagicMock()
    client.locate_beatmap.return_value = {"title": "Song", "artist": "Band", "difficulty_name": "4K Hard",
                                          "osu_file_hash": h}
    info = BeatmapUrlInfo(original_input="Song [4K Hard]", title="Song", artist="Band", difficulty="4K Hard")
    asset = locate_beatmap_in_lazer(info, files_dir=files_dir, bridge_client=client)
    kw = client.locate_beatmap.call_args.kwargs
    assert (kw["title"], kw["artist"], kw["difficulty"]) == ("Song", "Band", "4K Hard")
    assert asset is not None and asset.osu_path == path
