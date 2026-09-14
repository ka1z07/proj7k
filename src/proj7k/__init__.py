"""proj7k - osu!mania 7K evaluation, slicing and analysis toolchain."""

from proj7k.features import BeatmapFeatures, extract_beatmap_features
from proj7k.batch import (
    BenchmarkItem,
    BatchSummary,
    BenchmarkItemResult,
    BenchmarkBatchReport,
    run_benchmark_pipeline,
    load_manifest,
)
from proj7k.monotonicity import (
    TIER_ORDER,
    compute_kendall_tau,
    compute_spearman_rho,
    evaluate_tier_sequence,
    evaluate_batch_monotonicity,
    MonotonicityStep,
    MonotonicityViolation,
    DiscontinuityWarning,
    TierMonotonicityReport,
)

__version__ = "0.1.0"

__all__ = [
    "BeatmapFeatures",
    "extract_beatmap_features",
    "BenchmarkItem",
    "BatchSummary",
    "BenchmarkItemResult",
    "BenchmarkBatchReport",
    "run_benchmark_pipeline",
    "load_manifest",
    "TIER_ORDER",
    "compute_kendall_tau",
    "compute_spearman_rho",
    "evaluate_tier_sequence",
    "evaluate_batch_monotonicity",
    "MonotonicityStep",
    "MonotonicityViolation",
    "DiscontinuityWarning",
    "TierMonotonicityReport",
]

