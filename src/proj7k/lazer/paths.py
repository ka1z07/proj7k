"""
Per-platform location of the osu!lazer data directory (client.realm, files/, logs/).

osu!lazer keeps its data where osu-framework's desktop hosts put user storage, unless the
player moved it in-game, in which case a storage.ini next to the default location names the
new directory with a `FullPath = ...` line.
"""

import os
from pathlib import Path
import sys
from typing import Mapping, Optional


def default_lazer_dir(
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """The directory osu!lazer uses on this platform before any in-game relocation."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()

    if platform == "darwin":
        return home / "Library" / "Application Support" / "osu"
    if platform.startswith("win"):
        appdata = env.get("APPDATA")
        return (Path(appdata) if appdata else home / "AppData" / "Roaming") / "osu"
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / "osu"


def relocated_lazer_dir(base: Path) -> Optional[Path]:
    """The directory storage.ini in `base` points at, or None when the data was never moved."""
    ini = base / "storage.ini"
    try:
        lines = ini.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "FullPath" and value.strip():
            return Path(value.strip())
    return None


def resolve_lazer_dir(
    platform: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """The osu!lazer data directory on this machine, following an in-game relocation."""
    base = default_lazer_dir(platform=platform, env=env, home=home)
    return relocated_lazer_dir(base) or base


LAZER_DIR = resolve_lazer_dir()
