import hashlib
import json
from pathlib import Path
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType
from proj7k.profiler.cli import main, run_ingestion
from proj7k.profiler.matcher import HitJudgment
from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr


@pytest.fixture
def synthetic_pair(tmp_path: Path):
    osu_content = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 3

[Difficulty]
CircleSize: 7
OverallDifficulty: 8

[HitObjects]
36,192,1000,1,0,0:0:0:0:
109,192,2000,1,0,0:0:0:0:
182,192,1500,128,0,2500:0:0:0:0:
"""
    osu_path = tmp_path / "test_map.osu"
    osu_path.write_text(osu_content, encoding="utf-8")
    md5_hash = hashlib.md5(osu_content.encode("utf-8")).hexdigest()

    # Replay:
    # 1. Col 0 (36 -> col 0): press at 1000ms (+0ms -> MAX)
    # 2. Col 1 (109 -> col 1): press at 2010ms (+10ms -> MAX)
    # 3. Col 2 (182 -> col 2): press at 1495ms (-5ms -> MAX), release at 2510ms (+10ms)
    # 4. Col 4 (empty track): press at 1200ms -> Panic Ghost Tap
    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        ReplayFrame(time_ms=1000.0, keys=1),     # Col 0 press
        ReplayFrame(time_ms=1050.0, keys=0),     # Col 0 release
        ReplayFrame(time_ms=1200.0, keys=16),    # Col 4 press (ghost tap)
        ReplayFrame(time_ms=1250.0, keys=0),     # Col 4 release
        ReplayFrame(time_ms=1495.0, keys=4),     # Col 2 press (LN)
        ReplayFrame(time_ms=2010.0, keys=6),     # Col 1 press while LN held
        ReplayFrame(time_ms=2060.0, keys=4),     # Col 1 release
        ReplayFrame(time_ms=2510.0, keys=0),     # Col 2 release (LN tail)
    ]

    replay = OSRReplay(
        mode=3,
        game_version=20240101,
        beatmap_hash=md5_hash,
        player_name="KzPlayer",
        replay_hash="hash123",
        c300g=3,
        c300=0,
        c200=0,
        c100=0,
        c50=0,
        miss=0,
        total_score=1000000,
        max_combo=3,
        perfect=True,
        mods=0,
        timestamp_ticks=638000000000000000,
        action_frames=frames,
    )

    osr_path = tmp_path / "test_replay.osr"
    osr_path.write_bytes(serialize_osr(replay))

    return osu_path, osr_path, md5_hash


def test_run_ingestion_end_to_end(synthetic_pair):
    osu_path, osr_path, md5_hash = synthetic_pair

    report = run_ingestion(replay_path=osr_path, beatmap_path=osu_path)

    assert report.player_name == "KzPlayer"
    assert report.beatmap_hash == md5_hash
    assert report.hash_matched is True
    assert report.total_notes == 3
    assert report.miss_count == 0
    assert report.ghost_tap_count == 1
    assert report.official_counts["300g"] == 3
    assert report.timestamp_ticks == 638000000000000000
    assert report.play_duration_s == 2.51
    assert report.is_valid_play is False  # Duration < 30s


def test_cli_stdout_and_json(synthetic_pair, capsys):
    osu_path, osr_path, _ = synthetic_pair

    # Test standard human-readable stdout
    code = main(["--replay", str(osr_path), "--beatmap", str(osu_path)])
    assert code == 0
    captured = capsys.readouterr()
    assert "KzPlayer" in captured.out
    assert "Panic Ghost Taps: 1" in captured.out
    assert "MAX" in captured.out
    assert "By Lane: R1: 1" in captured.out
    assert "Official Replay Header Counts" in captured.out
    assert "[NOTICE] Aborted or retry run" in captured.out

    # Test structured JSON output
    code_json = main(["--replay", str(osr_path), "--beatmap", str(osu_path), "--json"])
    assert code_json == 0
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert data["player_name"] == "KzPlayer"
    assert data["total_notes"] == 3
    assert data["miss_count"] == 0
    assert data["ghost_tap_count"] == 1
    assert data["official_counts"]["300g"] == 3
    assert data["ghost_taps_by_column"]["R1"] == 1
    assert len(data["aligned_hits"]) == 3
    assert len(data["ghost_taps"]) == 1
    assert data["ghost_taps"][0]["column"] == 4
    assert data["ghost_taps"][0]["lane"] == "R1"
    assert data["ghost_taps"][0]["reason"] == "empty_track"


def test_cli_missing_files_error(capsys):
    code = main(["--replay", "non_existent.osr", "--beatmap", "non_existent.osu"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err


def test_hash_mismatch_warning(tmp_path: Path, synthetic_pair, capsys):
    osu_path, osr_path, _ = synthetic_pair
    # Alter the .osu content so MD5 hash changes
    osu_modified = tmp_path / "modified.osu"
    osu_modified.write_text(osu_path.read_text() + "\n// modified", encoding="utf-8")

    code = main(["--replay", str(osr_path), "--beatmap", str(osu_modified)])
    assert code == 0
    captured = capsys.readouterr()
    assert "MISMATCH WARNING" in captured.out


def test_stream_and_mashing_isolation(tmp_path: Path):
    # Beatmap with 3 rapid notes on col 0: at 1000ms, 1200ms, 1400ms
    osu_content = """osu file format v14
[General]
Mode: 3
[Difficulty]
CircleSize: 7
OverallDifficulty: 8
[HitObjects]
36,192,1000,1,0,0:0:0:0:
36,192,1200,1,0,0:0:0:0:
36,192,1400,1,0,0:0:0:0:
"""
    osu_file = tmp_path / "stream.osu"
    osu_file.write_text(osu_content, encoding="utf-8")

    # Replay:
    # 1. Panic mashing before note 0: at 500ms, 600ms, 700ms (3 ghost taps)
    # 2. Perfect tap on note 0: 1000ms (hit)
    # 3. Completely miss note 1 (no tap in [1036, 1364])
    # 4. Tap on note 2: 1390ms (-10ms -> MAX)
    # 5. Panic mashing after note 2: at 1800ms, 1900ms (2 ghost taps)
    frames = [
        ReplayFrame(time_ms=0.0, keys=0),
        ReplayFrame(time_ms=500.0, keys=1),
        ReplayFrame(time_ms=530.0, keys=0),
        ReplayFrame(time_ms=600.0, keys=1),
        ReplayFrame(time_ms=630.0, keys=0),
        ReplayFrame(time_ms=700.0, keys=1),
        ReplayFrame(time_ms=730.0, keys=0),
        ReplayFrame(time_ms=1000.0, keys=1),
        ReplayFrame(time_ms=1050.0, keys=0),
        ReplayFrame(time_ms=1390.0, keys=1),
        ReplayFrame(time_ms=1440.0, keys=0),
        ReplayFrame(time_ms=1800.0, keys=1),
        ReplayFrame(time_ms=1830.0, keys=0),
        ReplayFrame(time_ms=1900.0, keys=1),
        ReplayFrame(time_ms=1930.0, keys=0),
    ]

    replay = OSRReplay(
        mode=3,
        game_version=20240101,
        player_name="StreamPlayer",
        action_frames=frames,
    )
    osr_file = tmp_path / "stream.osr"
    osr_file.write_bytes(serialize_osr(replay))

    report = run_ingestion(osr_file, osu_file)
    assert report.total_notes == 3
    assert report.total_hits == 3
    assert report.miss_count == 1
    assert report.judgment_counts[HitJudgment.MAX] == 2
    assert report.judgment_counts[HitJudgment.MISS] == 1
    assert report.ghost_tap_count == 5  # 3 before + 2 after

