"""
proj7k.downscaler.cli - Command-Line Interface and osu!lazer Bridge Sync for Downscaler.

SPEC-P5.1-04 / ADR-0011 / Ticket 14.
"""

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from proj7k.parser import Beatmap7K, dump_osu_7k, parse_osu_7k
from proj7k.engine.skills import SKILL_TECH_KEY
from proj7k.lazer.annotator import (
    generate_binned_skill_tags,
    inject_binned_skill_tags,
)
from proj7k.lazer.bridge import (
    DEFAULT_REALM_PATH,
    BeatmapMutationPayload,
    LazerBeatmapRecord,
    RealmBridgeClient,
)
from proj7k.lazer.daemon import DEFAULT_LOCK_PATH
from proj7k.lazer.lock import SafeFlushWindow
from proj7k.downscaler.balancer import BimanualFluxBalancer
from proj7k.downscaler.pipeline import (
    DownscaleOptions,
    DownscaleResult,
    downscale_beatmap,
)
from proj7k.downscaler.locator import (
    MD5_PATTERN,
    ResolvedBeatmapAsset,
    fetch_beatmap_from_web,
    locate_beatmap_in_lazer,
    package_into_osz,
    parse_osu_url_or_id,
)


logger = logging.getLogger("proj7k.downscaler")


# ANSI Color Codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
DIM = "\033[2m"


@dataclass(frozen=True)
class LazerPracticeSyncResult:
    """Outcome of synchronizing downscaled practice charts into osu!lazer."""
    success: bool
    collection_name: str = "7K Practice"
    synced_hashes: List[str] = field(default_factory=list)
    updated_in_realm: int = 0
    error: Optional[str] = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.downscaler",
        description="proj7k - Closed-loop downscaler on the difficulty engine and derivative practice beatmap generator (SPEC-P5.1-04 / ADR-0011 / ADR-0021).",
    )

    req_group = parser.add_argument_group("Input & Target Configuration")
    req_group.add_argument(
        "-i",
        "--input",
        type=str,
        required=True,
        help="Path to an .osu file, directory, or osu! URL / numeric beatmap ID to downscale.",
    )
    req_group.add_argument(
        "-d",
        "--target-dan",
        type=str,
        default=None,
        help="Target Jinjin Dan tier (e.g. '7th', 'Stellium', '04th', 'Regular').",
    )
    req_group.add_argument(
        "-s",
        "--target-sr",
        type=float,
        default=None,
        help="Target continuous Star Rating on the difficulty engine's scale (e.g. 6.5). Overrides or interpolates Dan.",
    )
    req_group.add_argument(
        "--target-d",
        type=float,
        default=None,
        help="Target engine level D* in equivalent Hz (what --target-sr stands for, by the star scale).",
    )
    req_group.add_argument(
        "--dominant-skill",
        type=str,
        default=None,
        help="Override dominant skill dimension (e.g. 'jack', 'tech', 'stream', 'ln_inverse').",
    )
    req_group.add_argument(
        "--mode",
        choices=("technique", "free"),
        default="technique",
        help=(
            "'technique' (default) keeps the chart's dominant technique; 'free' lets it go and only chases the "
            "target, still keeping the chart playable (rhythm skeleton, hand balance, no long new silences)."
        ),
    )

    out_group = parser.add_argument_group("Output Options")
    out_group.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write derivative practice beatmap(s) and .osz package(s). Defaults to 'practice_maps'.",
    )
    out_group.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Explicit output path for single beatmap downscaling.",
    )
    out_group.add_argument(
        "--package-osz",
        action="store_true",
        default=True,
        help="Package practice beatmap into a standalone .osz archive (enabled by default unless --no-package is specified).",
    )
    out_group.add_argument(
        "--no-package",
        action="store_true",
        default=False,
        help="Disable .osz packaging and only emit raw .osu beatmap file(s).",
    )
    out_group.add_argument(
        "--osz-output",
        type=Path,
        default=None,
        help="Explicit destination path for the packaged .osz file.",
    )
    out_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate downscaling and print comparison cards without writing files.",
    )

    opt_group = parser.add_argument_group("Optimization & Pruning Parameters")
    opt_group.add_argument(
        "--prune-ratio",
        type=float,
        default=0.20,
        help="Batch pruning ratio per iteration (default: 0.20 = 20%%).",
    )
    opt_group.add_argument(
        "--max-iterations",
        type=int,
        default=25,
        help="Maximum closed-loop pruning iterations (default: 25).",
    )
    opt_group.add_argument(
        "--tolerance",
        type=float,
        default=0.05,
        help="Star convergence tolerance: arrived within this share of the target star (default: 0.05 = 5%%).",
    )
    opt_group.add_argument(
        "--min-cosine-similarity",
        type=float,
        default=0.80,
        help="Minimum 8-skill star-vector cosine similarity preservation gate (default: 0.80).",
    )

    lazer_group = parser.add_argument_group("osu!lazer Integration")
    lazer_group.add_argument(
        "--sync-lazer",
        action="store_true",
        help="Automatically inject derivative practice beatmaps into osu!lazer '7K Practice' collection.",
    )
    lazer_group.add_argument(
        "--realm",
        type=Path,
        default=DEFAULT_REALM_PATH,
        help=f"Path to osu!lazer client.realm database (default: {DEFAULT_REALM_PATH}).",
    )
    lazer_group.add_argument(
        "--lock-path",
        type=Path,
        default=None,
        help="Path to client.realm.lock file (default: client.realm.lock next to client.realm).",
    )

    fmt_group = parser.add_argument_group("Formatting & Logging")
    fmt_group.add_argument(
        "--json",
        action="store_true",
        help="Output downscaling diagnostic reports as structured JSON.",
    )
    fmt_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging.",
    )

    return parser


def open_with_system_handler(path: Path) -> None:
    """Hand a file to the desktop's default handler (osu!lazer registers itself for .osz)."""
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent illegal filesystem characters on OS platforms."""
    return re.sub(r'[\\/*?:"<>|]', "_", filename)


def generate_practice_filename(
    original_stem: str,
    version: str,
    beatmap: Optional[Beatmap7K] = None,
) -> str:
    """
    Constructs a canonical practice beatmap filename:
    Replaces existing `[Version]` with `[{version}]` or appends `[{version}]`.
    If original_stem is a hash, uses metadata from beatmap.
    """
    clean_version = sanitize_filename(version)
    if beatmap and (len(original_stem) >= 32 and all(c in "0123456789abcdefABCDEF" for c in original_stem)):
        clean_artist = sanitize_filename(beatmap.artist or "Artist")
        clean_title = sanitize_filename(beatmap.title or "Title")
        clean_creator = sanitize_filename(beatmap.creator or "Creator")
        return f"{clean_artist} - {clean_title} ({clean_creator}) [{clean_version}].osu"

    if "[" in original_stem and original_stem.endswith("]"):
        prefix = original_stem.rsplit("[", 1)[0]
        return f"{prefix}[{clean_version}].osu"
    return f"{original_stem} [{clean_version}].osu"


def _make_ascii_bar(val: float, max_val: float = 12.0, width: int = 16) -> str:
    """Creates a normalized ASCII progress bar for radar comparisons."""
    fraction = min(1.0, max(0.0, val / max(1.0, max_val)))
    filled = int(round(fraction * width))
    empty = width - filled
    return f"[{'=' * filled}{'-' * empty}]"


def format_downscale_report(
    result: DownscaleResult,
    output_path: Optional[Path] = None,
    sync_result: Optional[LazerPracticeSyncResult] = None,
    osz_path: Optional[Path] = None,
) -> str:
    """Formats an ANSI-colored summary card with tabular metrics and technique radar comparison."""
    orig_bm = result.original_beatmap
    down_bm = result.downscaled_beatmap
    target = result.target

    orig_sr = result.original_stars
    down_sr = result.downscaled_stars
    sr_delta = down_sr - orig_sr
    sr_pct = (sr_delta / max(0.01, orig_sr)) * 100.0

    orig_notes = len(orig_bm.hit_objects)
    down_notes = len(down_bm.hit_objects)
    notes_delta = down_notes - orig_notes
    notes_pct = (notes_delta / max(1, orig_notes)) * 100.0

    balancer = BimanualFluxBalancer()
    orig_flux = balancer.compute_flux_ratio(orig_bm.hit_objects)
    orig_flux_str = f"{orig_flux[0] * 100.0:.1f}% : {orig_flux[1] * 100.0:.1f}%"
    down_flux = result.bimanual_flux_ratio
    down_flux_str = f"{down_flux[0] * 100.0:.1f}% : {down_flux[1] * 100.0:.1f}%"

    p_orig = result.original_profile
    p_down = result.downscaled_profile
    dom_orig = SKILL_TECH_KEY[p_orig.dominant_skill]
    dom_down = SKILL_TECH_KEY[p_down.dominant_skill]
    dom_score_orig = p_orig.skills[p_orig.dominant_skill].stars
    dom_score_down = p_down.skills[p_down.dominant_skill].stars

    cos_sim = result.validation.cosine_similarity
    val_status = f"{GREEN}PASSED{RESET}" if result.validation.passed else f"{RED}FAILED{RESET}"
    if result.preserve_technique:
        dom_status = f"{GREEN}Preserved (Rank 1){RESET}" if result.validation.dominant_conserved else f"{RED}Shifted{RESET}"
        cos_status = "Passed (>= 0.80)" if result.validation.gate1_passed else "Failed (< 0.80)"
    else:
        dom_status = "Preserved (Rank 1)" if result.validation.dominant_conserved else f"{DIM}Shifted (free mode){RESET}"
        cos_status = f"{DIM}Not enforced (free mode){RESET}"
    mode_str = "Keep dominant technique" if result.preserve_technique else "Free (target difficulty only)"

    out_file_str = str(output_path.name) if output_path else "InMemory / DryRun"

    title_str = f"{orig_bm.artist} - {orig_bm.title} [{orig_bm.version}]"

    header_lines = [
        "=" * 80,
        f" {BOLD}PROJ7K PRACTICE GENERATOR & DOWNSCALER REPORT{RESET}",
        "=" * 80,
        f" Beatmap    : {CYAN}{title_str}{RESET}",
        f" Practice   : {CYAN}{down_bm.version}{RESET}",
        f" File Output: {out_file_str}",
    ]
    if osz_path:
        header_lines.append(f" OSZ Package: {CYAN}{osz_path.name}{RESET} ({osz_path})")
    header_lines.extend([
        f" Target Dan : {YELLOW}{target.target_dan}{RESET} (Star Target: {target.target_sr:.2f}★ | Level D*: {target.target_D:.2f})",
        f" Mode       : {mode_str}",
        "-" * 80,
    ])
    lines = list(header_lines)
    lines.extend([
        f" {BOLD}{'METRIC COMPARISON':<28} {'ORIGINAL':<16} {'PRACTICE':<16} {'DELTA / STATUS':<16}{RESET}",
        "-" * 80,
        f" Star Rating (engine)          {orig_sr:5.2f}★           {GREEN}{down_sr:5.2f}★{RESET}           {sr_delta:+5.2f}★ ({sr_pct:+.1f}%)",
        f" Notes Count                  {orig_notes:<16} {GREEN}{down_notes:<16}{RESET} {notes_delta:+d} ({notes_pct:+.1f}%)",
        f" Level D (Hz)                 {p_orig.total_D:5.2f}           {GREEN}{p_down.total_D:5.2f}{RESET}           {p_down.total_D - p_orig.total_D:+5.2f}",
        f" Bimanual Flux (L:R)          {orig_flux_str:<16} {GREEN}{down_flux_str:<16}{RESET} Balanced",
        f" Dominant Technique           {MAGENTA}{dom_orig.capitalize()} ({dom_score_orig:.2f}★){RESET}    {MAGENTA}{dom_down.capitalize()} ({dom_score_down:.2f}★){RESET}    {dom_status}",
        f" Skill-star Cosine Similarity     -                {GREEN}{cos_sim:.3f}{RESET}            {cos_status}",
        f" Validation Outcome           -                {val_status}",
        "-" * 80,
        f" {BOLD}8-SKILL COMPARISON (difficulty engine stars){RESET}",
        f" {'Dimension':<18} {'Original':<12} {'Practice':<12} {'Practice Visual Gauge':<20}",
        "-" * 80,
    ])

    skill_labels = {
        "rc_jack": "Jack", "rc_tech": "Tech", "rc_speed": "Speed", "rc_stamina": "Stream",
        "ln_general": "LN General", "ln_tech": "LN Tech", "ln_inverse": "LN Inverse", "ln_release": "LN Release",
    }
    dimensions: List[Tuple[str, float, float]] = [
        (label, p_orig.skills[skill].stars, p_down.skills[skill].stars) for skill, label in skill_labels.items()
    ]

    max_val = max(1.0, max(p_orig.total_stars, p_down.total_stars))
    for name, v_orig, v_down in dimensions:
        bar = _make_ascii_bar(v_down, max_val=max_val, width=16)
        color = GREEN if v_down <= v_orig else RED
        lines.append(f" {name:<18} {v_orig:5.2f}★       {color}{v_down:5.2f}★{RESET}       {bar}")

    if result.suggest_free_mode:
        lines.append("-" * 80)
        lines.append(
            f" {YELLOW}Target not reached while keeping the dominant technique "
            f"(stopped at {down_sr:.2f}★, target {target.target_sr:.2f}★).{RESET}"
        )
        lines.append(f" {YELLOW}Re-run with --mode free to drop that constraint and chase the target only.{RESET}")

    if sync_result:
        lines.append("-" * 80)
        lines.append(f" {BOLD}OSU!LAZER CLIENT SYNCHRONIZATION{RESET}")
        if sync_result.success:
            lines.append(f" Status       : {GREEN}SUCCESS{RESET}")
            lines.append(f" Collection   : {CYAN}{sync_result.collection_name}{RESET}")
            lines.append(f" Injected MD5s: {len(sync_result.synced_hashes)} practice chart(s)")
            lines.append(f" Realm Records: {sync_result.updated_in_realm} record(s) updated")
        else:
            lines.append(f" Status       : {RED}FAILED{RESET} ({sync_result.error})")

    lines.append("=" * 80)
    return "\n".join(lines)


def sync_practice_beatmaps_to_lazer(
    results: Sequence[DownscaleResult],
    realm_path: Optional[Path] = None,
    lock_path: Optional[Path] = None,
    bridge_client: Optional[RealmBridgeClient] = None,
    timeout_s: float = 10.0,
) -> LazerPracticeSyncResult:
    """
    Synchronizes downscaled practice beatmaps into osu!lazer database via Realm Bridge:
    - Verifies SafeFlushWindow lock availability.
    - Resolves existing records in Realm and updates StarRating and Binned Skill Tags.
    - Injects beatmap MD5 hashes into the '7K Practice' collection atomically.
    """
    target_realm = realm_path or DEFAULT_REALM_PATH
    target_lock = lock_path or (
        target_realm.parent / "client.realm.lock"
        if target_realm.name == "client.realm"
        else DEFAULT_LOCK_PATH
    )

    if not target_realm.exists():
        return LazerPracticeSyncResult(
            success=False,
            error=f"osu!lazer Realm database not found at '{target_realm}'",
        )

    client = bridge_client or RealmBridgeClient(default_realm_path=target_realm)

    # 1. Acquire SafeFlushWindow
    with SafeFlushWindow(target_lock, timeout_s=timeout_s, raise_on_busy=False) as win:
        if not win.is_acquired:
            return LazerPracticeSyncResult(
                success=False,
                error=(
                    f"Safe flush window is closed: osu!lazer database lock at '{target_lock}' "
                    "is held by the game process."
                ),
            )

    # 2. Match existing Realm beatmap records to annotate tags and StarRating
    existing_records: List[LazerBeatmapRecord] = []
    try:
        existing_records = client.dump_7k_beatmaps(realm_path=target_realm, auto_setup=True)
    except Exception as e:
        logger.warning(f"Could not dump existing Realm 7K beatmaps for annotation: {e}")

    rec_by_md5: Dict[str, LazerBeatmapRecord] = {}
    for r in existing_records:
        if r.md5_hash:
            rec_by_md5[r.md5_hash] = r
        if r.hash:
            rec_by_md5[r.hash] = r

    updates: List[BeatmapMutationPayload] = []
    practice_hashes: List[str] = []

    for res in results:
        bm = res.downscaled_beatmap
        bm_md5 = bm.md5
        if not bm_md5:
            bm_md5 = hashlib.md5(dump_osu_7k(bm).encode("utf-8")).hexdigest()
        practice_hashes.append(bm_md5)

        dom_tech = SKILL_TECH_KEY[res.downscaled_profile.dominant_skill]
        # The library carries the difficulty engine's scale (the daemon stamps it), the same one the loop steered by.
        sr = res.downscaled_profile.total_stars

        # If beatmap already exists in Realm, create mutation payload
        if bm_md5 in rec_by_md5:
            rec = rec_by_md5[bm_md5]
            new_tags = inject_binned_skill_tags(rec.tags, dom_tech, sr)
            updates.append(
                BeatmapMutationPayload(
                    id=rec.id,
                    star_rating=sr,
                    difficulty_name=rec.difficulty_name,
                    tags=new_tags,
                )
            )

    # 3. Retrieve existing '7K Practice' collection hashes to preserve prior practice charts
    existing_practice_hashes: List[str] = []
    try:
        existing_cols = client.dump_collections(realm_path=target_realm, auto_setup=False)
        if "7K Practice" in existing_cols:
            existing_practice_hashes = existing_cols["7K Practice"]
    except Exception as e:
        logger.debug(f"Could not dump existing collections: {e}")

    col_map: Dict[str, List[str]] = {
        "7K Practice": list(set(existing_practice_hashes + practice_hashes)),
    }

    try:
        update_res = client.apply_batch_update(
            updates=updates,
            collections=col_map,
            realm_path=target_realm,
            auto_setup=True,
        )
        if not update_res.success:
            return LazerPracticeSyncResult(
                success=False,
                synced_hashes=practice_hashes,
                error=f"Realm update-batch failed: {update_res.error}",
            )
        return LazerPracticeSyncResult(
            success=True,
            collection_name="7K Practice",
            synced_hashes=practice_hashes,
            updated_in_realm=update_res.updated_count,
        )
    except Exception as e:
        return LazerPracticeSyncResult(
            success=False,
            synced_hashes=practice_hashes,
            error=f"Exception during Realm bridge update: {e}",
        )


def discover_beatmap_files(input_path: Path) -> List[Path]:
    """Finds all candidate 7K .osu beatmap files from a file or directory."""
    if input_path.is_file():
        if input_path.suffix.lower() == ".osu":
            return [input_path]
        return []

    if input_path.is_dir():
        candidates = []
        for root, _, files in os.walk(input_path):
            for file in files:
                if file.lower().endswith(".osu"):
                    # Exclude already downscaled derivative practice beatmaps
                    if file.startswith("[P-") or "proj7k_downscaled" in file:
                        continue
                    candidates.append(Path(root) / file)
        return sorted(candidates)

    return []


def process_beatmap_file(
    osu_path: Path,
    options: DownscaleOptions,
    output_dir: Optional[Path] = None,
    explicit_output: Optional[Path] = None,
    dry_run: bool = False,
) -> Tuple[DownscaleResult, Optional[Path]]:
    """Downscales a single .osu beatmap file and exports derivative practice chart."""
    content = osu_path.read_text(encoding="utf-8", errors="replace")
    beatmap = parse_osu_7k(content)

    if beatmap.mode != 3 or beatmap.circle_size != 7:
        raise ValueError(
            f"File '{osu_path.name}' is not an osu!mania 7K chart (Mode: {beatmap.mode}, CS: {beatmap.circle_size})"
        )

    # Attach original MD5 if not set
    if not beatmap.md5:
        beatmap.md5 = hashlib.md5(content.encode("utf-8")).hexdigest()

    result = downscale_beatmap(beatmap, options=options)

    # Determine destination path
    dest_path: Optional[Path] = None
    if not dry_run:
        if explicit_output:
            dest_path = explicit_output
        else:
            target_dir = output_dir if output_dir is not None else osu_path.parent
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = generate_practice_filename(osu_path.stem, result.downscaled_beatmap.version, beatmap=beatmap)
            dest_path = target_dir / filename

        dumped_text = dump_osu_7k(result.downscaled_beatmap)
        dest_path.write_text(dumped_text, encoding="utf-8")

    return result, dest_path


@dataclass
class DownscaleRun:
    """What one downscaler invocation produced: per chart (result, written .osu, source .osu), the packages, the sync."""
    results: List[Tuple[DownscaleResult, Optional[Path], Path]] = field(default_factory=list)
    failed_count: int = 0
    osz_map: Dict[Path, Path] = field(default_factory=dict)
    sync_result: Optional[LazerPracticeSyncResult] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        sync_result = self.sync_result
        return {
            "total_processed": len(self.results),
            "failed_count": self.failed_count,
            "results": [
                {
                    "report": res.to_dict(),
                    "output_file": str(out_p) if out_p else None,
                    "osz_file": str(self.osz_map.get(out_p)) if (out_p and out_p in self.osz_map) else None,
                }
                for res, out_p, _ in self.results
            ],
            "sync": (
                {
                    "success": sync_result.success,
                    "collection": sync_result.collection_name,
                    "synced_hashes": sync_result.synced_hashes,
                    "updated_in_realm": sync_result.updated_in_realm,
                }
                if sync_result
                else None
            ),
        }


def run_downscale(
    input_ref: str,
    options: DownscaleOptions,
    output_dir: Optional[Path] = None,
    output: Optional[Path] = None,
    osz_output: Optional[Path] = None,
    package: bool = True,
    dry_run: bool = False,
    sync_lazer: bool = False,
    realm_path: Path = DEFAULT_REALM_PATH,
    lock_path: Optional[Path] = None,
) -> DownscaleRun:
    """
    Downscale `input_ref` (an .osu file, a directory of them, an osu! URL / beatmap ID, or the MD5 of a chart in
    the osu!lazer library), write the practice
    charts, package them as .osz unless told not to, and optionally sync them into osu!lazer. The CLI and the
    dashboard both run this; a failure that stops the run comes back as `error`.
    """
    input_str = str(input_ref).strip()
    input_path = Path(input_str)

    resolved_asset: Optional[ResolvedBeatmapAsset] = None
    beatmap_files: List[Path] = []

    is_url_or_id = (
        input_str.startswith("http://")
        or input_str.startswith("https://")
        or input_str.isdigit()
        or (not input_path.exists() and ("osu.ppy.sh" in input_str or MD5_PATTERN.match(input_str) is not None))
    )

    if is_url_or_id:
        logger.info(f"Resolving beatmap asset from URL or ID: {input_str}")
        resolved_asset = locate_beatmap_in_lazer(input_str, realm_path=realm_path)
        if resolved_asset and resolved_asset.osu_path and resolved_asset.osu_path.exists():
            beatmap_files = [resolved_asset.osu_path]
        else:
            url_info = parse_osu_url_or_id(input_str)
            if url_info.beatmap_id:
                fallback_dir = output_dir or Path("practice_maps")
                web_path = fetch_beatmap_from_web(url_info, output_dir=fallback_dir)
                if web_path and web_path.exists():
                    beatmap_files = [web_path]
            if not beatmap_files:
                return DownscaleRun(error=f"Could not locate beatmap for '{input_str}' in osu!lazer database or web.")
        if resolved_asset is not None:
            logger.info(f"Found in osu!lazer: {resolved_asset.artist} - {resolved_asset.title} [{resolved_asset.difficulty_name}]")
    else:
        if not input_path.exists():
            return DownscaleRun(error=f"Input path does not exist: '{input_path}'")
        beatmap_files = discover_beatmap_files(input_path)
        if not beatmap_files:
            return DownscaleRun(error=f"No 7K .osu beatmap files found at '{input_path}'")

    effective_output_dir = output_dir
    if effective_output_dir is None:
        effective_output_dir = Path("practice_maps")

    results: List[Tuple[DownscaleResult, Optional[Path], Path]] = []
    failed_count = 0

    for idx, path in enumerate(beatmap_files, start=1):
        try:
            res, out_path = process_beatmap_file(
                path,
                options=options,
                output_dir=effective_output_dir,
                explicit_output=output if len(beatmap_files) == 1 else None,
                dry_run=dry_run,
            )
            results.append((res, out_path, path))
        except Exception as e:
            logger.error(f"Failed to downscale '{path}': {e}")
            failed_count += 1

    if not results:
        return DownscaleRun(error="No beatmaps were successfully downscaled.")

    # Standalone practice .osz packaging is on unless turned off (--no-package) or a dry run
    should_package_osz = package and not dry_run

    osz_map: Dict[Path, Path] = {}
    if should_package_osz:
        for res, out_p, src_p in results:
            if not out_p or not out_p.exists():
                continue
            osz_target = osz_output if (osz_output and len(results) == 1) else out_p.with_suffix(".osz")

            # 1. Start with resolved assets if available (from URL / lazer DB lookup)
            audio_path = resolved_asset.audio_path if resolved_asset else None
            audio_filename = resolved_asset.audio_filename if resolved_asset else (res.downscaled_beatmap.audio_filename or "audio.mp3")
            bg_path = resolved_asset.bg_path if resolved_asset else None
            bg_filename = resolved_asset.bg_filename if resolved_asset else "bg.png"

            # 2. Check source directory (src_p.parent) and output directory (out_p.parent)
            search_dirs = [src_p.parent]
            if out_p.parent != src_p.parent:
                search_dirs.append(out_p.parent)

            # Audio resolution
            if not audio_path or not audio_path.exists():
                candidate_audio = None
                expected_audio = res.downscaled_beatmap.audio_filename or "audio.mp3"
                for s_dir in search_dirs:
                    if (s_dir / expected_audio).is_file():
                        candidate_audio = s_dir / expected_audio
                        audio_filename = expected_audio
                        break
                if not candidate_audio:
                    for s_dir in search_dirs:
                        if s_dir.exists():
                            for f in s_dir.iterdir():
                                if f.suffix.lower() in [".mp3", ".ogg", ".wav"]:
                                    candidate_audio = f
                                    audio_filename = f.name
                                    break
                        if candidate_audio:
                            break
                audio_path = candidate_audio

            # Background resolution
            if not bg_path or not bg_path.exists():
                candidate_bg = None
                expected_bg = None
                for ev in res.downscaled_beatmap.raw_events:
                    ev_str = ev.strip()
                    if (ev_str.startswith("0,0,") or ev_str.startswith("Video,")) and '"' in ev_str:
                        parts = ev_str.split('"')
                        if len(parts) >= 2 and parts[1].strip():
                            expected_bg = parts[1].strip()
                            break
                if expected_bg:
                    for s_dir in search_dirs:
                        if (s_dir / expected_bg).is_file():
                            candidate_bg = s_dir / expected_bg
                            bg_filename = expected_bg
                            break
                if not candidate_bg:
                    for s_dir in search_dirs:
                        if s_dir.exists():
                            for f in s_dir.iterdir():
                                if f.suffix.lower() in [".png", ".jpg", ".jpeg"]:
                                    candidate_bg = f
                                    bg_filename = f.name
                                    break
                        if candidate_bg:
                            break
                bg_path = candidate_bg

            try:
                created_osz = package_into_osz(
                    practice_osu_path=out_p,
                    output_osz_path=osz_target,
                    audio_path=audio_path,
                    audio_filename=audio_filename,
                    bg_path=bg_path,
                    bg_filename=bg_filename,
                )
                osz_map[out_p] = created_osz
            except Exception as e:
                logger.warning(f"Could not package .osz for {out_p}: {e}")

    # Synchronization with osu!lazer if requested
    sync_result: Optional[LazerPracticeSyncResult] = None
    if sync_lazer and not dry_run:
        # Trigger native OS import through the .osz file association (Q3 - A)
        for osz_file in osz_map.values():
            if osz_file.exists():
                logger.info(f"Triggering osu!lazer native import via system open: {osz_file.name}")
                try:
                    open_with_system_handler(osz_file)
                except Exception as e:
                    logger.warning(f"Failed to trigger system open for {osz_file}: {e}")

        sync_result = sync_practice_beatmaps_to_lazer(
            results=[r for r, _, _ in results],
            realm_path=realm_path,
            lock_path=lock_path,
        )
        if not sync_result.success:
            return DownscaleRun(results=results, failed_count=failed_count, osz_map=osz_map, sync_result=sync_result, error=f"osu!lazer synchronization failed: {sync_result.error}")

    return DownscaleRun(results=results, failed_count=failed_count, osz_map=osz_map, sync_result=sync_result)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.target_dan and args.target_sr is None and args.target_d is None:
        print(
            "Error: At least one of --target-dan, --target-sr, or --target-d must be specified.",
            file=sys.stderr,
        )
        return 1

    downscale_opts = DownscaleOptions(
        target_dan=args.target_dan,
        target_sr=args.target_sr,
        target_D=args.target_d,
        dominant_skill=args.dominant_skill,
        prune_ratio=args.prune_ratio,
        max_iterations=args.max_iterations,
        tolerance=args.tolerance,
        min_cosine_similarity=args.min_cosine_similarity,
        preserve_technique=args.mode == "technique",
    )
    run = run_downscale(
        args.input,
        downscale_opts,
        output_dir=args.output_dir,
        output=args.output,
        osz_output=args.osz_output,
        package=not args.no_package,
        dry_run=args.dry_run,
        sync_lazer=args.sync_lazer,
        realm_path=args.realm,
        lock_path=args.lock_path,
    )
    if run.error:
        print(f"Error: {run.error}", file=sys.stderr)
        return 1
    results, osz_map, sync_result = run.results, run.osz_map, run.sync_result

    # Output formatting
    if args.json:
        output_data = run.to_dict()
        print(json.dumps(output_data, indent=2, ensure_ascii=False))
    else:
        for res, out_path, _ in results:
            osz_p = osz_map.get(out_path) if out_path else None
            print(format_downscale_report(res, output_path=out_path, sync_result=sync_result, osz_path=osz_p))

    return 0


if __name__ == "__main__":
    sys.exit(main())
