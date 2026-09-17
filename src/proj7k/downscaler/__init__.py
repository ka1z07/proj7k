"""proj7k.downscaler - Closed-loop strain downscaler and technique preservation engine."""

from proj7k.downscaler.mutation import (
    apply_pure_deletion,
    update_practice_metadata,
    create_practice_beatmap,
    export_practice_beatmap,
)
from proj7k.downscaler.skeleton import MetricSkeletonDetector
from proj7k.downscaler.balancer import BimanualFluxBalancer
from proj7k.downscaler.mapper import (
    TwoTierDanMapper,
    DanTarget,
    CANONICAL_DAN_SR,
    CANONICAL_DAN_TIERS,
    parse_dan_tier,
    star_rating_to_strain,
)
from proj7k.downscaler.validator import (
    DualGateValidator,
    ValidationResult,
    compute_radar_cosine_similarity,
)
from proj7k.downscaler.pruner import (
    WindowedPeakBatchPruner,
    PruningResult,
    PruneIterationRecord,
)
from proj7k.downscaler.pipeline import (
    downscale_beatmap,
    DownscaleOptions,
    DownscaleResult,
)

__all__ = [
    "apply_pure_deletion",
    "update_practice_metadata",
    "create_practice_beatmap",
    "export_practice_beatmap",
    "MetricSkeletonDetector",
    "BimanualFluxBalancer",
    "TwoTierDanMapper",
    "DanTarget",
    "CANONICAL_DAN_SR",
    "CANONICAL_DAN_TIERS",
    "parse_dan_tier",
    "star_rating_to_strain",
    "DualGateValidator",
    "ValidationResult",
    "compute_radar_cosine_similarity",
    "WindowedPeakBatchPruner",
    "PruningResult",
    "PruneIterationRecord",
    "downscale_beatmap",
    "DownscaleOptions",
    "DownscaleResult",
]
