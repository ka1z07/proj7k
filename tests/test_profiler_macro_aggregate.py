import hashlib
import json
from pathlib import Path
import time
import pytest

from proj7k.parser import parse_osu_7k
from proj7k.profiler.matcher import HitJudgment
from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr
from proj7k.profiler.storage import (
    MatchSnapshot,
    ProfilerStorage,
    is_noise_match,
)
from proj7k.profiler.aggregate import (
    DimensionMacroMetric,
    MacroProfile,
    aggregate_macro_profile,
)
from proj7k.profiler.cli import main, run_ingestion
from proj7k.radar import TECHNIQUE_NAMES


def create_sample_chart(tmp_path: Path, bpm: float = 140.0, num_notes: int = 50) -> Path:
    lines = [
        "osu file format v14",
        "",
        "[General]",
        "Mode: 3",
        "",
        "[Difficulty]",
        "CircleSize: 7",
        "OverallDifficulty: 8",
        "",
        "[TimingPoints]",
        f"0,{60000.0 / bpm},4,2,0,100,1,0",
        "",
        "[HitObjects]",
    ]
    col_x = [36, 109, 182, 256, 329, 402, 475]
    for i in range(num_notes):
        t = 1000 + i * 150
        c = i % 7
        x = col_x[c]
        lines.append(f"{x},192,{t},1,0,0:0:0:0:")

    osu_content = "\n".join(lines) + "\n"
    osu_path = tmp_path / f"chart_{bpm}_{num_notes}.osu"
    osu_path.write_text(osu_content, encoding="utf-8")
    return osu_path


def create_sample_replay(
    tmp_path: Path,
    beatmap_path: Path,
    player_name: str = "PlayerA",
    duration_s: float = 40.0,
    completion_rate: float = 1.0,
    is_failed: bool = False,
    timestamp_sec: float = 1700000000.0,
) -> Path:
    beatmap_bytes = beatmap_path.read_bytes()
    md5_hash = hashlib.md5(beatmap_bytes).hexdigest()

    # Generate action frames
    num_frames = max(2, int(duration_s * 10))
    frames = []
    for i in range(num_frames):
        t_ms = (i / num_frames) * (duration_s * 1000.0)
        keys = (1 << (i % 7)) if i % 2 == 1 else 0
        frames.append(ReplayFrame(time_ms=t_ms, keys=keys))

    replay_hash = hashlib.md5(f"{player_name}_{duration_s}_{timestamp_sec}".encode()).hexdigest()

    replay = OSRReplay(
        mode=3,
        game_version=20240101,
        beatmap_hash=md5_hash,
        player_name=player_name,
        replay_hash=replay_hash,
        c300g=30,
        c300=10,
        c200=5,
        c100=0,
        c50=0,
        miss=2 if is_failed else 0,
        total_score=850000,
        max_combo=40,
        perfect=not is_failed,
        mods=0,
        life_bar="0|1.0,20000|0.0" if is_failed else "0|1.0,40000|1.0",
        timestamp_ticks=int(timestamp_sec * 10000000 + 621355968000000000),
        action_frames=frames,
    )

    osr_path = tmp_path / f"{player_name}_{replay_hash[:8]}.osr"
    osr_path.write_bytes(serialize_osr(replay))
    return osr_path


def test_sqlite_storage_lifecycle_and_multi_player_isolation(tmp_path: Path):
    db_path = tmp_path / "profiler.db"
    storage = ProfilerStorage(db_path=db_path)

    # 1. Store snapshots for PlayerA and PlayerB
    now = 1710000000.0
    snap_a1 = MatchSnapshot(
        player_name="PlayerA",
        timestamp=now - 100,
        beatmap_hash="hash_a1",
        replay_hash="rep_a1",
        play_duration_s=60.0,
        completion_rate=1.0,
        is_valid_play=True,
        is_failed=False,
        overall_ur=85.0,
        capacities={
            "stream": {"effective_capacity": 15.0, "star_rating": 7.0, "dan_tier": "7th Dan", "tested": True},
            "chordjack": {"effective_capacity": 12.0, "star_rating": 6.0, "dan_tier": "6th Dan", "tested": True},
        },
        fatal_time_ms=None,
        fatal_column=None,
        fatal_peak_strains={},
        summary={"total_notes": 100},
        created_at=now,
    )
    snap_a2 = MatchSnapshot(
        player_name="PlayerA",
        timestamp=now - 50,
        beatmap_hash="hash_a2",
        replay_hash="rep_a2",
        play_duration_s=75.0,
        completion_rate=1.0,
        is_valid_play=True,
        is_failed=False,
        overall_ur=78.0,
        capacities={
            "stream": {"effective_capacity": 17.5, "star_rating": 7.8, "dan_tier": "8th Dan", "tested": True},
            "chordjack": {"effective_capacity": 13.0, "star_rating": 6.3, "dan_tier": "6th Dan", "tested": True},
        },
        fatal_time_ms=None,
        fatal_column=None,
        fatal_peak_strains={},
        summary={"total_notes": 120},
        created_at=now,
    )
    snap_b1 = MatchSnapshot(
        player_name="PlayerB",
        timestamp=now - 30,
        beatmap_hash="hash_b1",
        replay_hash="rep_b1",
        play_duration_s=50.0,
        completion_rate=1.0,
        is_valid_play=True,
        is_failed=False,
        overall_ur=110.0,
        capacities={
            "stream": {"effective_capacity": 8.0, "star_rating": 4.5, "dan_tier": "4th Dan", "tested": True},
        },
        fatal_time_ms=None,
        fatal_column=None,
        fatal_peak_strains={},
        summary={"total_notes": 80},
        created_at=now,
    )

    id_a1 = storage.save_snapshot(snap_a1)
    id_a2 = storage.save_snapshot(snap_a2)
    id_b1 = storage.save_snapshot(snap_b1)

    assert id_a1 > 0
    assert id_a2 > id_a1
    assert id_b1 > id_a2

    # Query snapshots with player isolation
    snaps_a = storage.get_snapshots("PlayerA")
    assert len(snaps_a) == 2
    assert snaps_a[0].replay_hash == "rep_a2"  # Ordered by timestamp DESC
    assert snaps_a[1].replay_hash == "rep_a1"

    snaps_b = storage.get_snapshots("PlayerB")
    assert len(snaps_b) == 1
    assert snaps_b[0].player_name == "PlayerB"
    assert snaps_b[0].overall_ur == 110.0

    players = storage.list_players()
    assert set(players) == {"PlayerA", "PlayerB"}
    storage.close()


def test_noise_filter_discards_warmup_and_retry_spam():
    # Aborted run < 30s
    assert is_noise_match(duration_s=25.0, completion_rate=0.8, is_failed=False) is True
    # Aborted run < 50% completion
    assert is_noise_match(duration_s=45.0, completion_rate=0.45, is_failed=False) is True
    # Short warmup & low completion
    assert is_noise_match(duration_s=10.0, completion_rate=0.15, is_failed=False) is True

    # Valid completed run (>= 30s and >= 50%)
    assert is_noise_match(duration_s=35.0, completion_rate=0.55, is_failed=False) is False
    assert is_noise_match(duration_s=120.0, completion_rate=1.0, is_failed=False) is False

    # Failed runs are NEVER discarded as noise, regardless of duration or completion!
    assert is_noise_match(duration_s=15.0, completion_rate=0.25, is_failed=True) is False
    assert is_noise_match(duration_s=40.0, completion_rate=0.40, is_failed=True) is False


def test_noise_filtering_and_fatal_peak_preservation_in_storage(tmp_path: Path):
    db_path = tmp_path / "profiler_noise.db"
    storage = ProfilerStorage(db_path=db_path)

    now = 1710000000.0

    # 1. Retry run (<30s, not failed) -> Discarded by storage.save_snapshot_with_filter
    retry_snap = MatchSnapshot(
        player_name="Alice",
        timestamp=now - 200,
        beatmap_hash="map1",
        replay_hash="rep_retry",
        play_duration_s=15.0,
        completion_rate=0.3,
        is_valid_play=False,
        is_failed=False,
        overall_ur=95.0,
        capacities={},
        fatal_time_ms=None,
        fatal_column=None,
        fatal_peak_strains={},
        summary={},
        created_at=now,
    )
    saved_retry = storage.save_snapshot_with_filter(retry_snap)
    assert saved_retry is None

    # 2. Failed run (<30s, failed mid-song at fatal break) -> Preserved with fatal peak strains!
    failed_snap = MatchSnapshot(
        player_name="Alice",
        timestamp=now - 100,
        beatmap_hash="map2",
        replay_hash="rep_failed",
        play_duration_s=22.0,
        completion_rate=0.35,
        is_valid_play=False,
        is_failed=True,
        overall_ur=140.0,
        capacities={
            "chordjack": {"effective_capacity": 18.0, "star_rating": 8.0, "dan_tier": "8th Dan", "tested": True},
        },
        fatal_time_ms=21500.0,
        fatal_column=2,
        fatal_peak_strains={"chordjack": 19.5, "jack": 16.0},
        summary={},
        created_at=now,
    )
    saved_failed = storage.save_snapshot_with_filter(failed_snap)
    assert saved_failed is not None

    # 3. Valid completed run (60s, 100%) -> Preserved!
    clear_snap = MatchSnapshot(
        player_name="Alice",
        timestamp=now - 50,
        beatmap_hash="map3",
        replay_hash="rep_clear",
        play_duration_s=60.0,
        completion_rate=1.0,
        is_valid_play=True,
        is_failed=False,
        overall_ur=72.0,
        capacities={
            "stream": {"effective_capacity": 14.0, "star_rating": 6.8, "dan_tier": "6th Dan", "tested": True},
            "chordjack": {"effective_capacity": 15.0, "star_rating": 7.0, "dan_tier": "7th Dan", "tested": True},
        },
        fatal_time_ms=None,
        fatal_column=None,
        fatal_peak_strains={},
        summary={},
        created_at=now,
    )
    saved_clear = storage.save_snapshot_with_filter(clear_snap)
    assert saved_clear is not None

    # Verify Alice's snapshots: exactly 2 (the clear and the failed match; retry was discarded)
    alice_snaps = storage.get_snapshots("Alice")
    assert len(alice_snaps) == 2
    hashes = [s.replay_hash for s in alice_snaps]
    assert "rep_clear" in hashes
    assert "rep_failed" in hashes
    assert "rep_retry" not in hashes

    storage.close()


def test_rolling_temporal_window_aggregation_and_historical_trend(tmp_path: Path):
    db_path = tmp_path / "profiler_agg.db"
    storage = ProfilerStorage(db_path=db_path)

    now = 1710000000.0
    day_s = 86400.0

    # Old match (45 days ago): Stream 6.0★, Chordjack 5.5★, UR 80.0 (Cleared)
    storage.save_snapshot(
        MatchSnapshot(
            player_name="Bob",
            timestamp=now - 45 * day_s,
            beatmap_hash="old_map",
            replay_hash="rep_old",
            play_duration_s=80.0,
            completion_rate=1.0,
            is_valid_play=True,
            is_failed=False,
            overall_ur=80.0,
            capacities={
                "stream": {"effective_capacity": 12.0, "star_rating": 6.0, "dan_tier": "6th Dan", "tested": True},
                "chordjack": {"effective_capacity": 10.0, "star_rating": 5.5, "dan_tier": "5th Dan", "tested": True},
            },
            fatal_time_ms=None,
            fatal_column=None,
            fatal_peak_strains={},
            summary={},
            created_at=now,
        )
    )

    # Recent match 1 (10 days ago): Stream 7.5★, Chordjack 7.0★, UR 70.0 (Cleared)
    storage.save_snapshot(
        MatchSnapshot(
            player_name="Bob",
            timestamp=now - 10 * day_s,
            beatmap_hash="rec_map1",
            replay_hash="rep_rec1",
            play_duration_s=90.0,
            completion_rate=1.0,
            is_valid_play=True,
            is_failed=False,
            overall_ur=70.0,
            capacities={
                "stream": {"effective_capacity": 16.5, "star_rating": 7.5, "dan_tier": "7th Dan", "tested": True},
                "chordjack": {"effective_capacity": 15.0, "star_rating": 7.0, "dan_tier": "7th Dan", "tested": True},
            },
            fatal_time_ms=None,
            fatal_column=None,
            fatal_peak_strains={},
            summary={},
            created_at=now,
        )
    )

    # Recent match 2 (2 days ago): Failed run testing peak chordjack 8.2★ (Failed mid-song, UR 150.0)
    # Excluded from UR stability baseline, but included in peak strain capacity!
    storage.save_snapshot(
        MatchSnapshot(
            player_name="Bob",
            timestamp=now - 2 * day_s,
            beatmap_hash="rec_map2",
            replay_hash="rep_rec2",
            play_duration_s=40.0,
            completion_rate=0.45,
            is_valid_play=False,
            is_failed=True,
            overall_ur=150.0,
            capacities={
                "chordjack": {"effective_capacity": 19.0, "star_rating": 8.2, "dan_tier": "8th Dan", "tested": True},
            },
            fatal_time_ms=38000.0,
            fatal_column=3,
            fatal_peak_strains={"chordjack": 19.5},
            summary={},
            created_at=now,
        )
    )

    # 1. Query 30-day Recent Rolling Form (horizon_days=30)
    recent_profile: MacroProfile = aggregate_macro_profile(
        storage=storage,
        player_name="Bob",
        horizon_days=30.0,
        reference_timestamp=now,
        compare_against_all_time=True,
    )

    assert recent_profile.player_name == "Bob"
    assert recent_profile.window_mode == "recent_rolling"
    assert recent_profile.total_matches == 2  # Only 10 days ago and 2 days ago
    assert recent_profile.cleared_matches == 1
    assert recent_profile.failed_matches == 1

    # Stability baseline UR: only from cleared runs -> 70.0 (excludes failed 150.0)
    assert recent_profile.average_ur == pytest.approx(70.0, abs=0.1)

    # Capacities: Stream peak 7.5★, Chordjack peak 8.2★ (from the failed run)
    assert recent_profile.dimensions["stream"].star_rating == pytest.approx(7.5, abs=0.1)
    assert recent_profile.dimensions["chordjack"].star_rating == pytest.approx(8.2, abs=0.1)
    assert recent_profile.dominant_technique == "chordjack"

    # Trend comparison against all-time
    assert recent_profile.trend_comparison is not None
    # All-time chordjack peak is also 8.2 (since recent achieved new peak)
    assert recent_profile.trend_comparison["all_time_dominant"] == "chordjack"

    # 2. Query All-time Peak Profile (horizon_days=None)
    all_time_profile: MacroProfile = aggregate_macro_profile(
        storage=storage,
        player_name="Bob",
        horizon_days=None,
        reference_timestamp=now,
    )

    assert all_time_profile.window_mode == "all_time"
    assert all_time_profile.total_matches == 3  # 45d, 10d, 2d
    assert all_time_profile.cleared_matches == 2
    assert all_time_profile.failed_matches == 1
    # Average UR from the two cleared matches: (80.0 + 70.0) / 2 = 75.0
    assert all_time_profile.average_ur == pytest.approx(75.0, abs=0.1)
    assert all_time_profile.dimensions["chordjack"].star_rating == pytest.approx(8.2, abs=0.1)
    assert all_time_profile.dimensions["stream"].star_rating == pytest.approx(7.5, abs=0.1)

    storage.close()


def test_cli_batch_ingestion_and_profile_query(tmp_path: Path, capsys):
    db_file = tmp_path / "cli_test.db"

    # Prepare batch test data: 2 charts, 3 replays
    chart_dir = tmp_path / "charts"
    replay_dir = tmp_path / "replays"
    chart_dir.mkdir()
    replay_dir.mkdir()

    c1 = create_sample_chart(chart_dir, bpm=140.0, num_notes=40)
    c2 = create_sample_chart(chart_dir, bpm=180.0, num_notes=60)

    # Replay 1: PlayerX on c1, duration 40s (Valid clear)
    create_sample_replay(replay_dir, c1, player_name="PlayerX", duration_s=40.0, is_failed=False)
    # Replay 2: PlayerX on c2, duration 15s (Aborted retry -> noise)
    create_sample_replay(replay_dir, c2, player_name="PlayerX", duration_s=15.0, is_failed=False)
    # Replay 3: PlayerY on c1, duration 35s (Valid clear)
    create_sample_replay(replay_dir, c1, player_name="PlayerY", duration_s=35.0, is_failed=False)

    # 1. Run batch ingestion via CLI
    exit_code = main([
        "--batch-dir", str(replay_dir),
        "--beatmap-dir", str(chart_dir),
        "--db", str(db_file),
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Batch Replay Ingestion Completed" in captured.out
    assert "Saved:" in captured.out
    assert "2" in captured.out
    assert "Noise Filtered:" in captured.out

    # 2. Query 30-day Recent Rolling Form for PlayerX
    exit_code = main([
        "--player", "PlayerX",
        "--horizon-days", "30",
        "--db", str(db_file),
    ])
    assert exit_code == 0
    captured_player = capsys.readouterr()
    assert "PlayerX" in captured_player.out
    assert "Recent Rolling Form (30d)" in captured_player.out
    assert "Stability Baseline UR:" in captured_player.out

    # 3. Query All-Time Peak Profile for PlayerX with --json
    exit_code = main([
        "--player", "PlayerX",
        "--all-time",
        "--db", str(db_file),
        "--json",
    ])
    assert exit_code == 0
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert data["player_name"] == "PlayerX"
    assert data["window_mode"] == "all_time"
    assert data["total_matches"] == 1
    assert "dimensions" in data
    assert "overall_dan" in data
