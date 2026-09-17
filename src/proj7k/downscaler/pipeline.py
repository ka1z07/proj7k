"""
proj7k.downscaler.pipeline - High-level Beatmap Downscaling Pipeline Seam (SPEC-P5.1-03).

Provides the core Python interface:
downscale_beatmap(beatmap: Beatmap7K, options: Optional[DownscaleOptions] = None) -> DownscaleResult
"""

from dataclasses import dataclass, field
import hashlib
from typing import Any, Dict, List, Optional, Tuple, Union

from proj7k.features import BeatmapFeatures, extract_beatmap_features
from proj7k.parser import Beatmap7K, dump_osu_7k
from proj7k.radar import RadarOptions, TechniqueRadar, compute_technique_radar
from proj7k.rating import RatingOptions, StarRatingSynthesis, synthesize_star_rating
from proj7k.strain import StrainOptions, StrainTimeseriesProfile, compute_dual_hand_strain
from proj7k.downscaler.mapper import DanTarget, TwoTierDanMapper
from proj7k.downscaler.validator import DualGateValidator, ValidationResult
from proj7k.downscaler.pruner import WindowedPeakBatchPruner, PruningResult
from proj7k.downscaler.balancer import BimanualFluxBalancer
from proj7k.downscaler.mutation import update_practice_metadata


@dataclass(frozen=True)
class DownscaleOptions:
    """Configuration options for beatmap downscaling."""
    target_dan: Optional[str] = "7th"
    target_sr: Optional[float] = None
    target_strain: Optional[float] = None
    dominant_skill: Optional[str] = None
    prune_ratio: float = 0.20
    max_iterations: int = 25
    tolerance: float = 0.05
    min_cosine_similarity: float = 0.80
    strain_options: Optional[StrainOptions] = None
    radar_options: Optional[RadarOptions] = None
    rating_options: Optional[RatingOptions] = None


@dataclass(frozen=True)
class DownscaleResult:
    """Complete diagnostic and analytical report of a downscaled practice beatmap."""
    original_beatmap: Beatmap7K
    downscaled_beatmap: Beatmap7K
    target: DanTarget
    original_features: BeatmapFeatures
    downscaled_features: BeatmapFeatures
    original_radar: TechniqueRadar
    downscaled_radar: TechniqueRadar
    original_strain: StrainTimeseriesProfile
    downscaled_strain: StrainTimeseriesProfile
    original_rating: StarRatingSynthesis
    downscaled_rating: StarRatingSynthesis
    validation: ValidationResult
    pruning_result: PruningResult
    notes_removed: int
    removal_ratio: float
    bimanual_flux_ratio: Tuple[float, float]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target.to_dict(),
            "notes_removed": self.notes_removed,
            "removal_ratio": round(self.removal_ratio, 4),
            "bimanual_flux_ratio": (
                round(self.bimanual_flux_ratio[0], 3),
                round(self.bimanual_flux_ratio[1], 3),
            ),
            "original_star_rating": self.original_rating.star_rating,
            "downscaled_star_rating": self.downscaled_rating.star_rating,
            "original_p90_strain": round(self.original_strain.p90_strain, 3),
            "downscaled_p90_strain": round(self.downscaled_strain.p90_strain, 3),
            "validation": self.validation.to_dict(),
            "pruning": self.pruning_result.to_dict(),
            "warnings": self.warnings,
        }


def downscale_beatmap(
    beatmap: Beatmap7K,
    options: Optional[DownscaleOptions] = None,
) -> DownscaleResult:
    """
    Downscales an osu!mania 7K beatmap towards target Dan or Star Rating (ADR-0011, SPEC-P5.1-03).

    Enforces:
    - Pure deletion mutation (no deformed hit objects)
    - Metric skeleton protection (1/1 downbeats and chord bases)
    - Bimanual striking flux balance guidance towards [45%, 55%]
    - Closed-loop peak strain damping
    - Dual-gate technique preservation (radar cosine similarity >= 0.80 & dominant preserved)
    """
    if options is None:
        options = DownscaleOptions()

    strain_opts = options.strain_options or StrainOptions()
    radar_opts = options.radar_options or RadarOptions()
    rating_opts = options.rating_options or RatingOptions()

    # 1. Evaluate baseline characteristics of original beatmap
    orig_feat = extract_beatmap_features(beatmap)
    orig_radar = compute_technique_radar(beatmap, options=radar_opts)
    orig_strain = compute_dual_hand_strain(beatmap, options=strain_opts)
    orig_rating = synthesize_star_rating(orig_radar, p90_strain=orig_strain.p90_strain, options=rating_opts)

    # 2. Resolve target Dan and target strain
    mapper = TwoTierDanMapper()
    dom_skill = options.dominant_skill or orig_radar.dominant_technique
    target = mapper.resolve(
        target_dan=options.target_dan,
        target_sr=options.target_sr,
        dominant_skill=dom_skill,
    )

    target_strain = options.target_strain or target.target_strain
    warnings: List[str] = []

    # 3. Setup components
    validator = DualGateValidator(min_cosine_similarity=options.min_cosine_similarity)
    balancer = BimanualFluxBalancer()
    pruner = WindowedPeakBatchPruner(
        prune_ratio=options.prune_ratio,
        max_iterations=options.max_iterations,
        tolerance=options.tolerance,
        strain_options=strain_opts,
        validator=validator,
        balancer=balancer,
    )

    # 4. Execute closed-loop pruning
    pruning_res = pruner.prune(
        beatmap=beatmap,
        target_strain=target_strain,
        dominant_technique=dom_skill,
    )

    pruned_bm = pruning_res.downscaled_beatmap
    warnings.extend(pruning_res.warnings)

    # 5. Compute original MD5 and update derivative practice metadata
    orig_md5 = beatmap.md5 or hashlib.md5(dump_osu_7k(beatmap).encode("utf-8")).hexdigest()
    practice_bm = update_practice_metadata(
        pruned_bm,
        target_dan=target.target_dan,
        original_md5=orig_md5,
        dominant_skill=orig_radar.dominant_technique,
    )

    # 6. Re-evaluate final practice beatmap
    final_feat = extract_beatmap_features(practice_bm)
    final_radar = compute_technique_radar(practice_bm, options=radar_opts)
    final_strain = compute_dual_hand_strain(practice_bm, options=strain_opts)
    final_rating = synthesize_star_rating(final_radar, p90_strain=final_strain.p90_strain, options=rating_opts)

    validation_res = validator.validate(orig_radar, practice_bm, target=target)
    if not validation_res.passed:
        warnings.append("Technique preservation validation reported non-conforming metrics.")

    notes_removed = len(beatmap.hit_objects) - len(practice_bm.hit_objects)
    removal_ratio = notes_removed / max(1, len(beatmap.hit_objects))
    final_flux_ratio = balancer.compute_flux_ratio(practice_bm.hit_objects)

    return DownscaleResult(
        original_beatmap=beatmap,
        downscaled_beatmap=practice_bm,
        target=target,
        original_features=orig_feat,
        downscaled_features=final_feat,
        original_radar=orig_radar,
        downscaled_radar=final_radar,
        original_strain=orig_strain,
        downscaled_strain=final_strain,
        original_rating=orig_rating,
        downscaled_rating=final_rating,
        validation=validation_res,
        pruning_result=pruning_res,
        notes_removed=notes_removed,
        removal_ratio=removal_ratio,
        bimanual_flux_ratio=final_flux_ratio,
        warnings=warnings,
    )
