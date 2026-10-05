"""proj7k.downscaler - Closed-loop downscaler on the difficulty engine, and technique preservation (ADR-0021)."""

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
)
from proj7k.downscaler.marginal import removal_benefit
from proj7k.downscaler.validator import (
    DualGateValidator,
    ValidationResult,
    compute_radar_cosine_similarity,
)
from proj7k.downscaler.pruner import (
    ExcessLossPruner,
    PruningResult,
    PruneIterationRecord,
)
from proj7k.downscaler.pipeline import (
    downscale_beatmap,
    DownscaleOptions,
    DownscaleResult,
)
from proj7k.downscaler.cli import (
    main,
    build_parser,
    format_downscale_report,
    sync_practice_beatmaps_to_lazer,
    LazerPracticeSyncResult,
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
    "removal_benefit",
    "DualGateValidator",
    "ValidationResult",
    "compute_radar_cosine_similarity",
    "ExcessLossPruner",
    "PruningResult",
    "PruneIterationRecord",
    "downscale_beatmap",
    "DownscaleOptions",
    "DownscaleResult",
    "main",
    "build_parser",
    "format_downscale_report",
    "sync_practice_beatmaps_to_lazer",
    "LazerPracticeSyncResult",
]
