"""
proj7k.downscaler.locator - Automatic beatmap URL parser, local lazer database locator, and .osz packager.
"""

from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional, Tuple, Union

from proj7k.lazer.bridge import DEFAULT_REALM_PATH, RealmBridgeClient
from proj7k.parser import Beatmap7K, parse_osu_7k

logger = logging.getLogger("proj7k.downscaler.locator")


@dataclass(frozen=True)
class BeatmapUrlInfo:
    """Structured information extracted from an osu! beatmap URL or ID string."""
    original_input: str
    beatmap_id: Optional[int] = None
    beatmapset_id: Optional[int] = None
    ruleset_mode: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.beatmap_id is not None or self.beatmapset_id is not None


@dataclass(frozen=True)
class ResolvedBeatmapAsset:
    """Resolved physical paths and metadata for an osu! beatmap and its media files."""
    osu_path: Path
    title: str
    artist: str
    creator: str
    difficulty_name: str
    audio_path: Optional[Path] = None
    audio_filename: str = "audio.mp3"
    bg_path: Optional[Path] = None
    bg_filename: str = "bg.png"
    beatmap_id: Optional[int] = None
    beatmapset_id: Optional[int] = None
    file_hash: str = ""


def parse_osu_url_or_id(input_str: Union[str, int]) -> BeatmapUrlInfo:
    """
    Parses an osu! URL or ID string into structured BeatmapUrlInfo.

    Supported patterns:
    - https://osu.ppy.sh/beatmapsets/2422670#mania/5271675
    - https://osu.ppy.sh/beatmapsets/2422670
    - https://osu.ppy.sh/b/5271675
    - https://osu.ppy.sh/beatmaps/5271675
    - Plain numeric ID: '5271675'
    """
    clean = str(input_str).strip()

    # Pattern 1: https://osu.ppy.sh/beatmapsets/<set_id>#<mode>/<beatmap_id>
    m1 = re.search(r"osu\.ppy\.sh/beatmapsets/(\d+)(?:#(\w+)/(\d+))?", clean)
    if m1:
        set_id = int(m1.group(1))
        mode = m1.group(2)
        b_id = int(m1.group(3)) if m1.group(3) else None
        return BeatmapUrlInfo(
            original_input=clean,
            beatmap_id=b_id,
            beatmapset_id=set_id,
            ruleset_mode=mode,
        )

    # Pattern 2: https://osu.ppy.sh/b/<id> or https://osu.ppy.sh/beatmaps/<id>
    m2 = re.search(r"osu\.ppy\.sh/(?:b|beatmaps)/(\d+)", clean)
    if m2:
        return BeatmapUrlInfo(
            original_input=clean,
            beatmap_id=int(m2.group(1)),
        )

    # Pattern 3: Plain integer string
    if clean.isdigit():
        return BeatmapUrlInfo(
            original_input=clean,
            beatmap_id=int(clean),
        )

    return BeatmapUrlInfo(original_input=clean)


def _resolve_lazer_hashed_file(files_dir: Path, file_hash: str) -> Optional[Path]:
    """Resolves physical file on disk from osu!lazer SHA-256 hash."""
    if not file_hash or len(file_hash) < 2:
        return None
    candidate = files_dir / file_hash[0] / file_hash[:2] / file_hash
    return candidate if candidate.exists() else None


def locate_beatmap_in_lazer(
    url_or_id: Union[str, int, BeatmapUrlInfo],
    realm_path: Optional[Path] = None,
    files_dir: Optional[Path] = None,
    bridge_client: Optional[RealmBridgeClient] = None,
) -> Optional[ResolvedBeatmapAsset]:
    """
    Searches local osu!lazer database for a beatmap corresponding to the input URL or ID.
    Extracts the physical .osu file, audio.mp3, and background image paths.
    """
    info = url_or_id if isinstance(url_or_id, BeatmapUrlInfo) else parse_osu_url_or_id(url_or_id)
    if not info.is_valid:
        return None

    target_realm = realm_path or DEFAULT_REALM_PATH
    if not target_realm.exists() and bridge_client is None:
        logger.warning(f"osu!lazer database not found at {target_realm}")
        return None

    client = bridge_client or RealmBridgeClient(default_realm_path=target_realm)
    target_files_dir = files_dir or (target_realm.parent / "files")

    record: Optional[Dict[str, Any]] = None

    # Try 1: Search by beatmap_id
    if info.beatmap_id is not None:
        try:
            res_bm = client.locate_beatmap(online_id=info.beatmap_id, realm_path=target_realm)
            if res_bm and isinstance(res_bm, dict) and "beatmap" in res_bm:
                record = res_bm["beatmap"]
            else:
                record = res_bm
        except Exception as e:
            logger.debug(f"locate_beatmap by online_id failed: {e}")

    # Try 2: Search by beatmapset_id
    if not record and info.beatmapset_id is not None:
        try:
            res_bm = client.locate_beatmap(set_id=info.beatmapset_id, realm_path=target_realm)
            if res_bm and isinstance(res_bm, dict) and "beatmap" in res_bm:
                record = res_bm["beatmap"]
            else:
                record = res_bm
        except Exception as e:
            logger.debug(f"locate_beatmap by set_id failed: {e}")

    if not record:
        return None

    # Resolve physical paths
    files_list = record.get("files", [])
    osu_hash = record.get("osu_file_hash", "")
    audio_obj = record.get("audio_file")
    bg_obj = record.get("bg_file")

    audio_path: Optional[Path] = None
    if audio_obj and audio_obj.get("hash"):
        audio_path = _resolve_lazer_hashed_file(target_files_dir, audio_obj["hash"])

    bg_path: Optional[Path] = None
    if bg_obj and bg_obj.get("hash"):
        bg_path = _resolve_lazer_hashed_file(target_files_dir, bg_obj["hash"])

    # If files_list is present, sort candidate .osu files prioritizing non-practice originals
    matched_osu_hash = osu_hash
    if files_list:
        cand_osu_files = [
            f for f in files_list
            if f.get("filename", "").lower().endswith(".osu") and f.get("hash")
        ]
        # Prioritize original maps (not containing [p-)
        cand_osu_files.sort(key=lambda x: ("[p-" in x.get("filename", "").lower(), x.get("filename", "")))

        if info.beatmap_id is not None:
            found_by_id = False
            for f in cand_osu_files:
                fhash = f.get("hash", "")
                cand_path = _resolve_lazer_hashed_file(target_files_dir, fhash)
                if cand_path and cand_path.exists():
                    try:
                        with open(cand_path, "r", encoding="utf-8", errors="ignore") as fh:
                            content = fh.read(2000)
                            if f"BeatmapID:{info.beatmap_id}" in content or f"BeatmapID: {info.beatmap_id}" in content:
                                matched_osu_hash = fhash
                                found_by_id = True
                                break
                    except Exception:
                        pass
            if not found_by_id and cand_osu_files:
                matched_osu_hash = cand_osu_files[0].get("hash", matched_osu_hash)
        elif cand_osu_files:
            matched_osu_hash = cand_osu_files[0].get("hash", matched_osu_hash)

    osu_path = _resolve_lazer_hashed_file(target_files_dir, matched_osu_hash)
    if not osu_path:
        return None

    audio_file_info = record.get("audio_file") or {}
    bg_file_info = record.get("bg_file") or {}

    return ResolvedBeatmapAsset(
        osu_path=osu_path,
        title=record.get("title", ""),
        artist=record.get("artist", ""),
        creator=record.get("creator", ""),
        difficulty_name=record.get("difficulty_name", ""),
        audio_path=audio_path,
        audio_filename=audio_file_info.get("filename") or "audio.mp3",
        bg_path=bg_path,
        bg_filename=bg_file_info.get("filename") or "bg.png",
        beatmap_id=info.beatmap_id or record.get("online_id"),
        beatmapset_id=info.beatmapset_id or record.get("set_online_id"),
        file_hash=matched_osu_hash,
    )


def fetch_beatmap_from_web(
    info: BeatmapUrlInfo,
    output_dir: Path,
    timeout_s: float = 15.0,
) -> Optional[Path]:
    """
    Downloads .osu beatmap file from web endpoint (e.g. https://osu.ppy.sh/osu/<id>)
    as a fallback when beatmap is not found locally.
    """
    if not info.beatmap_id:
        return None

    url = f"https://osu.ppy.sh/osu/{info.beatmap_id}"
    dest_path = output_dir / f"{info.beatmap_id}.osu"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "proj7k/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            content = resp.read()
            if content.startswith(b"osu file format"):
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dest_path, "wb") as f:
                    f.write(content)
                return dest_path
    except Exception as e:
        logger.warning(f"Could not fetch .osu from {url}: {e}")

    return None


def package_into_osz(
    practice_osu_path: Path,
    output_osz_path: Path,
    audio_path: Optional[Path] = None,
    audio_filename: Optional[str] = None,
    bg_path: Optional[Path] = None,
    bg_filename: Optional[str] = None,
    extra_files: Optional[List[Tuple[Path, str]]] = None,
) -> Path:
    """
    Packages derivative practice beatmap, audio, and background image into an .osz archive.
    """
    output_osz_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_osz_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # 1. Practice .osu file
        z.write(practice_osu_path, arcname=practice_osu_path.name)

        # 2. Audio file
        if audio_path and audio_path.exists():
            target_audio_name = audio_filename or "audio.mp3"
            z.write(audio_path, arcname=target_audio_name)

        # 3. Background image
        if bg_path and bg_path.exists():
            default_bg_name = "bg.png" if bg_path.suffix.lower() == ".png" else "bg.jpg"
            target_bg_name = bg_filename or default_bg_name
            z.write(bg_path, arcname=target_bg_name)

        # 4. Extra files if any
        if extra_files:
            for p, arcname in extra_files:
                if p.exists():
                    z.write(p, arcname=arcname)

    return output_osz_path
