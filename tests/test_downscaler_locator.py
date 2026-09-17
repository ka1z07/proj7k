"""
Tests for proj7k.downscaler.locator module and URL-driven downscaler workflow.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import zipfile
import pytest

from proj7k.downscaler.locator import (
    BeatmapUrlInfo,
    ResolvedBeatmapAsset,
    parse_osu_url_or_id,
    locate_beatmap_in_lazer,
    package_into_osz,
)
from proj7k.downscaler.cli import main
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k


def _create_synthetic_osu_file(path: Path, title: str = "Locator Song") -> Path:
    hit_objects = [
        HitObject(column=i % 7, time=i * 200, note_type=NoteType.RICE)
        for i in range(28)
    ]
    bm = Beatmap7K(
        title=title,
        artist="Test Artist",
        creator="Mapper",
        version="Extra",
        hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)],
    )
    path.write_text(dump_osu_7k(bm), encoding="utf-8")
    return path


def test_parse_osu_url_or_id_formats():
    # 1. Full beatmapset + mode + beatmap id
    u1 = parse_osu_url_or_id("https://osu.ppy.sh/beatmapsets/2422670#mania/5271675")
    assert u1.beatmapset_id == 2422670
    assert u1.beatmap_id == 5271675
    assert u1.ruleset_mode == "mania"
    assert u1.is_valid

    # 2. Beatmapset only
    u2 = parse_osu_url_or_id("https://osu.ppy.sh/beatmapsets/2422670")
    assert u2.beatmapset_id == 2422670
    assert u2.beatmap_id is None
    assert u2.is_valid

    # 3. Short b/<id> URL
    u3 = parse_osu_url_or_id("https://osu.ppy.sh/b/5271675")
    assert u3.beatmap_id == 5271675
    assert u3.beatmapset_id is None
    assert u3.is_valid

    # 4. /beatmaps/<id> URL
    u4 = parse_osu_url_or_id("https://osu.ppy.sh/beatmaps/5271675")
    assert u4.beatmap_id == 5271675
    assert u4.beatmapset_id is None
    assert u4.is_valid

    # 5. Plain numeric string & int
    u5 = parse_osu_url_or_id("5271675")
    assert u5.beatmap_id == 5271675
    assert u5.is_valid

    u6 = parse_osu_url_or_id(5271675)
    assert u6.beatmap_id == 5271675
    assert u6.is_valid

    # 6. Invalid input
    u7 = parse_osu_url_or_id("https://example.com/not_osu")
    assert not u7.is_valid


def test_package_into_osz(tmp_path):
    osu_file = tmp_path / "chart.osu"
    osu_file.write_text("osu file format v14\n", encoding="utf-8")

    audio_file = tmp_path / "song.mp3"
    audio_file.write_bytes(b"\xFF\xFB\x90\x44" * 10)

    bg_file = tmp_path / "image.png"
    bg_file.write_bytes(b"\x89PNG\r\n\x1a\n" * 10)

    osz_dest = tmp_path / "packaged.osz"
    res = package_into_osz(
        practice_osu_path=osu_file,
        output_osz_path=osz_dest,
        audio_path=audio_file,
        audio_filename="custom_audio.mp3",
        bg_path=bg_file,
        bg_filename="custom_bg.png",
    )

    assert res.exists()
    assert res == osz_dest

    # Verify contents of zip
    with zipfile.ZipFile(osz_dest, "r") as z:
        names = z.namelist()
        assert "chart.osu" in names
        assert "custom_audio.mp3" in names
        assert "custom_bg.png" in names


def test_locate_beatmap_in_lazer_mock(tmp_path):
    files_dir = tmp_path / "files"
    dummy_hash = "aabbccddee11223344556677889900aabbccddee11223344556677889900aabbcc"
    osu_path = files_dir / dummy_hash[0] / dummy_hash[:2] / dummy_hash
    osu_path.parent.mkdir(parents=True, exist_ok=True)
    _create_synthetic_osu_file(osu_path)

    mock_record = {
        "success": True,
        "found": True,
        "beatmap": {
            "id": "mock-uuid",
            "title": "Mock Title",
            "artist": "Mock Artist",
            "creator": "Mock Creator",
            "difficulty_name": "Mock Diff",
            "osu_file_hash": dummy_hash,
            "audio_file": {"filename": "audio.mp3", "hash": dummy_hash},
            "bg_file": {"filename": "bg.png", "hash": dummy_hash},
            "files": [
                {"filename": "audio.mp3", "hash": dummy_hash},
                {"filename": "bg.png", "hash": dummy_hash},
                {"filename": "mock.osu", "hash": dummy_hash},
            ],
        },
    }

    with patch("proj7k.downscaler.locator.RealmBridgeClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.locate_beatmap.return_value = mock_record

        asset = locate_beatmap_in_lazer(
            "https://osu.ppy.sh/beatmapsets/1000#mania/2000",
            files_dir=files_dir,
        )

        assert asset is not None
        assert asset.title == "Mock Title"
        assert asset.beatmap_id == 2000
        assert asset.osu_path.exists()
        assert asset.audio_path.exists()
        assert asset.bg_path.exists()


def test_cli_with_url_and_package_osz(tmp_path, capsys):
    files_dir = tmp_path / "files"
    osu_hash = "11223344556677889900aabbccddee11223344556677889900aabbccddee112233"
    audio_hash = "99887766554433221100aabbccddee11223344556677889900aabbccddee1122"
    
    osu_path = files_dir / osu_hash[0] / osu_hash[:2] / osu_hash
    osu_path.parent.mkdir(parents=True, exist_ok=True)
    _create_synthetic_osu_file(osu_path, title="URL Test Song")

    audio_path = files_dir / audio_hash[0] / audio_hash[:2] / audio_hash
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"\xFF\xFB\x90\x44" * 10)

    mock_asset = ResolvedBeatmapAsset(
        osu_path=osu_path,
        title="URL Test Song",
        artist="Test Artist",
        creator="Mapper",
        difficulty_name="Extra",
        audio_path=audio_path,
        audio_filename="audio.mp3",
        beatmap_id=5271675,
        beatmapset_id=2422670,
        file_hash=osu_hash,
    )

    out_dir = tmp_path / "custom_practice_maps"

    with patch("proj7k.downscaler.cli.locate_beatmap_in_lazer", return_value=mock_asset):
        code = main([
            "--input", "https://osu.ppy.sh/beatmapsets/2422670#mania/5271675",
            "--target-dan", "7th",
            "--output-dir", str(out_dir),
            "--package-osz",
        ])
        assert code == 0

    captured = capsys.readouterr()
    assert "PROJ7K PRACTICE GENERATOR & DOWNSCALER REPORT" in captured.out
    assert "OSZ Package" in captured.out

    # Verify both .osu and .osz files exist in out_dir
    osu_outputs = list(out_dir.glob("*.osu"))
    osz_outputs = list(out_dir.glob("*.osz"))
    assert len(osu_outputs) == 1
    assert len(osz_outputs) == 1

    # Check zip contains audio
    with zipfile.ZipFile(osz_outputs[0], "r") as z:
        names = z.namelist()
        assert osu_outputs[0].name in names
        assert "audio.mp3" in names
