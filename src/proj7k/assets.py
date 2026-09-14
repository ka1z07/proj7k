import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any


@dataclass
class AssetLibraryIndex:
    by_id: Dict[int, Path] = field(default_factory=dict)
    by_title_version: Dict[Tuple[str, str], Path] = field(default_factory=dict)
    all_files: List[Path] = field(default_factory=list)

    def find_path(self, beatmap_id: Optional[int], song_name: Optional[str] = None) -> Optional[Path]:
        if beatmap_id is not None and beatmap_id in self.by_id:
            return self.by_id[beatmap_id]

        if song_name:
            # Match song name against title and version exactly
            normalized_target = song_name.strip().lower()
            for (title, version), path in self.by_title_version.items():
                if title and version and (title in normalized_target and version in normalized_target):
                    return path

        return None


def scan_local_asset_library(library_dir: Union[str, Path]) -> AssetLibraryIndex:
    """
    Recursively scans a directory for valid 7K .osu beatmap files and extracts:
    1. BeatmapID from filename or [Metadata] BeatmapID header.
    2. Title and Version from [Metadata] section.
    Enforces Mode: 3 and CircleSize: 7 pre-validation.
    """
    base = Path(library_dir)
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Asset library directory does not exist: {library_dir}")

    index = AssetLibraryIndex()

    for p in base.rglob("*.osu"):
        if not p.is_file():
            continue

        # Inspect header for 7K Mode, CircleSize, BeatmapID, Title, Version
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                beatmap_id: Optional[int] = int(p.stem) if p.stem.isdigit() else None
                title: str = ""
                version: str = ""
                mode_val: Optional[int] = None
                cs_val: Optional[int] = None

                for _ in range(80):
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if line.startswith("[HitObjects]"):
                        break

                    if line.startswith("Mode:"):
                        val = line.split(":", 1)[1].strip()
                        if val.isdigit():
                            mode_val = int(val)
                    elif line.startswith("CircleSize:"):
                        val = line.split(":", 1)[1].strip()
                        try:
                            cs_val = int(float(val))
                        except ValueError:
                            pass
                    elif line.startswith("BeatmapID:"):
                        val = line.split(":", 1)[1].strip()
                        if val.isdigit() and int(val) > 0:
                            beatmap_id = int(val)
                    elif line.startswith("Title:"):
                        title = line.split(":", 1)[1].strip().lower()
                    elif line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip().lower()

                # Validate Mode 3 (osu!mania) and CircleSize 7 (7K)
                if mode_val is not None and mode_val != 3:
                    continue
                if cs_val is not None and cs_val != 7:
                    continue

                index.all_files.append(p)
                if beatmap_id is not None:
                    index.by_id[beatmap_id] = p
                if title or version:
                    index.by_title_version[(title, version)] = p
        except Exception:
            continue

    return index


def bind_manifest_to_library(
    items: List[Any],
    library_dir_or_index: Union[str, Path, AssetLibraryIndex],
) -> List[Any]:
    """
    Binds BenchmarkItem instances to local .osu files found in the asset library.
    """
    if isinstance(library_dir_or_index, AssetLibraryIndex):
        index = library_dir_or_index
    else:
        index = scan_local_asset_library(library_dir_or_index)

    for item in items:
        # If item already has a valid existing path, preserve it
        if item.osu_path and os.path.exists(item.osu_path):
            continue

        matched = index.find_path(item.id, item.song)
        if matched:
            item.osu_path = str(matched)

    return items
