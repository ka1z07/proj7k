"""
proj7k.profiler: 7K Player Replay Profiler & Closed-Loop Diagnostic System (Phase 4.1).
"""

from proj7k.profiler.aggregate import (
    DimensionMacroMetric,
    MacroProfile,
    aggregate_macro_profile,
)
from proj7k.profiler.cli import (
    ProfilerIngestionReport,
    format_ingestion_report,
    format_macro_profile,
    run_batch_ingestion,
    run_ingestion,
    run_profiler,
)
from proj7k.profiler.matcher import HitAlignmentResult, HitJudgment, align_replay_hits
from proj7k.profiler.osr import OSRReplay, parse_osr
from proj7k.profiler.pathology import (
    BimanualPathology,
    CascadePrecursor,
    JackDriftReport,
    LNReleasePathology,
    PathologyReport,
    TrackPathology,
    analyze_pathology,
)
from proj7k.profiler.response import (
    DimensionCapacityResult,
    SkillRadarReport,
    StrainBin,
    StrainResponseOptions,
    analyze_strain_response,
)
from proj7k.profiler.storage import (
    MatchSnapshot,
    ProfilerStorage,
    build_snapshot_from_report,
    is_noise_match,
)

__all__ = [
    "ProfilerIngestionReport",
    "run_ingestion",
    "run_profiler",
    "run_batch_ingestion",
    "format_ingestion_report",
    "format_macro_profile",
    "HitAlignmentResult",
    "HitJudgment",
    "align_replay_hits",
    "OSRReplay",
    "parse_osr",
    "TrackPathology",
    "BimanualPathology",
    "JackDriftReport",
    "LNReleasePathology",
    "CascadePrecursor",
    "PathologyReport",
    "analyze_pathology",
    "StrainBin",
    "DimensionCapacityResult",
    "SkillRadarReport",
    "StrainResponseOptions",
    "analyze_strain_response",
    "MatchSnapshot",
    "ProfilerStorage",
    "build_snapshot_from_report",
    "is_noise_match",
    "DimensionMacroMetric",
    "MacroProfile",
    "aggregate_macro_profile",
]
