"""
Tests for RealmBridgeClient in proj7k.lazer.bridge.
"""

from pathlib import Path
import json
from unittest.mock import MagicMock, patch
import pytest

from proj7k.lazer.bridge import (
    RealmBridgeClient,
    LazerBeatmapRecord,
    BeatmapMutationPayload,
    BeatmapUpdatePayload,
    BeatmapRevertPayload,
    BatchUpdateResult,
    BridgeEnvironmentStatus,
    RealmBridgeError,
)


def test_data_models_contracts():
    record = LazerBeatmapRecord(
        id="a1b2c3d4-e5f6-7890-1234-56789abcdef0",
        hash="sha256hash123",
        md5_hash="md5hash123",
        file_hash="filehash123",
        star_rating=5.42,
        difficulty_name="Insane",
        tags="electronic instrumental",
        title="Test Song",
        artist="Test Artist",
        ruleset_id=3,
        circle_size=7.0,
    )
    assert record.id == "a1b2c3d4-e5f6-7890-1234-56789abcdef0"
    assert record.file_hash == "filehash123"
    assert record.is_7k_mania is True

    update = BeatmapUpdatePayload(
        id="a1b2c3d4-e5f6-7890-1234-56789abcdef0",
        star_rating=6.12,
        difficulty_name="Insane (6.12★ Jack)",
        tags="electronic instrumental dominant_jack jack_6★",
    )
    assert isinstance(update, BeatmapMutationPayload)
    assert update.to_dict()["star_rating"] == 6.12

    revert = BeatmapRevertPayload(
        id="a1b2c3d4-e5f6-7890-1234-56789abcdef0",
        star_rating=5.42,
        difficulty_name="Insane",
        tags="electronic instrumental",
    )
    assert isinstance(revert, BeatmapMutationPayload)
    assert revert.to_dict()["difficulty_name"] == "Insane"


def test_check_environment_detected():
    client = RealmBridgeClient()
    with patch("shutil.which") as mock_which, patch("pathlib.Path.exists") as mock_exists:
        mock_which.side_effect = lambda cmd: f"/usr/local/bin/{cmd}"
        mock_exists.return_value = True

        status = client.check_environment()
        assert status.has_node is True
        assert status.has_npm is True
        assert status.has_realm_dependency is True
        assert status.is_ready is True


def test_ensure_installed_triggers_npm():
    client = RealmBridgeClient()
    mock_status_unready = BridgeEnvironmentStatus(
        has_node=True,
        has_npm=True,
        has_realm_dependency=False,
        bridge_script_exists=True,
        node_path="/usr/local/bin/node",
        npm_path="/usr/local/bin/npm",
    )
    mock_status_ready = BridgeEnvironmentStatus(
        has_node=True,
        has_npm=True,
        has_realm_dependency=True,
        bridge_script_exists=True,
        node_path="/usr/local/bin/node",
        npm_path="/usr/local/bin/npm",
    )

    with patch.object(client, "check_environment", side_effect=[mock_status_unready, mock_status_ready]), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("subprocess.run") as mock_run:
        mock_proc = MagicMock(returncode=0)
        mock_run.return_value = mock_proc

        result = client.ensure_installed()
        assert result is True
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["/usr/local/bin/npm", "install"]


def test_dump_7k_beatmaps_success():
    client = RealmBridgeClient()
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({
        "success": True,
        "beatmaps": [
            {
                "id": "guid-1",
                "hash": "hash-1",
                "md5_hash": "md5-1",
                "file_hash": "file-1",
                "star_rating": 4.5,
                "difficulty_name": "Hard",
                "tags": "tags1",
                "title": "Song1",
                "artist": "Artist1",
                "ruleset_id": 3,
                "circle_size": 7.0,
            }
        ]
    })
    mock_proc.stderr = ""

    with patch.object(client, "ensure_installed", return_value=True), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        records = client.dump_7k_beatmaps(realm_path=Path("/tmp/client.realm"), auto_setup=False)
        assert len(records) == 1
        assert records[0].id == "guid-1"
        assert records[0].file_hash == "file-1"
        assert records[0].difficulty_name == "Hard"
        assert records[0].star_rating == 4.5
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "dump-7k" in cmd
        assert "--realm" in cmd


def test_dump_collections_success():
    client = RealmBridgeClient()
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({
        "success": True,
        "collections": {
            "7K Jack": ["md5-1", "md5-2"],
            "7K Practice": ["md5-p1"],
        },
    })
    mock_proc.stderr = ""

    with patch.object(client, "ensure_installed", return_value=True), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        cols = client.dump_collections(realm_path=Path("/tmp/client.realm"), auto_setup=False)
        assert "7K Jack" in cols
        assert "7K Practice" in cols
        assert cols["7K Practice"] == ["md5-p1"]
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "dump-collections" in cmd
        assert "--realm" in cmd


def test_apply_batch_update_success():
    client = RealmBridgeClient()
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({"success": True, "updated_count": 2})
    mock_proc.stderr = ""

    updates = [
        BeatmapUpdatePayload("id-1", 6.5, "Hard (6.50★ Jack)", "tag1"),
        BeatmapUpdatePayload("id-2", 7.2, "Insane (7.20★ Tech)", "tag2"),
    ]
    collections = {"7K Jack": ["md5-1"], "7K Tech": ["md5-2"]}

    with patch.object(client, "ensure_installed", return_value=True), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        result = client.apply_batch_update(updates, collections=collections, realm_path=Path("/tmp/client.realm"), auto_setup=False)
        assert result.success is True
        assert result.updated_count == 2
        assert result.error is None
        mock_run.assert_called_once()
        kwargs = mock_run.call_args[1]
        payload = json.loads(kwargs["input"])
        assert len(payload["updates"]) == 2
        assert payload["collections"]["7K Jack"] == ["md5-1"]


def test_revert_batch_success():
    client = RealmBridgeClient()
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({"success": True, "updated_count": 1})
    mock_proc.stderr = ""

    reverts = [BeatmapRevertPayload("id-1", 4.5, "Hard", "tag1")]
    collections_to_clean = ["7K Jack"]

    with patch.object(client, "ensure_installed", return_value=True), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        result = client.revert_batch(reverts, collections_to_clean=collections_to_clean, realm_path=Path("/tmp/client.realm"), auto_setup=False)
        assert result.success is True
        assert result.updated_count == 1
        mock_run.assert_called_once()
        kwargs = mock_run.call_args[1]
        payload = json.loads(kwargs["input"])
        assert len(payload["reverts"]) == 1
        assert payload["collections_to_clean"] == ["7K Jack"]


def test_dump_7k_error_handling():
    client = RealmBridgeClient()
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = json.dumps({"success": False, "error": "Database locked"})
    mock_proc.stderr = "Error: Database locked"

    with patch.object(client, "ensure_installed", return_value=True), \
         patch("subprocess.run", return_value=mock_proc):
        with pytest.raises(Exception) as exc_info:
            client.dump_7k_beatmaps(realm_path=Path("/tmp/client.realm"), auto_setup=False)
        assert "Database locked" in str(exc_info.value)


def test_command_not_found():
    client = RealmBridgeClient()
    with patch.object(client, "ensure_installed", return_value=True), \
         patch("subprocess.run", side_effect=FileNotFoundError("node not found")):
        with pytest.raises(Exception) as exc_info:
            client.dump_7k_beatmaps(realm_path=Path("/tmp/client.realm"), auto_setup=False)
        assert "Command not found" in str(exc_info.value)
