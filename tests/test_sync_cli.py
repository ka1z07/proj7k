"""
Tests for proj7k.sync CLI entrypoint and LazerDaemon loop.

SPEC-P2.3-03 / ADR-0009.
"""

from pathlib import Path
import signal
import threading
import time
from unittest.mock import MagicMock, patch
import pytest

from proj7k.lazer.bridge import BatchUpdateResult
from proj7k.lazer.daemon import LazerDaemon, SyncOptions, SyncSummary
from proj7k.sync import main, run_daemon


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "osu!lazer database synchronization" in captured.out


def test_cli_setup_success():
    with patch("proj7k.sync.RealmBridgeClient") as mock_bridge_cls:
        instance = mock_bridge_cls.return_value
        instance.ensure_installed.return_value = None

        code = main(["--setup"])
        assert code == 0
        instance.ensure_installed.assert_called_once()


def test_cli_setup_failure(capsys):
    with patch("proj7k.sync.RealmBridgeClient") as mock_bridge_cls:
        instance = mock_bridge_cls.return_value
        instance.ensure_installed.side_effect = RuntimeError("Node.js not found")

        code = main(["--setup"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Setup failed" in captured.err or "Setup failed" in captured.out


def test_cli_revert_success(tmp_path):
    realm_path = tmp_path / "client.realm"
    with patch("proj7k.sync.LazerSyncManager") as mock_mgr_cls:
        instance = mock_mgr_cls.return_value
        instance.revert_all.return_value = BatchUpdateResult(success=True, updated_count=5)

        code = main(["--revert", "--realm", str(realm_path)])
        assert code == 0
        instance.revert_all.assert_called_once()


def test_cli_revert_failure(tmp_path, capsys):
    realm_path = tmp_path / "client.realm"
    with patch("proj7k.sync.LazerSyncManager") as mock_mgr_cls:
        instance = mock_mgr_cls.return_value
        instance.revert_all.return_value = BatchUpdateResult(
            success=False, error="Revert failed: database locked"
        )

        code = main(["--revert", "--realm", str(realm_path)])
        assert code == 1
        captured = capsys.readouterr()
        assert "Revert failed" in captured.err or "Revert failed" in captured.out


def test_cli_once_success(tmp_path, capsys):
    realm_path = tmp_path / "client.realm"
    files_dir = tmp_path / "files"
    with patch("proj7k.sync.LazerSyncManager") as mock_mgr_cls:
        instance = mock_mgr_cls.return_value
        instance.sync_once.return_value = SyncSummary(
            success=True,
            total_7k=10,
            evaluated_count=2,
            updated_count=2,
            skipped_count=8,
            snapshot_path=str(tmp_path / "backup_123"),
        )

        code = main(["--once", "--realm", str(realm_path), "--files-dir", str(files_dir)])
        assert code == 0
        instance.sync_once.assert_called_once_with(wait_for_lock=False)
        captured = capsys.readouterr()
        assert "Sync completed successfully" in captured.out
        assert "Updated: 2" in captured.out


def test_cli_once_with_wait(tmp_path):
    realm_path = tmp_path / "client.realm"
    with patch("proj7k.sync.LazerSyncManager") as mock_mgr_cls:
        instance = mock_mgr_cls.return_value
        instance.sync_once.return_value = SyncSummary(
            success=True,
            total_7k=5,
            updated_count=1,
        )

        code = main(["--once", "--wait", "--realm", str(realm_path)])
        assert code == 0
        instance.sync_once.assert_called_once_with(wait_for_lock=True)


def test_cli_once_failure(tmp_path, capsys):
    realm_path = tmp_path / "client.realm"
    with patch("proj7k.sync.LazerSyncManager") as mock_mgr_cls:
        instance = mock_mgr_cls.return_value
        instance.sync_once.return_value = SyncSummary(
            success=False,
            error="Safe flush window is closed",
        )

        code = main(["--once", "--realm", str(realm_path)])
        assert code == 1
        captured = capsys.readouterr()
        assert "Sync failed" in captured.err or "Sync failed" in captured.out


def test_cli_default_action_is_once(tmp_path):
    realm_path = tmp_path / "client.realm"
    with patch("proj7k.sync.LazerSyncManager") as mock_mgr_cls:
        instance = mock_mgr_cls.return_value
        instance.sync_once.return_value = SyncSummary(success=True)

        code = main(["--realm", str(realm_path)])
        assert code == 0
        instance.sync_once.assert_called_once()


def test_lazer_daemon_run_and_stop():
    mock_manager = MagicMock()
    mock_manager.sync_once.return_value = SyncSummary(
        success=True, total_7k=3, updated_count=1, skipped_count=2
    )

    daemon = LazerDaemon(manager=mock_manager, interval_s=0.01)
    stop_event = threading.Event()

    # Trigger stop after a short interval
    def stop_later():
        time.sleep(0.05)
        stop_event.set()

    t = threading.Thread(target=stop_later)
    t.start()

    daemon.run(stop_event=stop_event)
    t.join()

    assert mock_manager.sync_once.call_count >= 1


def test_lazer_daemon_stop_method():
    mock_manager = MagicMock()
    mock_manager.sync_once.return_value = SyncSummary(success=True)

    daemon = LazerDaemon(manager=mock_manager, interval_s=0.01)

    def stop_daemon():
        time.sleep(0.05)
        daemon.stop()

    t = threading.Thread(target=stop_daemon)
    t.start()

    daemon.run()
    t.join()

    assert mock_manager.sync_once.call_count >= 1


def test_cli_daemon_invocation():
    with patch("proj7k.sync.run_daemon") as mock_run_daemon:
        code = main(["--daemon", "--interval", "2.5", "--verbose"])
        assert code == 0
        mock_run_daemon.assert_called_once()
        args, kwargs = mock_run_daemon.call_args
        assert kwargs.get("interval_s") == 2.5 or (len(args) >= 2 and args[1] == 2.5)
