"""
Tests for LazerSyncManager, Daemon and End-to-End Ingestion (Ticket 6 / SPEC-P2.3-03).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from proj7k.lazer.bridge import (
    BatchUpdateResult,
    BeatmapUpdatePayload,
    LazerBeatmapRecord,
    RealmBridgeClient,
)
from proj7k.lazer.backup import LazerBackupManager
from proj7k.lazer.daemon import (
    LazerSyncManager,
    SyncOptions,
    SyncSummary,
    resolve_beatmap_file,
)


def _make_osu_content(title="Test Song", version="Hard", mode=3, cs=7):
    return f"""osu file format v14

[General]
AudioFilename: audio.mp3
Mode: {mode}

[Metadata]
Title:{title}
Artist:Artist
Version:{version}

[Difficulty]
CircleSize:{cs}
OverallDifficulty:8

[HitObjects]
64,192,1000,1,0,0:0:0:0:
192,192,1200,1,0,0:0:0:0:
320,192,1400,1,0,0:0:0:0:
448,192,1600,1,0,0:0:0:0:
"""


def test_resolve_beatmap_file_sharded_and_flat(tmp_path: Path):
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    hash_hex = "a1b2c3d4e5f67890123456789012345678901234567890123456789012345678"
    sharded_dir = files_dir / hash_hex[:1] / hash_hex[:2]
    sharded_dir.mkdir(parents=True)
    sharded_file = sharded_dir / hash_hex
    sharded_file.write_text("dummy beatmap")

    rec = LazerBeatmapRecord(
        id="rec-1",
        hash=hash_hex,
        md5_hash="md5-1",
        file_hash=hash_hex,
        star_rating=3.0,
        difficulty_name="Hard",
        tags="",
        title="Title",
        artist="Artist",
        ruleset_id=3,
        circle_size=7.0,
    )

    resolved = resolve_beatmap_file(files_dir, rec)
    assert resolved == sharded_file


def test_sync_manager_sync_once_success(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    realm_file.write_text("realm database dummy")
    lock_file = tmp_path / "client.realm.lock"
    cache_dir = tmp_path / "cache"
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    # Put a real 7K osu file
    hash_hex = "abcdef1234567890"
    osu_file = files_dir / hash_hex
    osu_file.write_text(_make_osu_content())

    record = LazerBeatmapRecord(
        id="rec-1",
        hash=hash_hex,
        md5_hash="md5-rec-1",
        file_hash=hash_hex,
        star_rating=2.5,
        difficulty_name="Hard",
        tags="electronic",
        title="Test Song",
        artist="Artist",
        ruleset_id=3,
        circle_size=7.0,
    )

    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.dump_7k_beatmaps.return_value = [record]
    mock_bridge.apply_batch_update.return_value = BatchUpdateResult(success=True, updated_count=1)

    options = SyncOptions(
        realm_path=realm_file,
        files_dir=files_dir,
        cache_dir=cache_dir,
        lock_path=lock_file,
        auto_setup=False,
    )

    manager = LazerSyncManager(options=options, bridge_client=mock_bridge)
    summary = manager.sync_once()

    assert summary.success is True
    assert summary.total_7k == 1
    assert summary.evaluated_count == 1
    assert summary.updated_count == 1
    assert summary.error is None

    mock_bridge.apply_batch_update.assert_called_once()
    kwargs = mock_bridge.apply_batch_update.call_args.kwargs
    updates = kwargs["updates"]
    collections = kwargs["collections"]

    assert len(updates) == 1
    assert updates[0].id == "rec-1"
    assert "★" in updates[0].difficulty_name
    assert "dominant_" in updates[0].tags

    # Collections orchestrated
    assert len(collections) == 12
    assert "md5-rec-1" in collections["7K Tier: 00th-03rd (1★-4★)"] or "md5-rec-1" in collections["7K Tier: 04th-06th (4★-6★)"]

    # Backup state recorded
    backup_manager = manager.backup_manager
    states = backup_manager.load_backup_states()
    assert "rec-1" in states
    assert states["rec-1"].original_star_rating == 2.5
    assert states["rec-1"].original_difficulty_name == "Hard"


def test_sync_manager_game_running_blocks(tmp_path: Path):
    import fcntl
    realm_file = tmp_path / "client.realm"
    realm_file.write_text("realm dummy")
    lock_file = tmp_path / "client.realm.lock"
    lock_file.touch()
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    # Simulate game running holding lock
    with open(lock_file, "r+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            options = SyncOptions(
                realm_path=realm_file,
                files_dir=files_dir,
                cache_dir=tmp_path / "cache",
                lock_path=lock_file,
                auto_setup=False,
                lock_timeout_s=0.01,
            )

            manager = LazerSyncManager(options=options)
            summary = manager.sync_once()
            assert summary.success is False
            assert "lock" in summary.error.lower() or "safe flush" in summary.error.lower()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def test_sync_manager_revert_all(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    realm_file.write_text("realm dummy")
    lock_file = tmp_path / "client.realm.lock"
    cache_dir = tmp_path / "cache"

    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.revert_batch.return_value = BatchUpdateResult(success=True, updated_count=1)

    options = SyncOptions(
        realm_path=realm_file,
        files_dir=tmp_path / "files",
        cache_dir=cache_dir,
        lock_path=lock_file,
        auto_setup=False,
    )
    manager = LazerSyncManager(options=options, bridge_client=mock_bridge)

    # Seed a backup state
    manager.backup_manager.record_original_states([
        LazerBeatmapRecord("rec-1", "h", "m", "f", 3.0, "Hard", "", "T", "A", 3, 7.0)
    ])

    result = manager.revert_all()
    assert result.success is True
    assert result.updated_count == 1
    mock_bridge.revert_batch.assert_called_once()


def test_sync_manager_incremental_skips_unchanged(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    realm_file.write_text("realm dummy")
    lock_file = tmp_path / "client.realm.lock"
    cache_dir = tmp_path / "cache"
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    hash_hex = "abcdef1234567890"
    osu_file = files_dir / hash_hex
    osu_file.write_text(_make_osu_content())

    # Pre-evaluated and already annotated record
    from proj7k.difficulty import evaluate_intrinsic_difficulty
    diff_res = evaluate_intrinsic_difficulty(osu_file)
    sr = diff_res.star_rating
    dom_tech = diff_res.radar.dominant_technique

    from proj7k.lazer.annotator import format_injected_difficulty_name, inject_binned_skill_tags
    annotated_name = format_injected_difficulty_name("Hard", sr, dom_tech)
    annotated_tags = inject_binned_skill_tags("", dom_tech, sr)

    record = LazerBeatmapRecord(
        id="rec-1",
        hash=hash_hex,
        md5_hash="md5-rec-1",
        file_hash=hash_hex,
        star_rating=sr,
        difficulty_name=annotated_name,
        tags=annotated_tags,
        title="Test Song",
        artist="Artist",
        ruleset_id=3,
        circle_size=7.0,
    )

    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.dump_7k_beatmaps.return_value = [record]

    options = SyncOptions(
        realm_path=realm_file,
        files_dir=files_dir,
        cache_dir=cache_dir,
        lock_path=lock_file,
        auto_setup=False,
    )

    manager = LazerSyncManager(options=options, bridge_client=mock_bridge)
    summary = manager.sync_once()

    assert summary.success is True
    assert summary.total_7k == 1
    assert summary.evaluated_count == 0
    assert summary.skipped_count == 1
    assert summary.updated_count == 0
    # No write was necessary
    mock_bridge.apply_batch_update.assert_not_called()


def test_resolve_beatmap_file_variants(tmp_path: Path):
    nonexistent_dir = tmp_path / "nonexistent"
    rec = LazerBeatmapRecord("1", "hash1", "md5", "filehash", 3.0, "H", "", "T", "A", 3, 7.0)
    assert resolve_beatmap_file(nonexistent_dir, rec) is None

    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    # Flat match
    (files_dir / "hash1").write_text("flat content")
    assert resolve_beatmap_file(files_dir, rec) == files_dir / "hash1"

    # With .osu match
    rec2 = LazerBeatmapRecord("2", "hash2", "md5", "filehash2", 3.0, "H", "", "T", "A", 3, 7.0)
    (files_dir / "hash2.osu").write_text("osu content")
    assert resolve_beatmap_file(files_dir, rec2) == files_dir / "hash2.osu"

    # No match
    rec3 = LazerBeatmapRecord("3", "missing", "md5", "filehash3", 3.0, "H", "", "T", "A", 3, 7.0)
    assert resolve_beatmap_file(files_dir, rec3) is None


def test_sync_manager_paths_missing(tmp_path: Path):
    missing_realm = tmp_path / "missing.realm"
    missing_files = tmp_path / "missing_files"

    mgr1 = LazerSyncManager(options=SyncOptions(realm_path=missing_realm, files_dir=tmp_path))
    s1 = mgr1.sync_once()
    assert s1.success is False
    assert "database not found" in s1.error

    realm_file = tmp_path / "client.realm"
    realm_file.touch()
    mgr2 = LazerSyncManager(options=SyncOptions(realm_path=realm_file, files_dir=missing_files))
    s2 = mgr2.sync_once()
    assert s2.success is False
    assert "files directory not found" in s2.error


def test_sync_manager_setup_and_dump_exceptions(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    realm_file.touch()
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    # Setup error
    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.ensure_installed.side_effect = RuntimeError("Setup failed")
    mgr = LazerSyncManager(
        options=SyncOptions(realm_path=realm_file, files_dir=files_dir, auto_setup=True),
        bridge_client=mock_bridge,
    )
    res = mgr.sync_once()
    assert res.success is False
    assert "Setup failed" in res.error

    # Dump error
    mock_bridge.ensure_installed.side_effect = None
    mock_bridge.dump_7k_beatmaps.side_effect = RuntimeError("Dump failed")
    res2 = mgr.sync_once()
    assert res2.success is False
    assert "Failed to dump 7K beatmaps" in res2.error


def test_sync_manager_empty_or_non_7k(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    realm_file.touch()
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    # 4K map
    record_4k = LazerBeatmapRecord("1", "h", "m", "f", 3.0, "Hard", "", "T", "A", 3, 4.0)
    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.dump_7k_beatmaps.return_value = [record_4k]

    mgr = LazerSyncManager(
        options=SyncOptions(realm_path=realm_file, files_dir=files_dir, auto_setup=False),
        bridge_client=mock_bridge,
    )
    res = mgr.sync_once()
    assert res.success is True
    assert res.total_7k == 0


def test_sync_manager_missing_osu_and_corrupt_osu(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    realm_file.touch()
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    # 1 missing file
    rec_missing = LazerBeatmapRecord("1", "nohash", "m1", "nohash", 3.0, "Hard", "", "T", "A", 3, 7.0)
    # 1 corrupt file
    (files_dir / "corrupt").write_text("corrupted file")
    rec_corrupt = LazerBeatmapRecord("2", "corrupt", "m2", "corrupt", 3.0, "Hard", "", "T", "A", 3, 7.0)

    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.dump_7k_beatmaps.return_value = [rec_missing, rec_corrupt]

    mgr = LazerSyncManager(
        options=SyncOptions(realm_path=realm_file, files_dir=files_dir, auto_setup=False),
        bridge_client=mock_bridge,
    )
    res = mgr.sync_once()
    assert res.success is True
    assert res.total_7k == 2
    assert res.failed_count == 2
    assert res.evaluated_count == 0


def test_sync_manager_apply_batch_failure(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    realm_file.touch()
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    hash_hex = "good_osu"
    (files_dir / hash_hex).write_text(_make_osu_content())
    rec = LazerBeatmapRecord("1", hash_hex, "m", hash_hex, 2.0, "Hard", "", "T", "A", 3, 7.0)

    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.dump_7k_beatmaps.return_value = [rec]
    mock_bridge.apply_batch_update.return_value = BatchUpdateResult(success=False, error="DB write error")

    mgr = LazerSyncManager(
        options=SyncOptions(realm_path=realm_file, files_dir=files_dir, auto_setup=False),
        bridge_client=mock_bridge,
    )
    res = mgr.sync_once()
    assert res.success is False
    assert "Database update failed: DB write error" in res.error


def test_sync_manager_revert_all_lock_busy(tmp_path: Path):
    import fcntl
    realm_file = tmp_path / "client.realm"
    realm_file.touch()
    lock_file = tmp_path / "client.realm.lock"
    lock_file.touch()

    with open(lock_file, "r+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            mgr = LazerSyncManager(
                options=SyncOptions(
                    realm_path=realm_file,
                    lock_path=lock_file,
                    lock_timeout_s=0.01,
                    auto_setup=False,
                )
            )
            result = mgr.revert_all()
            assert result.success is False
            assert "Safe flush window is closed" in result.error
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def test_daemon_loop_logging_branches():
    from proj7k.lazer.daemon import LazerDaemon, run_daemon
    import threading

    stop_event = threading.Event()
    call_count = 0

    def mock_sync_once(wait_for_lock=False):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return SyncSummary(success=False, error="Safe flush window is closed: lock busy")
        elif call_count == 2:
            return SyncSummary(success=False, error="Random failure")
        else:
            stop_event.set()
            return SyncSummary(success=True, total_7k=1, updated_count=0, skipped_count=1)

    mock_mgr = MagicMock()
    mock_mgr.sync_once.side_effect = mock_sync_once

    run_daemon(manager=mock_mgr, interval_s=0.01, stop_event=stop_event)

    assert call_count >= 3


def test_sync_manager_incremental_preserves_existing_collections(tmp_path: Path):
    """
    Verify that when 1 map is already annotated and 1 map is new,
    updates only contain the new map, but collections contain BOTH maps.
    """
    realm_file = tmp_path / "client.realm"
    realm_file.touch()
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    # Map 1: already annotated
    rec_old = LazerBeatmapRecord(
        id="rec-old",
        hash="hash-old",
        md5_hash="md5-old",
        file_hash="hash-old",
        star_rating=5.50,
        difficulty_name="Hard (5.50★ 5th Jack)",
        tags="dominant_jack jack_5★ dan_5th",
        title="Old Song",
        artist="Artist",
        ruleset_id=3,
        circle_size=7.0,
    )

    # Map 2: brand new, unannotated
    hash_new = "hash-new"
    (files_dir / hash_new).write_text(_make_osu_content(title="New Song"))
    rec_new = LazerBeatmapRecord(
        id="rec-new",
        hash=hash_new,
        md5_hash="md5-new",
        file_hash=hash_new,
        star_rating=2.0,
        difficulty_name="Normal",
        tags="",
        title="New Song",
        artist="Artist",
        ruleset_id=3,
        circle_size=7.0,
    )

    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.dump_7k_beatmaps.return_value = [rec_old, rec_new]
    mock_bridge.apply_batch_update.return_value = BatchUpdateResult(success=True, updated_count=1)

    mgr = LazerSyncManager(
        options=SyncOptions(realm_path=realm_file, files_dir=files_dir, auto_setup=False),
        bridge_client=mock_bridge,
    )
    res = mgr.sync_once()

    assert res.success is True
    assert res.total_7k == 2
    assert res.skipped_count == 1
    assert res.updated_count == 1

    # Check payloads passed to apply_batch_update
    mock_bridge.apply_batch_update.assert_called_once()
    kwargs = mock_bridge.apply_batch_update.call_args.kwargs
    updates = kwargs["updates"]
    collections = kwargs["collections"]

    # Only rec-new was modified
    assert len(updates) == 1
    assert updates[0].id == "rec-new"

    # Both maps are present in collections!
    assert "md5-old" in collections["7K Jack"]
    assert "md5-old" in collections["7K Tier: 04th-06th (4★-6★)"]
    # rec-new is also in its respective collections
    found_new = any("md5-new" in hashes for hashes in collections.values())
    assert found_new is True


def test_sync_manager_preheats_when_locked(tmp_path: Path):
    """
    User symptom 3 (ADR-0009): While osu! is running and lock is held,
    sync_once with preheat_on_locked=True should still read beatmaps in read-only mode
    and preheat evaluation into cache, returning success=False (window closed) but evaluated_count > 0.
    """
    import fcntl
    realm_file = tmp_path / "client.realm"
    realm_file.touch()
    lock_file = tmp_path / "client.realm.lock"
    lock_file.touch()
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True)

    # 1 new 7K beatmap
    h = "abc12345"
    osu_file = files_dir / h
    osu_file.write_text(
        "osu file format v14\n[General]\nMode: 3\n[Difficulty]\nCircleSize: 7\n[HitObjects]\n"
        "64,192,1000,1,0,0:0:0:0:\n"
    )
    rec = LazerBeatmapRecord(
        id="rec-1",
        hash=h,
        md5_hash="md5-1",
        file_hash=h,
        star_rating=1.0,
        difficulty_name="Normal",
        tags="",
        title="Title",
        artist="Artist",
        ruleset_id=3,
        circle_size=7.0,
    )

    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.dump_7k_beatmaps.return_value = [rec]

    # Hold the lock
    with open(lock_file, "r+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            opts = SyncOptions(
                realm_path=realm_file,
                files_dir=files_dir,
                cache_dir=tmp_path / "cache",
                lock_path=lock_file,
                auto_setup=False,
            )
            manager = LazerSyncManager(options=opts, bridge_client=mock_bridge)
            summary = manager.sync_once(preheat_on_locked=True)

            assert summary.success is False
            assert "Safe flush window is closed" in summary.error
            assert summary.evaluated_count == 1
            # Batch update was NOT called because window was closed
            mock_bridge.apply_batch_update.assert_not_called()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)





