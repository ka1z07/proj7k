"""
Stopping a running dashboard job (ADR-0049): cooperatively, only where nothing is half-written.

A thread can't be killed from outside, so a job's thread watches its stop flag (`watching`) and the long work in it
asks at safe points: `checkpoint()` raises `Cancelled` once the flag is set. The points sit between whole steps
(between charts, replays or model runs, before a write starts), never inside a write to a shared store, so a stop
lands before or after each write. A child process started with `run` is killed (with its own children) when the
flag is set: what it writes is scratch until the parent reads it.

`Cancelled` is a `BaseException`, like `asyncio.CancelledError`, so the `except Exception` that skips one bad chart
or replay doesn't swallow it. Off a job's thread (the command line, tests) nothing is ever stopped.
"""

from contextlib import contextmanager
import os
import signal
import subprocess
import threading
from typing import Iterator, Optional

#: How often a child process `run` waits on checks the stop flag, in seconds.
POLL_S = 0.5

_local = threading.local()


class Cancelled(BaseException):
    """Raised on a job's thread at a safe point after the job was asked to stop."""


def current() -> Optional[threading.Event]:
    """The stop flag of the job on this thread, or None when no job runs on it."""
    return getattr(_local, "event", None)


@contextmanager
def watching(event: threading.Event) -> Iterator[None]:
    """Run the body as a job that stops at its next safe point once `event` is set."""
    previous = current()
    _local.event = event
    try:
        yield
    finally:
        _local.event = previous


def requested() -> bool:
    event = current()
    return event is not None and event.is_set()


def checkpoint() -> None:
    """A safe point: raises `Cancelled` when the job on this thread was asked to stop."""
    if requested():
        raise Cancelled()


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        # The model's Python starts workers of its own; /T takes them too.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc.poll() is None:
        proc.kill()


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    """
    `subprocess.run` that a stop interrupts: on a job's thread, the process (and every process it started) is killed
    as soon as the job is asked to stop, and `Cancelled` is raised. Only for children whose output is scratch.
    """
    event = current()
    if event is None:
        return subprocess.run(cmd, **kwargs)
    checkpoint()
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    if os.name != "nt":
        kwargs["start_new_session"] = True  # its own process group, so its children go with it
    with subprocess.Popen(cmd, **kwargs) as proc:
        while True:
            try:
                out, err = proc.communicate(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                if event.is_set():
                    _kill_tree(proc)
                    proc.communicate()
                    raise Cancelled()
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
