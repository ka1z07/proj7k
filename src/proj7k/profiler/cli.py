"""
CLI entrypoint, batch ingestion runner, and macro profile query interface for 7K Player Profiler.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

from proj7k.parser import Beatmap7K, parse_osu_7k
from proj7k.profiler.aggregate import (
    DimensionMacroMetric,
    MacroProfile,
    aggregate_macro_profile,
)
from proj7k.profiler.coach import (
    CandidateBeatmap,
    CoachingRecommendation,
    CoachingStrategy,
    PracticeBundleResult,
    format_bundle_report,
    format_coaching_report,
    generate_coaching_recommendations,
    generate_targeted_practice_bundle,
)
from proj7k.profiler.matcher import (
    HitAlignmentResult,
    HitJudgment,
    align_replay_hits,
    column_to_canonical_lane,
)
from proj7k.profiler.osr import OSRReplay, parse_osr
from proj7k.profiler.pathology import PathologyReport, analyze_pathology
from proj7k.profiler.response import SkillRadarReport, analyze_strain_response
from proj7k.profiler.storage import (
    MatchSnapshot,
    ProfilerStorage,
    build_snapshot_from_report,
    infer_is_failed,
    is_noise_match,
)


@dataclass
class ProfilerIngestionReport:
    """
    Structured ingestion, alignment, pathology, and skill radar report.
    """
    player_name: str
    beatmap_hash: str
    actual_beatmap_hash: str
    hash_matched: bool
    game_version: int
    mods: int
    timestamp_ticks: int
    official_counts: Dict[str, int]
    total_notes: int
    total_hits: int
    miss_count: int
    ghost_tap_count: int
    play_duration_s: float
    completion_rate: float
    is_valid_play: bool  # >= 30s and >= 50% completion (ADR-0012)
    judgment_counts: Dict[HitJudgment, int]
    ghost_taps_by_column: Dict[int, int]
    alignment_result: HitAlignmentResult
    pathology: Optional[PathologyReport] = None
    skill_radar: Optional[SkillRadarReport] = None
    replay_hash: str = ""
    life_bar: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_name": self.player_name,
            "beatmap_hash": self.beatmap_hash,
            "actual_beatmap_hash": self.actual_beatmap_hash,
            "hash_matched": self.hash_matched,
            "game_version": self.game_version,
            "mods": self.mods,
            "timestamp_ticks": self.timestamp_ticks,
            "official_counts": self.official_counts,
            "total_notes": self.total_notes,
            "total_hits": self.total_hits,
            "miss_count": self.miss_count,
            "ghost_tap_count": self.ghost_tap_count,
            "play_duration_s": round(self.play_duration_s, 2),
            "completion_rate": round(self.completion_rate, 4),
            "is_valid_play": self.is_valid_play,
            "judgment_counts": {k.value: v for k, v in self.judgment_counts.items()},
            "ghost_taps_by_column": {
                column_to_canonical_lane(col): cnt
                for col, cnt in self.ghost_taps_by_column.items()
            },
            "aligned_hits": [h.to_dict() for h in self.alignment_result.aligned_hits],
            "ghost_taps": [g.to_dict() for g in self.alignment_result.ghost_taps],
            "pathology": self.pathology.to_dict() if self.pathology else None,
            "skill_radar": self.skill_radar.to_dict() if self.skill_radar else None,
        }


def _determine_clock_rate(mods: int) -> float:
    """
    Determines gameplay clock rate multiplier based on mods bitmask.
    DoubleTime (64) / Nightcore (512): 1.5x
    HalfTime (256): 0.75x
    """
    if mods & (64 | 512):
        return 1.5
    if mods & 256:
        return 0.75
    return 1.0


def _scale_beatmap_clock_rate(beatmap: Beatmap7K, clock_rate: float) -> Beatmap7K:
    """Scales beatmap hit object and timing point timestamps to real physical playback time."""
    if abs(clock_rate - 1.0) < 1e-4:
        return beatmap
    import copy
    bm = copy.deepcopy(beatmap)
    for ho in bm.hit_objects:
        ho.time = ho.time / clock_rate
        if ho.end_time is not None:
            ho.end_time = ho.end_time / clock_rate
    for tp in bm.timing_points:
        tp.time = tp.time / clock_rate
        if tp.beat_length > 0:
            tp.beat_length = tp.beat_length / clock_rate
    return bm


def _scale_alignment_clock_rate(alignment: HitAlignmentResult, clock_rate: float) -> HitAlignmentResult:
    """Scales hit alignment target and hit timestamps to match scaled beatmap physical time."""
    if abs(clock_rate - 1.0) < 1e-4:
        return alignment
    import copy
    scaled_hits = []
    for h in alignment.aligned_hits:
        sh = copy.copy(h)
        sh.target_time = h.target_time / clock_rate
        if h.hit_time is not None:
            sh.hit_time = h.hit_time / clock_rate
        scaled_hits.append(sh)
    return HitAlignmentResult(
        aligned_hits=scaled_hits,
        ghost_taps=alignment.ghost_taps,
        judgment_counts=alignment.judgment_counts,
        total_hits=alignment.total_hits,
        miss_count=alignment.miss_count,
        total_ghost_taps=alignment.total_ghost_taps,
        ghost_taps_by_column=alignment.ghost_taps_by_column,
    )


def run_ingestion(
    replay_path: Path | str,
    beatmap_path: Path | str,
) -> ProfilerIngestionReport:
    """
    Parses replay and beatmap, checks hash integrity, and executes causal hit alignment.
    """
    replay_p = Path(replay_path)
    beatmap_p = Path(beatmap_path)

    if not replay_p.exists():
        raise FileNotFoundError(f"Replay file not found: {replay_p}")
    if not beatmap_p.exists():
        raise FileNotFoundError(f"Beatmap file not found: {beatmap_p}")

    # 1. Parse OSR Replay
    replay: OSRReplay = parse_osr(replay_p)

    # 2. Compute beatmap MD5 and parse beatmap
    beatmap_bytes = beatmap_p.read_bytes()
    actual_hash = hashlib.md5(beatmap_bytes).hexdigest().lower()
    replay_hash = replay.beatmap_hash.lower()
    hash_matched = (not replay_hash) or (replay_hash == actual_hash)

    beatmap = parse_osu_7k(str(beatmap_p))

    # 3. Causally align hits
    clock_rate = _determine_clock_rate(replay.mods)
    alignment: HitAlignmentResult = align_replay_hits(
        beatmap,
        replay.action_frames,
        clock_rate=clock_rate,
    )

    # 4. Play duration and completion metrics (ADR-0012 data quality standards)
    play_duration_s = 0.0
    if replay.action_frames:
        play_duration_s = (replay.action_frames[-1].time_ms - replay.action_frames[0].time_ms) / 1000.0
        play_duration_s = max(0.0, play_duration_s)

    total_notes = len(beatmap.hit_objects)
    completion_rate = (alignment.total_hits / total_notes) if total_notes > 0 else 0.0
    is_valid_play = (play_duration_s >= 30.0) and (completion_rate >= 0.50)

    official_counts = {
        "300g": replay.c300g,
        "300": replay.c300,
        "200": replay.c200,
        "100": replay.c100,
        "50": replay.c50,
        "miss": replay.miss,
    }


    # 5. Micro-Biomechanics & Pathology Analysis (ADR-0012)
    pathology = analyze_pathology(alignment, beatmap)

    # 6. Strain-Error Response & 8-Dim Dan Radar (ADR-0012)
    if abs(clock_rate - 1.0) > 1e-4:
        bm_strain = _scale_beatmap_clock_rate(beatmap, clock_rate)
        align_strain = _scale_alignment_clock_rate(alignment, clock_rate)
        skill_radar = analyze_strain_response(align_strain, bm_strain)
    else:
        skill_radar = analyze_strain_response(alignment, beatmap)

    return ProfilerIngestionReport(
        player_name=replay.player_name,
        beatmap_hash=replay.beatmap_hash,
        actual_beatmap_hash=actual_hash,
        hash_matched=hash_matched,
        game_version=replay.game_version,
        mods=replay.mods,
        timestamp_ticks=replay.timestamp_ticks,
        official_counts=official_counts,
        total_notes=total_notes,
        total_hits=alignment.total_hits,
        miss_count=alignment.miss_count,
        ghost_tap_count=alignment.total_ghost_taps,
        play_duration_s=play_duration_s,
        completion_rate=completion_rate,
        is_valid_play=is_valid_play,
        judgment_counts=alignment.judgment_counts,
        ghost_taps_by_column=alignment.ghost_taps_by_column,
        alignment_result=alignment,
        pathology=pathology,
        skill_radar=skill_radar,
        replay_hash=replay.replay_hash or replay_p.stem,
        life_bar=replay.life_bar,
    )


run_profiler = run_ingestion


def format_technique_title(dim: str) -> str:
    """Formats canonical technique identifier into human-readable capitalized title."""
    return dim.replace("_", " ").title()


def format_ingestion_report(report: ProfilerIngestionReport) -> str:
    """
    Formats the ingestion report as a clean terminal summary with canonical topological lanes.
    """
    lines = [
        "============================================================",
        "          proj7k Player Replay Diagnostic Ingestion         ",
        "============================================================",
        f"Player:       {report.player_name}",
        f"Beatmap Hash: {report.actual_beatmap_hash[:8]}... "
        + ("(MATCHED)" if report.hash_matched else "(MISMATCH WARNING)"),
        f"Play Time:    {report.play_duration_s:.1f}s | Completion: {report.completion_rate * 100:.1f}%",
        f"Total Notes:  {report.total_notes} | Processed Hits: {report.total_hits}",
        "------------------------------------------------------------",
        "Official Replay Header Counts:",
        f"  MAX: {report.official_counts['300g']} | 300: {report.official_counts['300']} | "
        f"200: {report.official_counts['200']} | 100: {report.official_counts['100']} | "
        f"50: {report.official_counts['50']} | Miss: {report.official_counts['miss']}",
        "------------------------------------------------------------",
        "Causal Hit Alignment Distribution:",
    ]

    for judg in HitJudgment:
        count = report.judgment_counts.get(judg, 0)
        pct = (count / report.total_notes * 100.0) if report.total_notes > 0 else 0.0
        lines.append(f"  {judg.value:<6}: {count:>5}  ({pct:>5.1f}%)")

    lines.extend([
        "------------------------------------------------------------",
        f"Panic Ghost Taps: {report.ghost_tap_count}",
    ])

    col_details = ", ".join(
        f"{column_to_canonical_lane(col)}: {cnt}"
        for col, cnt in sorted(report.ghost_taps_by_column.items())
        if cnt > 0
    )
    if col_details:
        lines.append(f"  By Lane: {col_details}")
    else:
        lines.append("  (None detected)")

    if not report.is_valid_play:
        lines.append("  [NOTICE] Aborted or retry run (<30s or <50% completion; filtered from baseline)")

    if report.pathology:
        path = report.pathology
        lines.extend([
            "------------------------------------------------------------",
            "Micro-Pathology Diagnostics:",
            "  Per-Track Variance & Timing Error:",
            "    Lane | Hits | Mean Error | Std Dev |    UR",
            "    -----+------+------------+---------+------",
        ])
        for lane, trk in path.tracks.items():
            lines.append(
                f"    {lane:<4} | {trk.hit_count:>4} | {trk.mean_error_ms:>+9.1f}ms | {trk.std_error_ms:>6.1f}ms | {trk.ur:>5.1f}"
            )
        lines.extend([
            "  Bimanual Load & UR:",
            f"    Left  Hand (L3..L1): UR {path.bimanual.left_ur:>5.1f} | Hits: {path.bimanual.left_hit_count:>4} | Mean: {path.bimanual.left_mean_error_ms:+.1f}ms",
            f"    Right Hand (R1..R3): UR {path.bimanual.right_ur:>5.1f} | Hits: {path.bimanual.right_hit_count:>4} | Mean: {path.bimanual.right_mean_error_ms:+.1f}ms",
            f"    Load Asymmetry:      {path.bimanual.load_asymmetry_ratio:.2f} | UR Asymmetry: {path.bimanual.ur_asymmetry_ratio:.2f}",
            "  Jack Stagnation Drift:",
            f"    Drift Slope:  {path.jack_drift.slope_ms_per_s:+.3f} ms/s | R²: {path.jack_drift.r_squared:.3f} | Notes: {path.jack_drift.stagnation_jack_count}",
            f"    Exhaustion:   {'ALERT (Fatigue drift detected)' if path.jack_drift.fatigue_alert else 'Normal'}",
            "  LN Release Decoupling:",
            f"    Total LNs:    {path.ln_release.total_lns} | Head UR: {path.ln_release.head_ur:.1f} | Tail UR: {path.ln_release.tail_ur:.1f}",
            f"    Tail Offset:  {path.ln_release.mean_tail_offset_ms:+.1f}ms (Early Panic: {path.ln_release.panic_release_count}, Sticky: {path.ln_release.sticky_count})",
        ])
        if path.cascade_precursor and path.cascade_precursor.fatal_time_ms is not None:
            pre = path.cascade_precursor
            lines.extend([
                "  Cascade Failure Precursor:",
                f"    Fatal Break:  At {pre.fatal_time_ms:.1f}ms on {column_to_canonical_lane(pre.fatal_column) if pre.fatal_column is not None else 'Unknown'}",
                f"    500ms Motif:  {pre.dominant_technique} ({pre.precursor_note_count} notes in window)",
            ])

    if report.skill_radar:
        radar = report.skill_radar
        lines.extend([
            "------------------------------------------------------------",
            "8-Dimension Skill Radar & Dan Breakdown:",
            f"  Overall Dan: {radar.overall_dan} | Dominant: {radar.dominant_technique.capitalize()} | Bottleneck: {radar.bottleneck_technique.capitalize()}",
            "  Dimension    | Capacity | Star Rating | Dan Tier  | Status",
            "  -------------+----------+-------------+-----------+-------------------------",
        ])
        for dim, cap in radar.dimensions.items():
            dim_name = format_technique_title(dim)
            if not cap.tested:
                status = f"Untested (Peak: {cap.peak_chart_strain:.1f})"
            elif cap.has_inflection:
                status = f"Inflection @ {cap.effective_capacity:.1f}"
            else:
                status = f"Stable (Peak: {cap.peak_chart_strain:.1f})"

            lines.append(
                f"  {dim_name:<12} | {cap.effective_capacity:>8.1f} | {cap.star_rating:>10.2f}★ | {cap.dan_tier:<9} | {status}"
            )

    lines.append("============================================================")
    return "\n".join(lines)


def format_macro_profile(profile: MacroProfile) -> str:
    """
    Formats the MacroProfile and historical trend comparison into a clean terminal report.
    """
    window_label = (
        "All-Time Peak Profile"
        if profile.window_mode == "all_time"
        else f"Recent Rolling Form ({int(profile.horizon_days or 30)}d)"
    )

    lines = [
        "============================================================",
        "              proj7k Macro Player Skill Profile             ",
        "============================================================",
        f"Player:             {profile.player_name}",
        f"Time Window:        {window_label}",
        f"Analyzed Matches:   {profile.total_matches} (Cleared: {profile.cleared_matches}, Failed Preserved: {profile.failed_matches})",
    ]

    if profile.average_ur is not None:
        lines.append(f"Stability Baseline UR: {profile.average_ur:.1f} (Cleared matches only)")
    else:
        lines.append("Stability Baseline UR: N/A (No cleared matches in window)")

    lines.extend([
        f"Overall Jinjin Dan: {profile.overall_dan} ({profile.overall_star_rating:.2f}★)",
        f"Dominant Technique: {profile.dominant_technique.capitalize()} | Bottleneck: {profile.bottleneck_technique.capitalize()}",
        "------------------------------------------------------------",
        "8-Dimension Skill Breakdown:",
        "  Dimension    | Peak Strain | Star Rating | Dan Tier  | Tests",
        "  -------------+-------------+-------------+-----------+------",
    ])

    for dim, metric in profile.dimensions.items():
        dim_name = format_technique_title(dim)
        lines.append(
            f"  {dim_name:<12} | {metric.peak_capacity:>11.2f} | {metric.star_rating:>10.2f}★ | {metric.dan_tier:<9} | {metric.match_count:>5}"
        )

    if profile.trend_comparison:
        tc = profile.trend_comparison
        lines.extend([
            "------------------------------------------------------------",
            "Historical Trend Comparison vs All-Time Peak:",
            f"  Overall:     {profile.overall_dan} ({profile.overall_star_rating:.2f}★) vs All-Time: {tc['all_time_overall_dan']} ({tc['all_time_overall_sr']:.2f}★)",
            "  Dimension    | Recent  | All-Time | Delta   | Status",
            "  -------------+---------+----------+---------+---------------------",
        ])
        for dim, delta_info in tc.get("dimension_deltas", {}).items():
            dim_name = format_technique_title(dim)
            sign = "+" if delta_info["delta_sr"] >= 0 else ""
            lines.append(
                f"  {dim_name:<12} | {delta_info['recent_sr']:>6.2f}★ | {delta_info['all_time_sr']:>7.2f}★ | {sign}{delta_info['delta_sr']:>5.2f}★ | {delta_info['status']}"
            )

    lines.append("============================================================")
    return "\n".join(lines)


def run_batch_ingestion(
    batch_dir: Path | str,
    beatmap_dir: Path | str,
    db_path: Optional[Path | str] = None,
    player_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ingests a batch of .osr replays mapped to .osu beatmaps, applies noise filtering,
    and stores valid snapshots into SQLite. If player_name is provided, replays
    from other players are skipped.
    """
    batch_p = Path(batch_dir)
    beatmap_p = Path(beatmap_dir)

    if not batch_p.exists():
        raise FileNotFoundError(f"Replay directory not found: {batch_p}")
    if not beatmap_p.exists():
        raise FileNotFoundError(f"Beatmap directory not found: {beatmap_p}")

    # Index beatmaps by MD5 and stem
    beatmap_by_hash: Dict[str, Path] = {}
    beatmap_by_stem: Dict[str, Path] = {}
    for osu_file in beatmap_p.glob("**/*.osu"):
        beatmap_by_stem[osu_file.stem.lower()] = osu_file
        try:
            h = hashlib.md5(osu_file.read_bytes()).hexdigest().lower()
            beatmap_by_hash[h] = osu_file
        except Exception:
            pass

    target_player_norm = player_name.strip().lower() if player_name else None
    stats = {
        "replays_processed": 0,
        "saved": 0,
        "noise_filtered": 0,
        "failed_preserved": 0,
        "skipped_other_player": 0,
        "errors": 0,
    }

    replays = list(batch_p.glob("**/*.osr"))
    with ProfilerStorage(db_path=db_path) as storage:
        for osr_file in replays:
            stats["replays_processed"] += 1
            try:
                # Parse header to locate beatmap and inspect player identity
                osr_data = parse_osr(osr_file)

                if target_player_norm is not None:
                    osr_player = (osr_data.player_name or "").strip().lower()
                    if osr_player != target_player_norm:
                        stats["skipped_other_player"] += 1
                        continue

                target_hash = osr_data.beatmap_hash.lower()
                matching_osu = beatmap_by_hash.get(target_hash)

                if matching_osu is None:
                    # Try matching by filename stem
                    matching_osu = beatmap_by_stem.get(osr_file.stem.lower())

                if matching_osu is None:
                    # No matching beatmap
                    stats["errors"] += 1
                    continue

                report = run_ingestion(osr_file, matching_osu)
                beatmap = parse_osu_7k(str(matching_osu))
                saved = storage.save_report_with_filter(report, beatmap=beatmap)

                if saved is not None:
                    stats["saved"] += 1
                    if saved.is_failed:
                        stats["failed_preserved"] += 1
                else:
                    stats["noise_filtered"] += 1
            except Exception:
                stats["errors"] += 1

    return stats


def run_replay_import(
    player_name: str,
    realm_path: Optional[Path | str] = None,
    files_dir: Optional[Path | str] = None,
    db_path: Optional[Path | str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Imports 7K mania replays for the specified player from local osu!lazer storage.

    Scans client.realm for the player's 7K scores, resolves each score's physical beatmap
    and replay files out of the lazer files directory, and ingests them into the profiler
    SQLite database with replay-hash deduplication and ADR-0012 noise filtering.
    """
    from proj7k.lazer.bridge import DEFAULT_REALM_PATH, RealmBridgeClient

    target_realm = Path(realm_path) if realm_path else DEFAULT_REALM_PATH
    if not target_realm.exists():
        raise FileNotFoundError(f"osu!lazer realm database not found at {target_realm}")

    target_files_dir = Path(files_dir) if files_dir else (target_realm.parent / "files")
    if not target_files_dir.exists():
        raise FileNotFoundError(f"osu!lazer files directory not found at {target_files_dir}")

    client = RealmBridgeClient(default_realm_path=target_realm)
    scores = client.dump_7k_scores(realm_path=target_realm, user=player_name)

    if limit is not None and limit > 0:
        scores = scores[:limit]

    stats = {
        "replays_discovered": len(scores),
        "saved": 0,
        "already_exists": 0,
        "noise_filtered": 0,
        "failed_preserved": 0,
        "missing_files": 0,
        "errors": 0,
    }

    with ProfilerStorage(db_path=db_path) as storage:
        for item in scores:
            b_hash = item.get("beatmap_file_hash", "")
            r_hash = item.get("replay_file_hash", "")

            if not b_hash or not r_hash:
                stats["missing_files"] += 1
                continue

            if storage.has_replay(r_hash):
                stats["already_exists"] += 1
                continue

            bp = target_files_dir / b_hash[0] / b_hash[:2] / b_hash
            rp = target_files_dir / r_hash[0] / r_hash[:2] / r_hash

            if not bp.exists() or not rp.exists():
                stats["missing_files"] += 1
                continue

            try:
                report = run_ingestion(rp, bp)
                # Use Lazer's physical replay file hash to guarantee global deduplication across formats
                report.replay_hash = r_hash
                beatmap = parse_osu_7k(str(bp))

                # Resolve the Failed verdict once, so this pre-filter and the persistence
                # layer agree on it. The verdict is inferred from the life bar and the
                # cascade precursor rather than assumed. Assuming False here would discard
                # the short, low-completion aborted runs that ADR-0012 requires us to keep
                # for their pre-fatal peak strains.
                is_failed = infer_is_failed(report)
                if is_noise_match(report.play_duration_s, report.completion_rate, is_failed):
                    stats["noise_filtered"] += 1
                    continue

                saved = storage.save_report_with_filter(report, is_failed=is_failed, beatmap=beatmap)

                if saved is not None:
                    stats["saved"] += 1
                    if saved.is_failed:
                        stats["failed_preserved"] += 1
                else:
                    stats["already_exists"] += 1
            except Exception:
                stats["errors"] += 1

    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.profiler",
        description="proj7k - Causal Replay Diagnostic Ingestion & Macro Skill Profiler",
    )
    # Single ingestion flags
    parser.add_argument(
        "--replay",
        "-r",
        type=str,
        help="Path to osu!mania .osr replay file.",
    )
    parser.add_argument(
        "--beatmap",
        "-b",
        type=str,
        help="Path to matching osu!mania .osu beatmap file.",
    )
    # Batch ingestion flags
    parser.add_argument(
        "--batch-dir",
        type=str,
        help="Directory containing osu!mania .osr replay files for batch ingestion.",
    )
    parser.add_argument(
        "--beatmap-dir",
        type=str,
        help="Directory containing matching .osu beatmap files for batch ingestion.",
    )
    # Macro profile query flags
    parser.add_argument(
        "--player",
        "-p",
        type=str,
        help="Player username to query macro skill profile for.",
    )
    parser.add_argument(
        "--horizon-days",
        type=float,
        default=30.0,
        help="Rolling time window size in days (default: 30).",
    )
    parser.add_argument(
        "--all-time",
        action="store_true",
        help="Query all-time peak profile instead of rolling window.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Custom path to SQLite profiler database (default: ~/.proj7k/profiler.db).",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save single replay ingestion to SQLite database.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON instead of human-readable text.",
    )
    # Coaching recommendation flags (SPEC-P4.1-05)
    parser.add_argument(
        "--recommend",
        nargs="?",
        const="both",
        default=None,
        choices=["both", "bottleneck", "specialty"],
        help="Generate adaptive coaching recommendations (bottleneck breaker, specialty push, or both).",
    )
    parser.add_argument(
        "--realm",
        type=str,
        default=None,
        help="Custom path to osu!lazer client.realm database for beatmap recall.",
    )
    # Practice bundle flags (SPEC-P4.1-05)
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="Generate Three-Tier Targeted Practice Bundle (.osz) from high-strain section slice around fatal failure point.",
    )
    parser.add_argument(
        "--bundle-dir",
        type=str,
        default=None,
        help="Output directory for generated practice bundle .osz and .osu files (default: ./practice_bundles).",
    )
    parser.add_argument(
        "--fatal-time",
        type=float,
        default=None,
        help="Explicit fatal failure timestamp in ms to override automatic detection for practice slice extraction.",
    )
    # Lazer replay import flags (inbound: client.realm -> SQLite).
    # Deliberately not "--sync-lazer": the downscaler already owns that flag for the
    # opposite direction (ADR-0011, injecting practice beatmaps INTO the realm).
    parser.add_argument(
        "--import-replays",
        action="store_true",
        help="Import 7K mania replays from local osu!lazer client.realm into the profiler SQLite database.",
    )
    parser.add_argument(
        "--import-limit",
        type=int,
        default=None,
        help="Optional maximum number of replays to process during --import-replays.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 1. Mode: Lazer Replay Import
    if args.import_replays:
        if not args.player:
            print("Error: --player <name> is required when running --import-replays.", file=sys.stderr)
            return 1
        try:
            stats = run_replay_import(
                player_name=args.player,
                realm_path=args.realm,
                db_path=args.db,
                limit=args.import_limit,
            )
        except Exception as e:
            print(f"Lazer replay import error: {e}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print("============================================================")
            print("      proj7k osu!lazer 7K Replay Import Completed           ")
            print("============================================================")
            print(f"Player Target:        {args.player}")
            print(f"Replays Discovered:   {stats['replays_discovered']}")
            print(f"Newly Ingested:       {stats['saved']}")
            print(f"Already In DB:        {stats['already_exists']}")
            print(f"Noise Filtered:       {stats['noise_filtered']} (<30s or <50% completion)")
            print(f"Failed Preserved:     {stats['failed_preserved']}")
            if stats["missing_files"] > 0:
                print(f"Missing Files:        {stats['missing_files']}")
            if stats["errors"] > 0:
                print(f"Ingestion Errors:     {stats['errors']}")
            print("============================================================")
        return 0

    # 2. Mode: Batch Replay Ingestion
    if args.batch_dir:
        if not args.beatmap_dir:
            print("Error: --beatmap-dir is required when using --batch-dir.", file=sys.stderr)
            return 1
        try:
            stats = run_batch_ingestion(
                args.batch_dir,
                args.beatmap_dir,
                db_path=args.db,
                player_name=args.player,
            )
        except Exception as e:
            print(f"Batch ingestion error: {e}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print("============================================================")
            print("         proj7k Batch Replay Ingestion Completed            ")
            print("============================================================")
            print(f"Replays Processed:    {stats['replays_processed']}")
            print(f"Saved:                {stats['saved']}")
            print(f"Noise Filtered:       {stats['noise_filtered']} (<30s or <50% completion)")
            print(f"Failed Preserved:     {stats['failed_preserved']}")
            if stats.get("skipped_other_player", 0) > 0:
                print(f"Other Player Skipped: {stats['skipped_other_player']}")
            if stats["errors"] > 0:
                print(f"Errors/Unmatched:     {stats['errors']}")
            print("============================================================")
        return 0

    # 3. Mode: Player Macro Profile Query
    if args.player:
        horizon = None if args.all_time else args.horizon_days
        storage = ProfilerStorage(db_path=args.db)
        try:
            profile = aggregate_macro_profile(
                storage=storage,
                player_name=args.player,
                horizon_days=horizon,
                compare_against_all_time=not args.all_time,
            )
        finally:
            storage.close()

        coaching_recs: Optional[List[CoachingRecommendation]] = None
        if args.recommend:
            realm_p = Path(args.realm) if args.realm else None
            coaching_recs = generate_coaching_recommendations(
                profile,
                strategy=args.recommend,
                realm_path=realm_p,
            )

        if args.json:
            data = profile.to_dict()
            if coaching_recs is not None:
                data["coaching_recommendations"] = [r.to_dict() for r in coaching_recs]
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(format_macro_profile(profile))
            if coaching_recs:
                print(format_coaching_report(coaching_recs))
        return 0

    # 3. Mode: Single Replay Ingestion
    if args.replay and args.beatmap:
        try:
            report = run_ingestion(args.replay, args.beatmap)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        if not args.no_save:
            try:
                storage = ProfilerStorage(db_path=args.db)
                beatmap = parse_osu_7k(str(args.beatmap))
                storage.save_report_with_filter(report, beatmap=beatmap)
                storage.close()
            except Exception:
                pass

        coaching_recs = None
        if args.recommend:
            realm_p = Path(args.realm) if args.realm else None
            coaching_recs = generate_coaching_recommendations(
                report,
                strategy=args.recommend,
                realm_path=realm_p,
            )

        bundle_result: Optional[PracticeBundleResult] = None
        if args.bundle:
            fatal_t = args.fatal_time
            if fatal_t is None and report.pathology and report.pathology.cascade_precursor:
                fatal_t = report.pathology.cascade_precursor.fatal_time_ms
            if fatal_t is None and report.miss_count > 0:
                for h in report.alignment_result.aligned_hits:
                    if h.judgment == HitJudgment.MISS:
                        fatal_t = h.hit_object_time_ms
                        break

            if fatal_t is not None:
                beatmap_obj = parse_osu_7k(str(args.beatmap))
                bm_dir = Path(args.beatmap).parent

                cand_audio = None
                expected_audio = beatmap_obj.audio_filename or "audio.mp3"
                if (bm_dir / expected_audio).is_file():
                    cand_audio = bm_dir / expected_audio
                else:
                    for f in bm_dir.iterdir():
                        if f.suffix.lower() in [".mp3", ".ogg", ".wav"]:
                            cand_audio = f
                            break

                cand_bg = None
                for ev in beatmap_obj.raw_events:
                    ev_str = ev.strip()
                    if (ev_str.startswith("0,0,") or ev_str.startswith("Video,")) and '"' in ev_str:
                        toks = ev_str.split('"')
                        if len(toks) >= 2 and (bm_dir / toks[1].strip()).is_file():
                            cand_bg = bm_dir / toks[1].strip()
                            break

                bundle_out = Path(args.bundle_dir or "./practice_bundles")

                player_cap = None
                dom_tech = None
                if report.pathology and report.pathology.cascade_precursor:
                    dom_tech = report.pathology.cascade_precursor.dominant_technique
                if report.skill_radar:
                    if dom_tech and dom_tech in report.skill_radar.dimensions:
                        player_cap = report.skill_radar.dimensions[dom_tech].effective_capacity
                    elif report.skill_radar.dominant_technique in report.skill_radar.dimensions:
                        player_cap = report.skill_radar.dimensions[report.skill_radar.dominant_technique].effective_capacity

                bundle_result = generate_targeted_practice_bundle(
                    beatmap=beatmap_obj,
                    fatal_time_ms=fatal_t,
                    player_capacity=player_cap,
                    dominant_technique=dom_tech,
                    output_dir=bundle_out,
                    audio_path=cand_audio,
                    bg_path=cand_bg,
                )
            elif not args.json:
                print("Notice: No fatal failure point detected in replay. Use --fatal-time <ms> to generate a practice bundle for an explicit section.", file=sys.stderr)

        if args.json:
            data = report.to_dict()
            if coaching_recs is not None:
                data["coaching_recommendations"] = [r.to_dict() for r in coaching_recs]
            if bundle_result is not None:
                data["practice_bundle"] = bundle_result.to_dict()
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(format_ingestion_report(report))
            if coaching_recs:
                print(format_coaching_report(coaching_recs))
            if bundle_result:
                print(format_bundle_report(bundle_result))
        return 0

    # If neither query nor ingestion args were provided
    parser.print_help(file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
