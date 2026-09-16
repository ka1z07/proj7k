"""
Tests for POSIX File Lock Probe and Safe Flush Window (Ticket 6 / SPEC-P2.3-03).
"""

import fcntl
import os
from pathlib import Path
import threading
import time
import pytest

from proj7k.lazer.lock import (
    LockBusyError,
    probe_realm_lock,
    SafeFlushWindow,
)


def test_probe_realm_lock_unlocked(tmp_path: Path):
    lock_file = tmp_path / "client.realm.lock"
    assert not lock_file.exists()
    # Should create file and return True
    assert probe_realm_lock(lock_file) is True
    assert lock_file.exists()


def test_probe_realm_lock_locked(tmp_path: Path):
    lock_file = tmp_path / "client.realm.lock"
    lock_file.touch()

    # Simulate game holding lock
    with open(lock_file, "r+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            # Second probe from separate file handle should report False (busy)
            assert probe_realm_lock(lock_file) is False
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    # After unlock, probe should report True
    assert probe_realm_lock(lock_file) is True


def test_safe_flush_window_success(tmp_path: Path):
    lock_file = tmp_path / "client.realm.lock"
    with SafeFlushWindow(lock_file) as window:
        assert window.is_acquired is True
        # While inside, external probe sees it as locked
        assert probe_realm_lock(lock_file) is False

    # After context exits, lock is freed
    assert probe_realm_lock(lock_file) is True


def test_safe_flush_window_busy_raises(tmp_path: Path):
    lock_file = tmp_path / "client.realm.lock"
    with open(lock_file, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(LockBusyError):
                with SafeFlushWindow(lock_file, timeout_s=0.05, raise_on_busy=True):
                    pass
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def test_safe_flush_window_busy_no_raise(tmp_path: Path):
    lock_file = tmp_path / "client.realm.lock"
    with open(lock_file, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with SafeFlushWindow(lock_file, timeout_s=0.0, raise_on_busy=False) as window:
                assert window.is_acquired is False
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def test_safe_flush_window_timeout_and_acquire(tmp_path: Path):
    lock_file = tmp_path / "client.realm.lock"

    def release_after_delay():
        with open(lock_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            time.sleep(0.1)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    t = threading.Thread(target=release_after_delay)
    t.start()
    time.sleep(0.02)

    # Window with 0.5s timeout should wait and acquire successfully
    with SafeFlushWindow(lock_file, timeout_s=0.5, retry_interval_s=0.02) as window:
        assert window.is_acquired is True

    t.join()


def test_probe_realm_lock_os_error(tmp_path: Path):
    from unittest.mock import patch
    with patch("builtins.open", side_effect=OSError("Permission denied")):
        assert probe_realm_lock(tmp_path / "client.realm.lock") is False


def test_safe_flush_window_unlock_os_error(tmp_path: Path):
    from unittest.mock import patch
    lock_file = tmp_path / "client.realm.lock"
    window = SafeFlushWindow(lock_file)
    window.__enter__()
    assert window.is_acquired is True
    with patch("fcntl.flock", side_effect=OSError("Disk detached")):
        window.__exit__(None, None, None)
    assert window.is_acquired is False


