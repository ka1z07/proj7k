"""proj7k - osu!mania 7K evaluation, slicing and analysis toolchain."""

from proj7k.parser import (
    Beatmap7K,
    HitObject,
    NoteType,
    TimingPoint,
    parse_osu_7k,
    dump_osu_7k,
)
from proj7k.features import BeatmapFeatures, FeatureOptions, extract_beatmap_features
from proj7k.parser import dominant_bpm, dominant_timing_point
from proj7k.calibration import (
    DEFAULT_CALIBRATION,
    StrainStarCalibration,
    compute_methodology_fingerprint,
)
from proj7k.physics import (
    ANTIPHASE_ONSET_WINDOW_S,
    BRACKET_PHASE_INVERSION_WINDOW_MS,
    CHORDJACK_STEP_INTERVAL_MS,
    JACK_INTERVAL_PENALTY_MS,
    SPEED_BURST_INTERVAL_MS,
)

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
    read_ladder_metric,
    MonotonicityStep,
    MonotonicityViolation,
    DiscontinuityWarning,
    TierMonotonicityReport,
)
from proj7k.scaling import (
    compute_action_window,
    compute_inverse_scaling_factor,
    apply_inverse_bpm_scaling,
    compute_inverse_score,
    ClockWindowRecord,
    ScalingRegime,
    ScalingConfig,
    LOW_SPEED_BPM_THRESHOLD,
    HIGH_SPEED_BPM_THRESHOLD,
    LOW_SPEED_CAP,
)
from proj7k.distillation import (
    DISTILLATION_FEATURE_KEYS,
    extract_feature_vector,
    normalize_feature_vectors,
    compute_technique_centroids,
    compute_feature_importance,
    compute_separability_matrix,
    distill_benchmark_features,
    SeparabilityMatrix,
    DistillationResult,
)
from proj7k.assets import (
    AssetLibraryIndex,
    scan_local_asset_library,
    bind_manifest_to_corpus,
    bind_manifest_to_library,
    load_corpus_fixture,
)
from proj7k.cache import (
    TwoLayerCache,
    ALGORITHM_VERSION,
    DEFAULT_CACHE_DIR,
)
from proj7k.checksum import compute_feature_checksum, compute_star_rating_checksum
from proj7k.guard import (
    MetricGate,
    MonotonicityGuardError,
    MonotonicityGuardConfig,
    MonotonicityGuardResult,
    evaluate_monotonicity_guard,
    run_monotonicity_guard,
)
from proj7k.strain import (
    StrainTimeseriesProfile,
    StrainOptions,
    compute_dual_hand_strain,
    compute_judgment_overlap_buffer,
    compute_high_speed_scaling_factor,
    compute_micro_speed_burst,
)
from proj7k.radar import (
    TechniqueRadar,
    RawTechniqueDrivers,
    RadarOptions,
    compute_raw_technique_drivers,
    compute_technique_radar,
)
from proj7k.rating import (
    RatingOptions,
    StarRatingSynthesis,
    aggregate_p_norm,
    apply_tanh_soft_cap,
    compute_raw_strain_star_rating,
    synthesize_star_rating,
)

__version__ = "0.1.0"


def __getattr__(name: str):
    if name in (
        "DifficultyOptions",
        "IntrinsicDifficultyResult",
        "evaluate_intrinsic_difficulty",
        "current_engine_version",
    ):
        import proj7k.difficulty as _diff
        return getattr(_diff, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Beatmap7K",
    "HitObject",
    "NoteType",
    "TimingPoint",
    "parse_osu_7k",
    "dump_osu_7k",
    "BeatmapFeatures",
    "FeatureOptions",
    "extract_beatmap_features",
    "dominant_bpm",
    "dominant_timing_point",
    "DEFAULT_CALIBRATION",
    "StrainStarCalibration",
    "compute_methodology_fingerprint",
    "ANTIPHASE_ONSET_WINDOW_S",
    "BRACKET_PHASE_INVERSION_WINDOW_MS",
    "CHORDJACK_STEP_INTERVAL_MS",
    "JACK_INTERVAL_PENALTY_MS",
    "SPEED_BURST_INTERVAL_MS",

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
    "read_ladder_metric",
    "MonotonicityStep",
    "MonotonicityViolation",
    "DiscontinuityWarning",
    "TierMonotonicityReport",
    "compute_action_window",
    "compute_inverse_scaling_factor",
    "apply_inverse_bpm_scaling",
    "compute_inverse_score",
    "ClockWindowRecord",
    "ScalingRegime",
    "ScalingConfig",
    "LOW_SPEED_BPM_THRESHOLD",
    "HIGH_SPEED_BPM_THRESHOLD",
    "LOW_SPEED_CAP",
    "DISTILLATION_FEATURE_KEYS",
    "extract_feature_vector",
    "normalize_feature_vectors",
    "compute_technique_centroids",
    "compute_feature_importance",
    "compute_separability_matrix",
    "distill_benchmark_features",
    "SeparabilityMatrix",
    "DistillationResult",
    "AssetLibraryIndex",
    "scan_local_asset_library",
    "bind_manifest_to_corpus",
    "bind_manifest_to_library",
    "load_corpus_fixture",
    "TwoLayerCache",
    "ALGORITHM_VERSION",
    "DEFAULT_CACHE_DIR",
    "compute_feature_checksum",
    "compute_star_rating_checksum",
    "MetricGate",
    "MonotonicityGuardError",
    "MonotonicityGuardConfig",
    "MonotonicityGuardResult",
    "evaluate_monotonicity_guard",
    "run_monotonicity_guard",
    "StrainTimeseriesProfile",
    "StrainOptions",
    "compute_dual_hand_strain",
    "compute_judgment_overlap_buffer",
    "compute_high_speed_scaling_factor",
    "compute_micro_speed_burst",
    "TechniqueRadar",
    "RawTechniqueDrivers",
    "RadarOptions",
    "compute_raw_technique_drivers",
    "compute_technique_radar",
    "RatingOptions",
    "StarRatingSynthesis",
    "aggregate_p_norm",
    "apply_tanh_soft_cap",
    "compute_raw_strain_star_rating",
    "current_engine_version",
    "synthesize_star_rating",
    "DifficultyOptions",
    "IntrinsicDifficultyResult",
    "evaluate_intrinsic_difficulty",
]

