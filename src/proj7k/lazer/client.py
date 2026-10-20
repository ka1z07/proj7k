"""
Closing and starting the osu!lazer client, so the dashboard can restart the game (and sync while it is closed).

Closing is the polite kind only: the game is asked to close its window, exactly as clicking ✕ does, so it saves and
exits on its own (it may ask first, for instance while a download is running). Nothing here kills it. The game counts
as closed once its process is gone; the Realm lock (`proj7k.lazer.lock`) is released with it.

Where the game lives:
- Windows: the installer puts a launcher at %LOCALAPPDATA%\\osulazer\\osu!.exe and the game in `current\\` beside it.
  The launcher also applies a downloaded update before starting the game, so it is what we start. osu!stable's
  process has the same image name, so only processes under the lazer install count as lazer.
- macOS: the `osu!` application bundle, started and quit through LaunchServices.
- Linux: the AppImage, whose location the player chooses; it must be configured (`--lazer-exe`), and the running
  game is found by its process name.
"""

from dataclasses import dataclass, field
import json
import logging
import ntpath
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Callable, List, Mapping, Optional, Sequence

from proj7k.i18n import tr

logger = logging.getLogger("proj7k.lazer.client")

#: How long a polite close may take before we give up (the game may be waiting on its own "are you sure?" dialog).
DEFAULT_CLOSE_TIMEOUT_S = 30.0

MAC_APP_NAME = "osu!"


def default_lazer_executable(
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Optional[Path]:
    """Where the installer puts osu!lazer on this platform, or None where there is no standard place (Linux)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()

    if platform.startswith("win"):
        local = env.get("LOCALAPPDATA")
        return (Path(local) if local else home / "AppData" / "Local") / "osulazer" / "osu!.exe"
    if platform == "darwin":
        return Path("/Applications/osu!.app")
    return None


def _run(args: Sequence[str]) -> subprocess.CompletedProcess:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return subprocess.run(list(args), capture_output=True, text=True, timeout=20, **kwargs)


@dataclass
class LazerProcess:
    pid: int
    path: Optional[Path] = None


def _windows_processes(run=_run) -> List[LazerProcess]:
    """Every running `osu!` process with its image path (osu!stable included; the caller filters)."""
    script = (
        "Get-Process -Name 'osu!' -ErrorAction SilentlyContinue | "
        "Select-Object Id, Path | ConvertTo-Json -Compress"
    )
    res = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    text = (res.stdout or "").strip()
    if not text:
        return []
    rows = json.loads(text)
    if isinstance(rows, dict):
        rows = [rows]
    return [LazerProcess(int(r["Id"]), Path(r["Path"]) if r.get("Path") else None) for r in rows]


def _posix_processes(run=_run) -> List[LazerProcess]:
    res = run(["pgrep", "-x", "osu!"])
    return [LazerProcess(int(line)) for line in (res.stdout or "").split() if line.strip().isdigit()]


@dataclass
class LazerClient:
    """Finds, closes and starts osu!lazer. The process and launch hooks are swappable for tests."""
    executable: Optional[Path] = field(default_factory=default_lazer_executable)
    platform: str = sys.platform
    run: Callable[[Sequence[str]], subprocess.CompletedProcess] = _run
    launcher: Optional[Callable[[Path], None]] = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    @property
    def is_windows(self) -> bool:
        return self.platform.startswith("win")

    def _install_root(self) -> Optional[Path]:
        if self.executable is None:
            return None
        return self.executable if self.executable.suffix == ".app" else self.executable.parent

    def processes(self) -> List[LazerProcess]:
        """The running osu!lazer processes (never osu!stable's)."""
        if self.is_windows:
            root = self._install_root()
            found = _windows_processes(self.run)
            if root is None:
                return found
            root_text = ntpath.normcase(str(root)).rstrip("\\") + "\\"
            return [p for p in found if p.path and ntpath.normcase(str(p.path)).startswith(root_text)]
        if self.platform == "darwin":
            res = self.run(["pgrep", "-f", f"{MAC_APP_NAME}.app/Contents/MacOS/"])
            return [LazerProcess(int(x)) for x in (res.stdout or "").split() if x.strip().isdigit()]
        return _posix_processes(self.run)

    def is_running(self) -> bool:
        return bool(self.processes())

    def request_close(self, procs: Sequence[LazerProcess]) -> None:
        """Ask the game to close, as clicking its window's ✕ does."""
        if self.is_windows:
            args = ["taskkill"]
            for p in procs:
                args += ["/PID", str(p.pid)]
            self.run(args)
        elif self.platform == "darwin":
            self.run(["osascript", "-e", f'quit app "{MAC_APP_NAME}"'])
        else:
            for p in procs:
                try:
                    os.kill(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def close(self, timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S) -> bool:
        """
        Close osu!lazer and wait until it has exited. Returns whether it was running.
        Raises TimeoutError when it is still running after `timeout_s` (it may be showing a dialog).
        """
        procs = self.processes()
        if not procs:
            logger.info("osu!lazer is not running.")
            return False
        logger.info(f"Asking osu!lazer to close (pid {', '.join(str(p.pid) for p in procs)})...")
        self.request_close(procs)
        deadline = self.clock() + timeout_s
        while self.clock() < deadline:
            self.sleep(0.5)
            if not self.processes():
                logger.info("osu!lazer has exited.")
                return True
        raise TimeoutError(
            tr(f"osu!lazer 在 {timeout_s:.0f} 秒内没有退出：它可能弹出了确认窗口（例如有下载在进行），请在游戏里确认后再试",
               f"osu!lazer didn't exit within {timeout_s:.0f} s: it may be showing a confirmation (a download in progress, "
               "for example); confirm it in the game and try again")
        )

    def start(self) -> None:
        """Start osu!lazer, detached from this process."""
        exe = self.executable
        if exe is None:
            raise FileNotFoundError(tr("不知道 osu!lazer 装在哪里：启动 dashboard 时用 --lazer-exe 指定",
                                       "Don't know where osu!lazer is installed: start the dashboard with --lazer-exe"))
        if not exe.exists():
            raise FileNotFoundError(tr(f"找不到 osu!lazer：{exe}（用 --lazer-exe 指定实际位置）",
                                       f"osu!lazer not found: {exe} (pass its location with --lazer-exe)"))
        logger.info(f"Starting osu!lazer from '{exe}'...")
        if self.launcher is not None:
            self.launcher(exe)
        elif self.is_windows:
            os.startfile(str(exe))  # type: ignore[attr-defined]
        elif self.platform == "darwin":
            subprocess.Popen(["open", str(exe)])
        else:
            subprocess.Popen([str(exe)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def restart(self, timeout_s: float = DEFAULT_CLOSE_TIMEOUT_S) -> bool:
        """Close osu!lazer if it runs, then start it. Returns whether it had been running."""
        was_running = self.close(timeout_s)
        self.start()
        return was_running
