"""
Star rating synthesis and soft-cap compression for 7K intrinsic difficulty.

Implements:
1. Raw physical strain star rating fit: SR_raw = a * S_base^0.65 + b
2. Extremum-dominant p-Norm aggregation (p = 4.0) over technique radar vector
3. C^1 smooth Hyperbolic Tangent (tanh) soft-cap compression above 9.5★

There is no separate hard ceiling on the output, and deliberately so: the soft cap asymptotes to
`soft_cap_threshold + soft_cap_scale` (12.5★ at the canonical calibration) without ever reaching
it, so any clamp at or above that value could never bind while any clamp below it would put a
non-smooth corner into a curve that exists to be smooth. The field that used to carry one
(`RatingOptions.max_star_rating`) was read by nothing and is gone (issue #48).
"""

from dataclasses import dataclass
import math
from typing import Dict, List, Optional, Sequence, Union

from proj7k.calibration import (
    DEFAULT_CALIBRATION,
    StrainStarCalibration,
    compute_methodology_fingerprint,
)
from proj7k.dan import CANONICAL_DAN_SR, CANONICAL_DAN_TIERS
from proj7k.radar import TechniqueRadar
from proj7k.strain import compute_raw_strain_star_rating


@dataclass(frozen=True)
class RatingOptions:
    """
    Single options object carrying the star-rating calibration used by the whole engine.

    Defaults are read off `calibration.DEFAULT_CALIBRATION` rather than re-typed here, so the
    anchor law and the driver back-pressure exponent have exactly one definition. The
    `calibration` property exposes them as one value object for the radar to consume.
    """
    p_norm: float = 4.0
    damping_coeff: float = 0.08
    soft_cap_threshold: float = 9.5
    soft_cap_scale: float = 3.0
    strain_exp: float = DEFAULT_CALIBRATION.strain_exp
    strain_a: float = DEFAULT_CALIBRATION.strain_a
    strain_b: float = DEFAULT_CALIBRATION.strain_b
    driver_backpressure_exp: float = DEFAULT_CALIBRATION.driver_backpressure_exp

    @property
    def calibration(self) -> StrainStarCalibration:
        """Anchor-law and back-pressure constants as a single injectable value object."""
        return StrainStarCalibration(
            strain_a=self.strain_a,
            strain_b=self.strain_b,
            strain_exp=self.strain_exp,
            driver_backpressure_exp=self.driver_backpressure_exp,
        )

    @property
    def methodology_fingerprint(self) -> str:
        """
        Algorithm version of the star-rating stage: the strain anchor law, the driver
        back-pressure exponent, the p-norm aggregation, the soft-cap compression, and the
        canonical Dan anchors behind the injected tier label.

        `difficulty.DifficultyOptions.engine_fingerprint` folds this together with the radar,
        strain, feature, and physical constants into the version stamped into injected metadata.
        """
        return compute_methodology_fingerprint(
            strain_a=self.strain_a,
            strain_b=self.strain_b,
            strain_exp=self.strain_exp,
            driver_backpressure_exp=self.driver_backpressure_exp,
            p_norm=self.p_norm,
            damping_coeff=self.damping_coeff,
            soft_cap_threshold=self.soft_cap_threshold,
            soft_cap_scale=self.soft_cap_scale,
            dan_tiers=tuple(CANONICAL_DAN_TIERS),
            dan_sr=tuple(CANONICAL_DAN_SR[t] for t in CANONICAL_DAN_TIERS),
        )


#: The canonical field defaults, named once. The standalone operator signatures below read their
#: defaults from here rather than carrying a second copy of 4.0 / 0.08 / 9.5 / 3.0 — the profiler
#: calls them without a RatingOptions, so a duplicated literal there would be a live second
#: definition of the same calibration.
DEFAULT_RATING_OPTIONS = RatingOptions()


@dataclass(frozen=True)
class StarRatingSynthesis:
    star_rating: float           # SR_final: after tanh soft-cap
    uncompressed_rating: float   # SR_norm: before tanh soft-cap
    raw_strain_rating: float     # SR_raw: purely from base strain
    dominant_technique: str
    dominant_score: float
    synergy_bonus: float         # SR_norm - dominant_score

    def to_dict(self) -> Dict[str, Union[float, str]]:
        return {
            "star_rating": self.star_rating,
            "uncompressed_rating": self.uncompressed_rating,
            "raw_strain_rating": self.raw_strain_rating,
            "dominant_technique": self.dominant_technique,
            "dominant_score": self.dominant_score,
            "synergy_bonus": self.synergy_bonus,
        }




def aggregate_p_norm(
    scores: Union[Sequence[float], TechniqueRadar, Dict[str, float]],
    p: float = DEFAULT_RATING_OPTIONS.p_norm,
    damping_coeff: float = DEFAULT_RATING_OPTIONS.damping_coeff,
) -> float:
    """
    Aggregates multi-dimensional technique scores via extremum-dominant p-norm:
    SR_norm = max(R) * (sum((r_k / max(R))^p))^(1/p) * (1.0 + damping_coeff * sum(...))^(-0.5)
    """
    if isinstance(scores, TechniqueRadar):
        vals = [
            scores.jack,
            scores.tech,
            scores.speed,
            scores.stream,
            scores.ln_general,
            scores.ln_tech,
            scores.ln_inverse,
            scores.ln_release,
        ]
    elif isinstance(scores, dict):
        vals = list(scores.values())
    else:
        vals = list(scores)

    if not vals:
        return 0.0

    max_v = max(vals)
    if max_v <= 1e-9:
        return 0.0

    # Normalized relative powers
    power_sum = sum(math.pow(max(0.0, v) / max_v, p) for v in vals)
    norm_factor = math.pow(power_sum, 1.0 / p)
    damping = math.pow(1.0 + damping_coeff * power_sum, -0.5)

    return max_v * norm_factor * damping


def apply_tanh_soft_cap(
    sr: float,
    threshold: float = DEFAULT_RATING_OPTIONS.soft_cap_threshold,
    scale: float = DEFAULT_RATING_OPTIONS.soft_cap_scale,
) -> float:
    """
    Applies C^1 smooth hyperbolic tangent soft-cap compression:
    SR_final = sr, if sr <= threshold
    SR_final = threshold + scale * tanh((sr - threshold) / scale), if sr > threshold
    """
    if sr <= threshold:
        return max(0.0, sr)
    return threshold + scale * math.tanh((sr - threshold) / scale)


def synthesize_star_rating(
    radar: TechniqueRadar,
    p90_strain: float = 0.0,
    options: Optional[RatingOptions] = None,
) -> StarRatingSynthesis:
    """
    Synthesizes final Star Rating from technique radar and p90 strain profile.
    """
    if options is None:
        options = RatingOptions()

    sr_raw = compute_raw_strain_star_rating(
        p90_strain,
        a=options.strain_a,
        b=options.strain_b,
        exp=options.strain_exp,
    )

    sr_norm = aggregate_p_norm(
        radar,
        p=options.p_norm,
        damping_coeff=options.damping_coeff,
    )

    # Fallback to sr_raw if radar scores are all zero but physical strain exists
    if sr_norm <= 1e-9 and sr_raw > 0.0:
        sr_norm = sr_raw

    # Compute synergy bonus against single highest technique
    dom_score = radar.dominant_score
    synergy = max(0.0, sr_norm - dom_score) if dom_score > 0 else 0.0

    # Soft-cap compression
    sr_final = apply_tanh_soft_cap(
        sr_norm,
        threshold=options.soft_cap_threshold,
        scale=options.soft_cap_scale,
    )

    return StarRatingSynthesis(
        star_rating=round(sr_final, 4),
        uncompressed_rating=round(sr_norm, 4),
        raw_strain_rating=round(sr_raw, 4),
        dominant_technique=radar.dominant_technique,
        dominant_score=round(dom_score, 4),
        synergy_bonus=round(synergy, 4),
    )
