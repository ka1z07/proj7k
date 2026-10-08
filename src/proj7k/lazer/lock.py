"""
File Lock Probe and Safe Flush Window for osu!lazer client.realm.

Implements SPEC-P2.3-03 / ADR-0009.

Realm Core holds a shared lock on client.realm.lock for as long as any process has the
database open: flock() on macOS and Linux, LockFileEx() on the first byte on Windows. Taking
the matching exclusive lock without blocking therefore succeeds only while osu!lazer is closed.
"""

import os
import sys
from pathlib import Path
import time
from typing import Optional, Union


if sys.platform == "win32":
    import msvcrt

    def _try_exclusive_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_exclusive_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class LockBusyError(RuntimeError):
    """Exception raised when the osu!lazer database lock is occupied by the game process."""
    pass


class SafeFlushWindow:
    """
    Context manager that acquires and holds an exclusive lock on client.realm.lock.
    Ensures safe atomic write transactions without conflicting with a running osu!lazer instance.
    """

    def __init__(
        self,
        lock_path: Union[str, Path],
        timeout_s: float = 0.0,
        retry_interval_s: float = 0.05,
        raise_on_busy: bool = True,
    ):
        self.lock_path = Path(lock_path)
        self.timeout_s = max(0.0, float(timeout_s))
        self.retry_interval_s = max(0.001, float(retry_interval_s))
        self.raise_on_busy = raise_on_busy
        self.is_acquired: bool = False
        self._file: Optional[object] = None

    def __enter__(self) -> "SafeFlushWindow":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._file = open(self.lock_path, "a+")
        except OSError:
            if self.raise_on_busy:
                raise LockBusyError(f"Cannot open lock file at '{self.lock_path}'")
            self.is_acquired = False
            return self


        deadline = time.time() + self.timeout_s
        while True:
            try:
                _try_exclusive_lock(self._file.fileno())
                self.is_acquired = True
                return self
            except (BlockingIOError, OSError):
                if time.time() >= deadline:
                    break
                remaining = deadline - time.time()
                time.sleep(min(self.retry_interval_s, remaining))

        # Failed to acquire within timeout
        if self._file:
            self._file.close()
            self._file = None

        if self.raise_on_busy:
            raise LockBusyError(
                f"osu!lazer database lock at '{self.lock_path}' is held by the game. "
                "Safe flush window is closed."
            )

        self.is_acquired = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file and self.is_acquired:
            try:
                _unlock(self._file.fileno())
            except OSError:
                pass
            finally:
                self._file.close()
                self._file = None
                self.is_acquired = False


def probe_realm_lock(lock_path: Union[str, Path]) -> bool:
    """
    Non-blocking probe of the osu!lazer database lock.
    Returns True if the lock can be acquired (safe flush window is open, game not running).
    Returns False if the lock is held (game is running).
    """
    with SafeFlushWindow(lock_path, timeout_s=0.0, raise_on_busy=False) as window:
        return window.is_acquired

