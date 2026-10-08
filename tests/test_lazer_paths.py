"""
Tests for the per-platform osu!lazer data directory lookup.
"""

from pathlib import Path

from proj7k.lazer.paths import default_lazer_dir, relocated_lazer_dir, resolve_lazer_dir


HOME = Path("/home/player")


def test_default_dir_macos():
    assert default_lazer_dir("darwin", {}, HOME) == HOME / "Library" / "Application Support" / "osu"


def test_default_dir_windows_uses_appdata():
    appdata = Path("C:/Users/player/AppData/Roaming")
    assert default_lazer_dir("win32", {"APPDATA": str(appdata)}, HOME) == appdata / "osu"
    assert default_lazer_dir("win32", {}, HOME) == HOME / "AppData" / "Roaming" / "osu"


def test_default_dir_linux_follows_xdg():
    assert default_lazer_dir("linux", {}, HOME) == HOME / ".local" / "share" / "osu"
    assert default_lazer_dir("linux", {"XDG_DATA_HOME": "/data"}, HOME) == Path("/data") / "osu"


def test_storage_ini_relocation(tmp_path: Path):
    base = tmp_path / ".local" / "share" / "osu"
    base.mkdir(parents=True)
    assert relocated_lazer_dir(base) is None

    moved = tmp_path / "osu-data"
    (base / "storage.ini").write_text(f"FullPath = {moved}\n", encoding="utf-8")
    assert relocated_lazer_dir(base) == moved
    assert resolve_lazer_dir("linux", {}, tmp_path) == moved


def test_storage_ini_without_full_path_is_ignored(tmp_path: Path):
    base = tmp_path / ".local" / "share" / "osu"
    base.mkdir(parents=True)
    (base / "storage.ini").write_text("FullPath =\n", encoding="utf-8")
    assert resolve_lazer_dir("linux", {}, tmp_path) == base
