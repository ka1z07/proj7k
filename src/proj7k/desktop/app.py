"""
The desktop app: find or start the dashboard, then open it in an app window.

Usage:
    python3 -m proj7k.desktop [--port 7770] [--window auto|webview|app|browser] [--page home]

Windows: double-click `proj7k.cmd`, or the Start-menu / desktop shortcut `deploy/desktop/install-shortcut.ps1` makes.

The window, in order of preference (`--window auto`):

1. `webview`: a native window from pywebview (`pip install pywebview`; on Windows it uses the WebView2 runtime that
   ships with Windows 10/11), when it is installed and loads.
2. `app`: Microsoft Edge or Google Chrome in app mode (`--app=URL`): a window with no tabs or address bar, with its
   own profile under `~/.proj7k/desktop`, so it remembers its size and position and does not mix with browsing.
3. `browser`: the default browser, as `python3 -m proj7k.dashboard` opens it.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import List, Optional, Sequence
import urllib.request
import webbrowser

from proj7k.i18n import language, system_language, tr

logger = logging.getLogger("proj7k.desktop")

REPO_ROOT = Path(__file__).resolve().parents[3]
PROFILE_DIR = Path.home() / ".proj7k" / "desktop"
LOG_DIR = Path.home() / ".cache" / "proj7k" / "logs"
TITLE = "proj7k"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m proj7k.desktop", description="proj7k as a desktop app.")
    parser.add_argument("--port", type=int, default=7770, help="The dashboard's port (default: 7770).")
    parser.add_argument(
        "--window", choices=("auto", "webview", "app", "browser"), default="auto",
        help="How to show it: a pywebview window, an Edge/Chrome app window, or the default browser (default: auto).",
    )
    parser.add_argument("--page", default="home", help="The page to open on (home, history, library, live, ...).")
    parser.add_argument("--timeout", type=float, default=90.0, help="Seconds to wait for a dashboard it starts.")
    return parser


# --- the dashboard ---------------------------------------------------------------------------------------------


def dashboard_running(port: int, timeout: float = 1.5) -> bool:
    """Whether a proj7k dashboard answers on this port (and not some other program)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=timeout) as res:
            return "realm_path" in json.loads(res.read().decode("utf-8"))
    except Exception:
        return False


def start_dashboard(port: int) -> None:
    """
    Starts the dashboard detached from this process, so it keeps running (and keeps its jobs) after the window
    closes. On Windows it goes through `restart-services.ps1 -DashboardOnly`, the same start the auto-updater and
    the restart button use; elsewhere it is a background process logging to ~/.cache/proj7k/logs/dashboard.log.
    """
    script = REPO_ROOT / "restart-services.ps1"
    if sys.platform == "win32" and script.is_file():
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-DashboardOnly", "-DashboardPort", str(port)],
            cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO_ROOT / "src"), env.get("PYTHONPATH", "")) if p)
    log = open(LOG_DIR / "dashboard.log", "ab")
    kwargs = {"start_new_session": True}
    if sys.platform == "win32":
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
    subprocess.Popen(
        [_console_python(), "-m", "proj7k.dashboard", "--port", str(port), "--no-open"],
        cwd=str(REPO_ROOT), env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, **kwargs,
    )


def _console_python() -> str:
    """python.exe rather than pythonw.exe: the dashboard logs to stdout."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        return str(exe.parent / "python.exe")
    return sys.executable


def ensure_dashboard(port: int, timeout: float) -> bool:
    if dashboard_running(port):
        logger.info(f"Using the dashboard already running on port {port}.")
        return True
    logger.info(f"No dashboard on port {port}; starting one.")
    start_dashboard(port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if dashboard_running(port):
            return True
        time.sleep(0.5)
    return False


# --- the window ------------------------------------------------------------------------------------------------


def chromium_candidates() -> List[Path]:
    """Edge first (every Windows 10/11 has it), then Chrome, where each is usually installed."""
    found: List[Path] = []
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            found += [Path(r) / rel for r in roots if r]
    elif sys.platform == "darwin":
        found += [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                  Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for name in ("microsoft-edge", "msedge", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            found.append(Path(p))
    return [p for p in found if p.is_file()]


def app_window_command(browser: Path, url: str) -> List[str]:
    return [
        str(browser), f"--app={url}", f"--user-data-dir={PROFILE_DIR / 'browser'}",
        "--window-size=1440,920", "--no-first-run", "--no-default-browser-check",
    ]


def open_webview(url: str) -> bool:
    try:
        import webview  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        settings = getattr(webview, "settings", None)
        if isinstance(settings, dict):
            settings["ALLOW_DOWNLOADS"] = True
            settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.create_window(TITLE, url, width=1440, height=920, min_size=(960, 600), text_select=True)
        webview.start(private_mode=False, storage_path=str(PROFILE_DIR / "webview"))
        return True
    except Exception as e:
        logger.warning(f"pywebview could not open a window: {e}")
        return False


def open_app_window(url: str) -> bool:
    for browser in chromium_candidates():
        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(app_window_command(browser, url), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            logger.info(f"{browser} did not start: {e}")
            continue
        # With its own profile the browser stays in the foreground process until the window closes; if a window
        # of that profile is already open it hands the URL over and exits at once, which is fine too.
        proc.wait()
        return True
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: List[logging.Handler] = [logging.FileHandler(LOG_DIR / "desktop.log", encoding="utf-8")]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", handlers=handlers)

    if not ensure_dashboard(args.port, args.timeout):
        with language(system_language()):
            message = tr(f"proj7k 控制台没有启动起来，日志在 {LOG_DIR}", f"The proj7k console didn't start; logs are in {LOG_DIR}")
        logger.error(message)
        _alert(message)
        return 1
    url = f"http://127.0.0.1:{args.port}/#{args.page}"

    order = {"auto": ("webview", "app", "browser"), "webview": ("webview", "browser"),
             "app": ("app", "browser"), "browser": ("browser",)}[args.window]
    for how in order:
        if how == "webview" and open_webview(url):
            return 0
        if how == "app" and open_app_window(url):
            return 0
        if how == "browser":
            webbrowser.open(url)
            return 0
    return 1


def _alert(message: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    print(message, file=sys.stderr if sys.stderr else sys.stdout)
