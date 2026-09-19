"""
CLI entrypoint and ingestion runner for 7K Player Replay Profiler.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Sequence

from proj7k.parser import parse_osu_7k
from proj7k.profiler.matcher import (
    HitAlignmentResult,
    HitJudgment,
    align_replay_hits,
    column_to_canonical_lane,
)
from proj7k.profiler.osr import OSRReplay, parse_osr
from proj7k.profiler.pathology import PathologyReport, analyze_pathology
from proj7k.profiler.response import SkillRadarReport, analyze_strain_response


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
    )


run_profiler = run_ingestion



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
            dim_name = dim.replace("_", " ").title()
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



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.profiler",
        description="proj7k - Causal Replay Ingestion & Hit Alignment CLI",
    )
    parser.add_argument(
        "--replay",
        "-r",
        required=True,
        type=str,
        help="Path to osu!mania .osr replay file.",
    )
    parser.add_argument(
        "--beatmap",
        "-b",
        required=True,
        type=str,
        help="Path to matching osu!mania .osu beatmap file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON instead of human-readable text.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        report = run_ingestion(args.replay, args.beatmap)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_ingestion_report(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
