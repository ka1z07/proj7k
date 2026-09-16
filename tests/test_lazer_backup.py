"""
Tests for Disaster Recovery Snapshots and State Backup Manager (Ticket 6 / SPEC-P2.3-03).
"""

from pathlib import Path
import time
from unittest.mock import MagicMock
import pytest

from proj7k.lazer.bridge import (
    BatchUpdateResult,
    LazerBeatmapRecord,
    RealmBridgeClient,
)
from proj7k.lazer.annotator import ALL_BIAXIAL_COLLECTIONS
from proj7k.lazer.backup import (
    BeatmapBackupState,
    LazerBackupManager,
)


def _make_dummy_record(id_str: str, sr: float = 3.5, diff: str = "Normal", tags: str = "orig"):
    return LazerBeatmapRecord(
        id=id_str,
        hash="h-" + id_str,
        md5_hash="m-" + id_str,
        file_hash="f-" + id_str,
        star_rating=sr,
        difficulty_name=diff,
        tags=tags,
        title="Title",
        artist="Artist",
        ruleset_id=3,
        circle_size=7.0,
    )


def test_create_realm_snapshot_rotation(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    realm_file.write_text("dummy realm content")

    cache_dir = tmp_path / "cache"
    manager = LazerBackupManager(realm_path=realm_file, cache_dir=cache_dir, max_snapshots=3)

    # 1. First snapshot
    s1 = manager.create_realm_snapshot()
    assert s1 is not None and s1.exists()
    time.sleep(0.01)

    # 2. Second snapshot
    s2 = manager.create_realm_snapshot()
    assert s2 is not None and s2.exists()
    time.sleep(0.01)

    # 3. Third snapshot
    s3 = manager.create_realm_snapshot()
    assert s3 is not None and s3.exists()
    time.sleep(0.01)

    # 4. Fourth snapshot triggers rotation: oldest (s1) should be removed
    s4 = manager.create_realm_snapshot()
    assert s4 is not None and s4.exists()

    snapshots = manager.list_snapshots()
    assert len(snapshots) == 3
    assert s1 not in snapshots
    assert s2 in snapshots
    assert s3 in snapshots
    assert s4 in snapshots


def test_create_realm_snapshot_non_existent(tmp_path: Path):
    manager = LazerBackupManager(realm_path=tmp_path / "non_existent.realm", cache_dir=tmp_path / "cache")
    assert manager.create_realm_snapshot() is None


def test_record_original_states_first_write_protection(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    cache_dir = tmp_path / "cache"
    manager = LazerBackupManager(realm_path=realm_file, cache_dir=cache_dir)

    r1 = _make_dummy_record("id-1", sr=3.0, diff="Normal", tags="tag1")
    r2 = _make_dummy_record("id-2", sr=4.5, diff="Hard", tags="tag2")

    # Initial record
    count = manager.record_original_states([r1, r2])
    assert count == 2

    states = manager.load_backup_states()
    assert len(states) == 2
    assert states["id-1"].original_star_rating == 3.0
    assert states["id-1"].original_difficulty_name == "Normal"
    assert states["id-2"].original_star_rating == 4.5

    # Second record with MUTATED data for id-1: MUST NOT overwrite existing state (first-write-protection)
    r1_mutated = _make_dummy_record("id-1", sr=6.0, diff="Normal (6.00★ Jack)", tags="dominant_jack")
    r3 = _make_dummy_record("id-3", sr=5.0, diff="Insane", tags="tag3")

    count2 = manager.record_original_states([r1_mutated, r3])
    assert count2 == 1  # Only id-3 added

    states2 = manager.load_backup_states()
    assert len(states2) == 3
    # Pristine original preserved
    assert states2["id-1"].original_star_rating == 3.0
    assert states2["id-1"].original_difficulty_name == "Normal"
    assert states2["id-3"].original_star_rating == 5.0


def test_revert_realm_modifications(tmp_path: Path):
    realm_file = tmp_path / "client.realm"
    cache_dir = tmp_path / "cache"
    manager = LazerBackupManager(realm_path=realm_file, cache_dir=cache_dir)

    r1 = _make_dummy_record("id-1", sr=3.0, diff="Normal", tags="tag1")
    manager.record_original_states([r1])

    mock_bridge = MagicMock(spec=RealmBridgeClient)
    mock_bridge.revert_batch.return_value = BatchUpdateResult(success=True, updated_count=1)

    result = manager.revert_realm_modifications(mock_bridge)
    assert result.success is True
    assert result.updated_count == 1

    mock_bridge.revert_batch.assert_called_once()
    kwargs = mock_bridge.revert_batch.call_args.kwargs
    revert_payloads = kwargs["reverts"]
    assert len(revert_payloads) == 1
    assert revert_payloads[0].id == "id-1"
    assert revert_payloads[0].star_rating == 3.0
    assert revert_payloads[0].difficulty_name == "Normal"

    # Collections cleaned should match all 12 biaxial collections
    assert set(kwargs["collections_to_clean"]) == set(ALL_BIAXIAL_COLLECTIONS)


    # After successful revert, backup states are cleared
    assert len(manager.load_backup_states()) == 0


def test_revert_realm_modifications_empty(tmp_path: Path):
    manager = LazerBackupManager(realm_path=tmp_path / "client.realm", cache_dir=tmp_path / "cache")
    mock_bridge = MagicMock(spec=RealmBridgeClient)
    result = manager.revert_realm_modifications(mock_bridge)
    assert result.success is True
    assert result.updated_count == 0
    mock_bridge.revert_batch.assert_not_called()


def test_list_snapshots_non_existent_parent(tmp_path: Path):
    manager = LazerBackupManager(realm_path=tmp_path / "non_existent_dir" / "client.realm", cache_dir=tmp_path / "cache")
    assert manager.list_snapshots() == []


def test_load_backup_states_corrupt_json(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    state_file = cache_dir / "lazer_backup_state.json"
    state_file.write_text("invalid json content")

    manager = LazerBackupManager(realm_path=tmp_path / "client.realm", cache_dir=cache_dir)
    assert manager.load_backup_states() == {}


def test_clear_backup_states_os_error(tmp_path: Path):
    from unittest.mock import patch
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    state_file = cache_dir / "lazer_backup_state.json"
    state_file.write_text("{}")

    manager = LazerBackupManager(realm_path=tmp_path / "client.realm", cache_dir=cache_dir)
    with patch.object(Path, "unlink", side_effect=OSError("Permission error")):
        manager.clear_backup_states()  # does not throw

