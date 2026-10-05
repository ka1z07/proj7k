"""
proj7k.downscaler.pipeline - High-level Beatmap Downscaling Pipeline Seam (SPEC-P5.1-03, ADR-0021).

Provides the core Python interface:
downscale_beatmap(beatmap: Beatmap7K, options: Optional[DownscaleOptions] = None) -> DownscaleResult

The loop steers by the spec-v0.2 engine's own star rating (ADR-0021); there is no second scale.
"""

from dataclasses import dataclass, field
import hashlib
from typing import Any, Dict, List, Optional, Tuple

from proj7k.engine import DifficultyProfile
from proj7k.engine.skills import SKILL_TECH_KEY
from proj7k.features import BeatmapFeatures, extract_beatmap_features
from proj7k.parser import Beatmap7K, dump_osu_7k
from proj7k.downscaler.balancer import BimanualFluxBalancer
from proj7k.downscaler.mapper import DanTarget, TwoTierDanMapper
from proj7k.downscaler.mutation import update_practice_metadata
from proj7k.downscaler.pruner import ExcessLossPruner, PruningResult
from proj7k.downscaler.validator import DualGateValidator, ValidationResult


@dataclass(frozen=True)
class DownscaleOptions:
    """Configuration options for beatmap downscaling. A target is a Dan tier, a star rating, or an engine level."""
    target_dan: Optional[str] = "7th"
    target_sr: Optional[float] = None
    target_D: Optional[float] = None
    dominant_skill: Optional[str] = None
    prune_ratio: float = 0.20
    max_iterations: int = 25
    tolerance: float = 0.05
    window_s: float = 1.0
    min_cosine_similarity: float = 0.80


@dataclass(frozen=True)
class DownscaleResult:
    """Complete diagnostic and analytical report of a downscaled practice beatmap."""
    original_beatmap: Beatmap7K
    downscaled_beatmap: Beatmap7K
    target: DanTarget
    original_features: BeatmapFeatures
    downscaled_features: BeatmapFeatures
    #: The difficulty engine's profiles: stars, the eight skills' stars, dominance.
    original_profile: DifficultyProfile
    downscaled_profile: DifficultyProfile
    validation: ValidationResult
    pruning_result: PruningResult
    notes_removed: int
    removal_ratio: float
    bimanual_flux_ratio: Tuple[float, float]
    warnings: List[str] = field(default_factory=list)

    @property
    def original_stars(self) -> float:
        return self.original_profile.total_stars

    @property
    def downscaled_stars(self) -> float:
        return self.downscaled_profile.total_stars

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target.to_dict(),
            "notes_removed": self.notes_removed,
            "removal_ratio": round(self.removal_ratio, 4),
            "bimanual_flux_ratio": (
                round(self.bimanual_flux_ratio[0], 3),
                round(self.bimanual_flux_ratio[1], 3),
            ),
            "original_star_rating": round(self.original_stars, 4),
            "downscaled_star_rating": round(self.downscaled_stars, 4),
            "original_D": round(self.original_profile.total_D, 4),
            "downscaled_D": round(self.downscaled_profile.total_D, 4),
            "validation": self.validation.to_dict(),
            "pruning": self.pruning_result.to_dict(),
            "warnings": self.warnings,
        }


def downscale_beatmap(
    beatmap: Beatmap7K,
    options: Optional[DownscaleOptions] = None,
) -> DownscaleResult:
    """
    Downscales an osu!mania 7K beatmap towards target Dan, Star Rating or engine level (ADR-0011, ADR-0021).

    Enforces:
    - Pure deletion mutation (no deformed hit objects)
    - Metric skeleton protection (1/1 downbeats and chord bases)
    - Bimanual striking flux balance guidance towards [45%, 55%]
    - Closed-loop excess-loss pruning on the engine's own star
    - Dual-gate technique preservation (skill-stars cosine similarity >= 0.80 & dominant skill preserved)
    """
    if options is None:
        options = DownscaleOptions()

    validator = DualGateValidator(min_cosine_similarity=options.min_cosine_similarity)
    orig_feat = extract_beatmap_features(beatmap)
    orig_profile = validator.profile_of(beatmap)

    dom_skill = options.dominant_skill or SKILL_TECH_KEY[orig_profile.dominant_skill]
    target = TwoTierDanMapper().resolve(
        target_dan=options.target_dan,
        target_sr=options.target_sr,
        target_D=options.target_D,
        dominant_skill=dom_skill,
    )

    balancer = BimanualFluxBalancer()
    pruner = ExcessLossPruner(
        prune_ratio=options.prune_ratio,
        max_iterations=options.max_iterations,
        tolerance=options.tolerance,
        window_s=options.window_s,
        validator=validator,
        balancer=balancer,
    )
    pruning_res = pruner.prune(beatmap=beatmap, target_sr=target.target_sr, dominant_skill=dom_skill)
    warnings: List[str] = list(pruning_res.warnings)

    # Derivative practice metadata: the original's identity, the target tier and the dominant skill's tag
    orig_md5 = beatmap.md5 or hashlib.md5(dump_osu_7k(beatmap).encode("utf-8")).hexdigest()
    practice_bm = update_practice_metadata(
        pruning_res.downscaled_beatmap,
        target_dan=target.target_dan,
        original_md5=orig_md5,
        dominant_skill=SKILL_TECH_KEY[orig_profile.dominant_skill],
    )

    validation_res = validator.validate(beatmap, practice_bm, target=target)
    if not validation_res.passed:
        warnings.append("Technique preservation validation reported non-conforming metrics.")

    notes_removed = len(beatmap.hit_objects) - len(practice_bm.hit_objects)
    return DownscaleResult(
        original_beatmap=beatmap,
        downscaled_beatmap=practice_bm,
        target=target,
        original_features=orig_feat,
        downscaled_features=extract_beatmap_features(practice_bm),
        original_profile=orig_profile,
        downscaled_profile=validator.profile_of(practice_bm),
        validation=validation_res,
        pruning_result=pruning_res,
        notes_removed=notes_removed,
        removal_ratio=notes_removed / max(1, len(beatmap.hit_objects)),
        bimanual_flux_ratio=balancer.compute_flux_ratio(practice_bm.hit_objects),
        warnings=warnings,
    )
