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
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from proj7k.parser import Beatmap7K, dump_osu_7k, parse_osu_7k
from proj7k.radar import TechniqueRadar
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
        description="proj7k - Closed-loop strain downscaler and derivative practice beatmap generator (SPEC-P5.1-04 / ADR-0011).",
    )

    req_group = parser.add_argument_group("Input & Target Configuration")
    req_group.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="Path to an .osu beatmap file or a directory containing .osu beatmaps to downscale.",
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
        help="Target continuous Star Rating (e.g. 6.5). Overrides or interpolates Dan.",
    )
    req_group.add_argument(
        "--target-strain",
        type=float,
        default=None,
        help="Explicit strain target override S_target.",
    )
    req_group.add_argument(
        "--dominant-skill",
        type=str,
        default=None,
        help="Override dominant skill dimension (e.g. 'jack', 'tech', 'stream', 'ln_inverse').",
    )

    out_group = parser.add_argument_group("Output Options")
    out_group.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to write derivative practice beatmap(s). Defaults to same folder as original.",
    )
    out_group.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Explicit output path for single beatmap downscaling.",
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
        help="Maximum closed-loop strain damping iterations (default: 25).",
    )
    opt_group.add_argument(
        "--tolerance",
        type=float,
        default=0.05,
        help="Strain convergence tolerance epsilon (default: 0.05 = 5%%).",
    )
    opt_group.add_argument(
        "--min-cosine-similarity",
        type=float,
        default=0.80,
        help="Minimum 8D technique radar cosine similarity preservation gate (default: 0.80).",
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


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent illegal filesystem characters on OS platforms."""
    return re.sub(r'[\\/*?:"<>|]', "_", filename)


def generate_practice_filename(original_stem: str, version: str) -> str:
    """
    Constructs a canonical practice beatmap filename:
    Replaces existing `[Version]` with `[{version}]` or appends `[{version}]`.
    """
    clean_version = sanitize_filename(version)
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
) -> str:
    """Formats an ANSI-colored summary card with tabular metrics and technique radar comparison."""
    orig_bm = result.original_beatmap
    down_bm = result.downscaled_beatmap
    target = result.target

    orig_sr = result.original_rating.star_rating
    down_sr = result.downscaled_rating.star_rating
    sr_delta = down_sr - orig_sr
    sr_pct = (sr_delta / max(0.01, orig_sr)) * 100.0

    orig_notes = len(orig_bm.hit_objects)
    down_notes = len(down_bm.hit_objects)
    notes_delta = down_notes - orig_notes
    notes_pct = (notes_delta / max(1, orig_notes)) * 100.0

    orig_strain = result.original_strain
    down_strain = result.downscaled_strain

    balancer = BimanualFluxBalancer()
    orig_flux = balancer.compute_flux_ratio(orig_bm.hit_objects)
    orig_flux_str = f"{orig_flux[0] * 100.0:.1f}% : {orig_flux[1] * 100.0:.1f}%"
    down_flux = result.bimanual_flux_ratio
    down_flux_str = f"{down_flux[0] * 100.0:.1f}% : {down_flux[1] * 100.0:.1f}%"

    dom_orig = result.original_radar.dominant_technique
    dom_down = result.downscaled_radar.dominant_technique
    dom_score_orig = result.original_radar.dominant_score
    dom_score_down = result.downscaled_radar.dominant_score

    cos_sim = result.validation.cosine_similarity
    val_status = f"{GREEN}PASSED{RESET}" if result.validation.passed else f"{RED}FAILED{RESET}"
    dom_status = f"{GREEN}Preserved (Rank 1){RESET}" if result.validation.dominant_conserved else f"{RED}Shifted{RESET}"

    out_file_str = str(output_path.name) if output_path else "InMemory / DryRun"

    title_str = f"{orig_bm.artist} - {orig_bm.title} [{orig_bm.version}]"

    lines = [
        "=" * 80,
        f" {BOLD}PROJ7K PRACTICE GENERATOR & DOWNSCALER REPORT{RESET}",
        "=" * 80,
        f" Beatmap    : {CYAN}{title_str}{RESET}",
        f" Practice   : {CYAN}{down_bm.version}{RESET}",
        f" File Output: {out_file_str}",
        f" Target Dan : {YELLOW}{target.target_dan}{RESET} (SR Target: {target.target_sr:.2f}★ | Strain Target: {target.target_strain:.2f})",
        "-" * 80,
        f" {BOLD}{'METRIC COMPARISON':<28} {'ORIGINAL':<16} {'PRACTICE':<16} {'DELTA / STATUS':<16}{RESET}",
        "-" * 80,
        f" Star Rating                  {orig_sr:5.2f}★           {GREEN}{down_sr:5.2f}★{RESET}           {sr_delta:+5.2f}★ ({sr_pct:+.1f}%)",
        f" Notes Count                  {orig_notes:<16} {GREEN}{down_notes:<16}{RESET} {notes_delta:+d} ({notes_pct:+.1f}%)",
        f" P90 Strain                   {orig_strain.p90_strain:5.2f}           {GREEN}{down_strain.p90_strain:5.2f}{RESET}           {down_strain.p90_strain - orig_strain.p90_strain:+5.2f}",
        f" Peak Strain                  {orig_strain.peak_strain:5.2f}           {GREEN}{down_strain.peak_strain:5.2f}{RESET}           {down_strain.peak_strain - orig_strain.peak_strain:+5.2f}",
        f" Bimanual Flux (L:R)          {orig_flux_str:<16} {GREEN}{down_flux_str:<16}{RESET} Balanced",
        f" Dominant Technique           {MAGENTA}{dom_orig.capitalize()} ({dom_score_orig:.2f}★){RESET}    {MAGENTA}{dom_down.capitalize()} ({dom_score_down:.2f}★){RESET}    {dom_status}",
        f" Radar Cosine Similarity      -                {GREEN}{cos_sim:.3f}{RESET}            {'Passed (>= 0.80)' if result.validation.gate1_passed else 'Failed (< 0.80)'}",
        f" Validation Outcome           -                {val_status}",
        "-" * 80,
        f" {BOLD}8-DIMENSION TECHNIQUE RADAR COMPARISON{RESET}",
        f" {'Dimension':<18} {'Original':<12} {'Practice':<12} {'Practice Visual Gauge':<20}",
        "-" * 80,
    ]

    r_orig: TechniqueRadar = result.original_radar
    r_down: TechniqueRadar = result.downscaled_radar
    dimensions: List[Tuple[str, float, float]] = [
        ("Jack", r_orig.jack, r_down.jack),
        ("Tech", r_orig.tech, r_down.tech),
        ("Speed", r_orig.speed, r_down.speed),
        ("Stream", r_orig.stream, r_down.stream),
        ("LN General", r_orig.ln_general, r_down.ln_general),
        ("LN Tech", r_orig.ln_tech, r_down.ln_tech),
        ("LN Inverse", r_orig.ln_inverse, r_down.ln_inverse),
        ("LN Release", r_orig.ln_release, r_down.ln_release),
    ]

    max_val = max(1.0, max(orig_sr, down_sr))
    for name, v_orig, v_down in dimensions:
        bar = _make_ascii_bar(v_down, max_val=max_val, width=16)
        color = GREEN if v_down <= v_orig else RED
        lines.append(f" {name:<18} {v_orig:5.2f}★       {color}{v_down:5.2f}★{RESET}       {bar}")

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

        dom_tech = res.downscaled_radar.dominant_technique
        sr = res.downscaled_rating.star_rating

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
            filename = generate_practice_filename(osu_path.stem, result.downscaled_beatmap.version)
            dest_path = target_dir / filename

        dumped_text = dump_osu_7k(result.downscaled_beatmap)
        dest_path.write_text(dumped_text, encoding="utf-8")

    return result, dest_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.target_dan and args.target_sr is None and args.target_strain is None:
        print(
            "Error: At least one of --target-dan, --target-sr, or --target-strain must be specified.",
            file=sys.stderr,
        )
        return 1

    input_path = args.input
    if not input_path.exists():
        print(f"Error: Input path does not exist: '{input_path}'", file=sys.stderr)
        return 1

    beatmap_files = discover_beatmap_files(input_path)
    if not beatmap_files:
        print(f"Error: No 7K .osu beatmap files found at '{input_path}'", file=sys.stderr)
        return 1

    downscale_opts = DownscaleOptions(
        target_dan=args.target_dan,
        target_sr=args.target_sr,
        target_strain=args.target_strain,
        dominant_skill=args.dominant_skill,
        prune_ratio=args.prune_ratio,
        max_iterations=args.max_iterations,
        tolerance=args.tolerance,
        min_cosine_similarity=args.min_cosine_similarity,
    )

    results: List[Tuple[DownscaleResult, Optional[Path]]] = []
    failed_count = 0

    for idx, path in enumerate(beatmap_files, start=1):
        try:
            res, out_path = process_beatmap_file(
                path,
                options=downscale_opts,
                output_dir=args.output_dir,
                explicit_output=args.output if len(beatmap_files) == 1 else None,
                dry_run=args.dry_run,
            )
            results.append((res, out_path))
        except Exception as e:
            logger.error(f"Failed to downscale '{path}': {e}")
            failed_count += 1

    if not results:
        print("Error: No beatmaps were successfully downscaled.", file=sys.stderr)
        return 1

    # Synchronization with osu!lazer if requested
    sync_result: Optional[LazerPracticeSyncResult] = None
    if args.sync_lazer and not args.dry_run:
        sync_result = sync_practice_beatmaps_to_lazer(
            results=[r for r, _ in results],
            realm_path=args.realm,
            lock_path=args.lock_path,
        )
        if not sync_result.success:
            print(f"Error during osu!lazer synchronization: {sync_result.error}", file=sys.stderr)
            return 1

    # Output formatting
    if args.json:
        output_data = {
            "total_processed": len(results),
            "failed_count": failed_count,
            "results": [
                {
                    "report": res.to_dict(),
                    "output_file": str(out_p) if out_p else None,
                }
                for res, out_p in results
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
        print(json.dumps(output_data, indent=2, ensure_ascii=False))
    else:
        for res, out_path in results:
            print(format_downscale_report(res, output_path=out_path, sync_result=sync_result))

    return 0


if __name__ == "__main__":
    sys.exit(main())
