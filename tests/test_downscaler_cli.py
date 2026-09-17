"""
Tests for proj7k.downscaler CLI entrypoint and Lazer Bridge practice sync.

SPEC-P5.1-04 / ADR-0011 / Ticket 14.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k, parse_osu_7k
from proj7k.downscaler.cli import build_parser, main, format_downscale_report
from proj7k.lazer.bridge import BatchUpdateResult, LazerBeatmapRecord


def _create_synthetic_osu_file(path: Path, title: str = "Test Song", bpm: float = 280.0) -> Path:
    beat_length = 60000.0 / bpm
    step_ms = beat_length / 2.0
    hit_objects = []
    for step in range(32):
        t = step * step_ms
        cols = [0, 2, 4] if (step % 2 == 0) else [1, 3, 5]
        for c in cols:
            hit_objects.append(HitObject(column=c, time=t, note_type=NoteType.RICE))

    bm = Beatmap7K(
        title=title,
        artist="Test Artist",
        creator="Tester",
        version="Expert",
        hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=beat_length, meter=4, uninherited=True)],
    )
    dumped = dump_osu_7k(bm)
    path.write_text(dumped, encoding="utf-8")
    return path


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "downscaler" in captured.out.lower() or "practice" in captured.out.lower()


def test_cli_missing_input(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code != 0


def test_cli_missing_target(tmp_path, capsys):
    osu_file = _create_synthetic_osu_file(tmp_path / "test.osu")
    code = main(["--input", str(osu_file)])
    assert code != 0
    captured = capsys.readouterr()
    assert "target" in captured.err.lower() or "target" in captured.out.lower()


def test_cli_single_beatmap_success(tmp_path, capsys):
    osu_file = _create_synthetic_osu_file(tmp_path / "test.osu")
    code = main(["--input", str(osu_file), "--target-dan", "7th"])
    assert code == 0

    captured = capsys.readouterr()
    assert "PROJ7K PRACTICE GENERATOR & DOWNSCALER REPORT" in captured.out
    assert "Star Rating" in captured.out
    assert "8-DIMENSION TECHNIQUE RADAR" in captured.out.upper()

    # Verify output file generated in same directory
    practice_files = list(tmp_path.glob("*.osu"))
    assert len(practice_files) == 2
    derivative_file = [f for f in practice_files if f.name != "test.osu"][0]
    parsed = parse_osu_7k(str(derivative_file))
    assert "[P-7th" in parsed.version
    assert "proj7k_downscaled" in parsed.tags
    assert "target_7th" in parsed.tags


def test_cli_output_dir_and_target_sr(tmp_path, capsys):
    in_file = _create_synthetic_osu_file(tmp_path / "source.osu")
    out_dir = tmp_path / "output_folder"
    code = main([
        "--input", str(in_file),
        "--target-sr", "6.2",
        "--output-dir", str(out_dir),
    ])
    assert code == 0
    assert out_dir.exists()
    out_files = list(out_dir.glob("*.osu"))
    assert len(out_files) == 1
    parsed = parse_osu_7k(str(out_files[0]))
    assert "proj7k_downscaled" in parsed.tags


def test_cli_batch_directory(tmp_path, capsys):
    songs_dir = tmp_path / "songs"
    songs_dir.mkdir()
    song1 = _create_synthetic_osu_file(songs_dir / "song1.osu", title="Song 1")
    song2 = _create_synthetic_osu_file(songs_dir / "song2.osu", title="Song 2")

    # Non-osu file should be ignored
    (songs_dir / "readme.txt").write_text("not a beatmap")

    code = main([
        "--input", str(songs_dir),
        "--target-dan", "6th",
    ])
    assert code == 0

    captured = capsys.readouterr()
    assert "Song 1" in captured.out
    assert "Song 2" in captured.out

    # Both derivative files should exist
    all_osu = list(songs_dir.glob("*.osu"))
    assert len(all_osu) == 4  # 2 original + 2 practice


def test_cli_sync_lazer_integration(tmp_path, capsys):
    osu_file = _create_synthetic_osu_file(tmp_path / "test_sync.osu")
    realm_path = tmp_path / "client.realm"
    realm_path.touch()
    lock_path = tmp_path / "client.realm.lock"

    mock_record = LazerBeatmapRecord(
        id="uuid-1234",
        hash="hash1",
        md5_hash="dummy_md5",
        file_hash="file_hash1",
        star_rating=5.0,
        difficulty_name="Expert",
        tags="original_tags",
        title="Test Song",
        artist="Test Artist",
        ruleset_id=3,
        circle_size=7.0,
    )

    with patch("proj7k.downscaler.cli.SafeFlushWindow") as mock_window_cls, \
         patch("proj7k.downscaler.cli.RealmBridgeClient") as mock_bridge_cls:

        win_instance = MagicMock()
        win_instance.is_acquired = True
        mock_window_cls.return_value.__enter__.return_value = win_instance

        bridge_instance = mock_bridge_cls.return_value
        bridge_instance.dump_7k_beatmaps.return_value = [mock_record]
        bridge_instance.apply_batch_update.return_value = BatchUpdateResult(success=True, updated_count=1)

        code = main([
            "--input", str(osu_file),
            "--target-dan", "7th",
            "--sync-lazer",
            "--realm", str(realm_path),
            "--lock-path", str(lock_path),
        ])
        assert code == 0

        # Verify bridge was called with 7K Practice collection
        bridge_instance.apply_batch_update.assert_called_once()
        _, kwargs = bridge_instance.apply_batch_update.call_args
        collections = kwargs.get("collections", {})
        assert "7K Practice" in collections
        assert len(collections["7K Practice"]) > 0

        captured = capsys.readouterr()
        assert "7K Practice" in captured.out
        assert "SYNCHRONIZATION" in captured.out.upper()


def test_cli_sync_lazer_lock_held_failure(tmp_path, capsys):
    osu_file = _create_synthetic_osu_file(tmp_path / "test_sync_fail.osu")
    realm_path = tmp_path / "client.realm"
    realm_path.touch()

    with patch("proj7k.downscaler.cli.SafeFlushWindow") as mock_window_cls:
        win_instance = MagicMock()
        win_instance.is_acquired = False
        mock_window_cls.return_value.__enter__.return_value = win_instance

        code = main([
            "--input", str(osu_file),
            "--target-dan", "7th",
            "--sync-lazer",
            "--realm", str(realm_path),
        ])
        assert code == 1
        captured = capsys.readouterr()
        assert "Safe flush window is closed" in captured.err or "Safe flush window is closed" in captured.out


def test_cli_explicit_output_file(tmp_path):
    osu_file = _create_synthetic_osu_file(tmp_path / "origin.osu")
    dest_file = tmp_path / "custom_practice.osu"

    code = main([
        "--input", str(osu_file),
        "--target-dan", "8th",
        "--output", str(dest_file),
    ])
    assert code == 0
    assert dest_file.exists()
    parsed = parse_osu_7k(str(dest_file))
    assert "[P-8th" in parsed.version


def test_cli_non_7k_error(tmp_path, capsys):
    osu_file = tmp_path / "4k.osu"
    # Create 4K chart (CircleSize: 4)
    content = (
        "osu file format v14\n\n"
        "[General]\nMode: 3\n\n"
        "[Metadata]\nTitle: 4K Song\nArtist: Artist\nCreator: Tester\nVersion: 4K\n\n"
        "[Difficulty]\nCircleSize: 4\nOverallDifficulty: 8\n\n"
        "[TimingPoints]\n0,250,4,1,0,100,1,0\n\n"
        "[HitObjects]\n64,192,1000,1,0,0:0:0:0:\n"
    )
    osu_file.write_text(content, encoding="utf-8")

    code = main(["--input", str(osu_file), "--target-dan", "7th"])
    assert code != 0
    captured = capsys.readouterr()
    assert "not an osu!mania 7K chart" in captured.err or "No beatmaps were successfully downscaled" in captured.err


def test_cli_dry_run(tmp_path, capsys):
    osu_file = _create_synthetic_osu_file(tmp_path / "dry.osu")
    code = main([
        "--input", str(osu_file),
        "--target-dan", "7th",
        "--dry-run",
    ])
    assert code == 0
    # No extra files created
    files = list(tmp_path.glob("*.osu"))
    assert len(files) == 1
    captured = capsys.readouterr()
    assert "PROJ7K PRACTICE GENERATOR & DOWNSCALER REPORT" in captured.out


def test_cli_json_output(tmp_path, capsys):
    import json
    osu_file = _create_synthetic_osu_file(tmp_path / "json_test.osu")
    code = main([
        "--input", str(osu_file),
        "--target-dan", "7th",
        "--json",
    ])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["total_processed"] == 1
    assert data["failed_count"] == 0
    assert "report" in data["results"][0]
    assert data["results"][0]["report"]["target"]["target_dan"] == "7th"


def test_cli_sync_lazer_bridge_failure(tmp_path, capsys):
    osu_file = _create_synthetic_osu_file(tmp_path / "fail_sync.osu")
    realm_path = tmp_path / "client.realm"
    realm_path.touch()

    with patch("proj7k.downscaler.cli.SafeFlushWindow") as mock_window_cls, \
         patch("proj7k.downscaler.cli.RealmBridgeClient") as mock_bridge_cls:

        win_instance = MagicMock()
        win_instance.is_acquired = True
        mock_window_cls.return_value.__enter__.return_value = win_instance

        bridge_instance = mock_bridge_cls.return_value
        bridge_instance.dump_7k_beatmaps.return_value = []
        bridge_instance.apply_batch_update.return_value = BatchUpdateResult(
            success=False, error="Realm schema version mismatch"
        )

        code = main([
            "--input", str(osu_file),
            "--target-dan", "7th",
            "--sync-lazer",
            "--realm", str(realm_path),
        ])
        assert code == 1
        captured = capsys.readouterr()
        assert "Realm schema version mismatch" in captured.err


def test_cli_subprocess_invocation(tmp_path):
    import subprocess
    import sys
    osu_file = _create_synthetic_osu_file(tmp_path / "subproc.osu")
    cmd = [
        sys.executable,
        "-m",
        "proj7k.downscaler",
        "--input",
        str(osu_file),
        "--target-dan",
        "7th",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={"PYTHONPATH": "src", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "PROJ7K PRACTICE GENERATOR & DOWNSCALER REPORT" in proc.stdout
    # Verify file was written
    out_files = [f for f in tmp_path.glob("*.osu") if f.name != "subproc.osu"]
    assert len(out_files) == 1
