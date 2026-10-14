"""
Tests for proj7k.lazer.client: finding osu!lazer (and not osu!stable), closing it politely, starting it.
"""

import json
from pathlib import Path
import subprocess

import pytest

from proj7k.lazer.client import LazerClient, default_lazer_executable


def test_default_executable_per_platform(tmp_path):
    assert default_lazer_executable("win32", {"LOCALAPPDATA": r"C:\Users\K\AppData\Local"}) == (
        Path(r"C:\Users\K\AppData\Local") / "osulazer" / "osu!.exe"
    )
    assert default_lazer_executable("win32", {}, home=tmp_path) == tmp_path / "AppData" / "Local" / "osulazer" / "osu!.exe"
    assert default_lazer_executable("darwin", {}) == Path("/Applications/osu!.app")
    assert default_lazer_executable("linux", {}) is None


class FakeWindows:
    """Get-Process and taskkill as seen by the client; taskkill makes the listed processes exit after `lag` polls."""

    def __init__(self, procs, lag=1, exits=True):
        self.procs = dict(procs)  # pid -> image path
        self.calls = []
        self.lag = lag
        self.exits = exits
        self.closing = set()

    def run(self, args):
        self.calls.append(list(args))
        out = ""
        if args[0] == "powershell":
            if self.closing and self.exits:
                if self.lag <= 0:
                    for pid in self.closing:
                        self.procs.pop(pid, None)
                    self.closing.clear()
                self.lag -= 1
            rows = [{"Id": pid, "Path": path} for pid, path in self.procs.items()]
            out = json.dumps(rows[0] if len(rows) == 1 else rows) if rows else ""
        elif args[0] == "taskkill":
            assert "/F" not in args
            self.closing |= {int(a) for a in args[2::2]}
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")


def _client(fake, exe, **kw):
    clock = [0.0]

    def sleep(s):
        clock[0] += s

    return LazerClient(executable=exe, platform="win32", run=fake.run, sleep=sleep, clock=lambda: clock[0], **kw)


LAZER = r"C:\Users\K\AppData\Local\osulazer"
STABLE = r"C:\Users\K\AppData\Local\osu!\osu!.exe"


def test_only_processes_under_the_lazer_install_count():
    fake = FakeWindows({
        10: LAZER + r"\current\osu!.exe",
        11: STABLE,
        12: r"C:\Users\K\AppData\Local\osulazer-old\osu!.exe",
    })
    client = _client(fake, Path(LAZER) / "osu!.exe")
    assert [p.pid for p in client.processes()] == [10]


def test_close_asks_politely_and_waits_for_exit():
    fake = FakeWindows({10: LAZER + r"\current\osu!.exe", 11: STABLE}, lag=2)
    client = _client(fake, Path(LAZER) / "osu!.exe")
    assert client.close() is True
    assert ["taskkill", "/PID", "10"] in fake.calls
    assert 11 in fake.procs and 10 not in fake.procs


def test_close_when_not_running_and_timeout():
    assert _client(FakeWindows({11: STABLE}), Path(LAZER) / "osu!.exe").close() is False

    stuck = _client(FakeWindows({10: LAZER + r"\current\osu!.exe"}, exits=False), Path(LAZER) / "osu!.exe")
    with pytest.raises(TimeoutError):
        stuck.close(timeout_s=3)


def test_restart_closes_then_starts(tmp_path):
    exe = tmp_path / "osulazer" / "osu!.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    fake = FakeWindows({10: str(exe.parent / "current" / "osu!.exe")})
    started = []
    client = _client(fake, exe, launcher=started.append)
    assert client.restart() is True
    assert started == [exe]
    assert client.restart() is False  # nothing to close the second time: it just starts
    assert started == [exe, exe]


def test_start_needs_an_existing_executable(tmp_path):
    with pytest.raises(FileNotFoundError):
        LazerClient(executable=None, platform="linux").start()
    with pytest.raises(FileNotFoundError):
        LazerClient(executable=tmp_path / "missing.AppImage", platform="linux", launcher=lambda p: None).start()
