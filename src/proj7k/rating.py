"""
Star rating synthesis and soft-cap compression for 7K intrinsic difficulty.

Implements:
1. Raw physical strain star rating fit: SR_raw = a * S_base^0.65 + b
2. Extremum-dominant composition over the 8 absolute technique stars: SR_norm = max(R)
3. C^1 smooth Hyperbolic Tangent (tanh) soft-cap compression above 9.5★

There is no separate hard ceiling on the output, and deliberately so: the soft cap asymptotes to
`soft_cap_threshold + soft_cap_scale` (12.5★ at the canonical calibration) without ever reaching
it, so any clamp at or above that value could never bind while any clamp below it would put a
non-smooth corner into a curve that exists to be smooth. The field that used to carry one
(`RatingOptions.max_star_rating`) was read by nothing and is gone (issue #48).
"""

from dataclasses import dataclass
import math
from typing import Dict, List, Optional, Sequence, Tuple, Union

from proj7k.calibration import (
    DEFAULT_CALIBRATION,
    StrainStarCalibration,
    TechniqueStarAnchor,
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
    strain anchor law and the eight technique anchors have exactly one definition. The
    `calibration` property exposes them as one value object for the radar to consume.
    """
    soft_cap_threshold: float = 9.5
    soft_cap_scale: float = 3.0
    strain_exp: float = DEFAULT_CALIBRATION.strain_exp
    strain_a: float = DEFAULT_CALIBRATION.strain_a
    strain_b: float = DEFAULT_CALIBRATION.strain_b
    technique_anchors: Tuple[TechniqueStarAnchor, ...] = DEFAULT_CALIBRATION.technique_anchors

    @property
    def calibration(self) -> StrainStarCalibration:
        """Strain anchor law and technique anchors as a single injectable value object."""
        return StrainStarCalibration(
            strain_a=self.strain_a,
            strain_b=self.strain_b,
            strain_exp=self.strain_exp,
            technique_anchors=self.technique_anchors,
        )

    @property
    def methodology_fingerprint(self) -> str:
        """
        Algorithm version of the star-rating stage: the strain anchor law, the eight absolute
        technique anchors, the soft-cap compression, and the canonical Dan anchors behind the
        injected tier label.

        `difficulty.DifficultyOptions.engine_fingerprint` folds this together with the radar,
        strain, feature, and physical constants into the version stamped into injected metadata.
        """
        return compute_methodology_fingerprint(
            strain_a=self.strain_a,
            strain_b=self.strain_b,
            strain_exp=self.strain_exp,
            technique_anchors=tuple(
                (anchor.technique, anchor.a, anchor.exp) for anchor in self.technique_anchors
            ),
            soft_cap_threshold=self.soft_cap_threshold,
            soft_cap_scale=self.soft_cap_scale,
            dan_tiers=tuple(CANONICAL_DAN_TIERS),
            dan_sr=tuple(CANONICAL_DAN_SR[t] for t in CANONICAL_DAN_TIERS),
        )


#: The canonical field defaults, named once. `apply_tanh_soft_cap`'s standalone signature reads
#: its defaults from here rather than carrying a second copy of 9.5 / 3.0 — the profiler calls it
#: without a RatingOptions, so a duplicated literal there would be a live second definition of
#: the same calibration.
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
    p: float,
    damping_coeff: float,
) -> float:
    """
    Aggregates multi-dimensional scores via extremum-dominant p-norm:
    SR_norm = max(R) * (sum((r_k / max(R))^p))^(1/p) * (1.0 + damping_coeff * sum(...))^(-0.5)

    Not the chart-rating composition any more (issue #50): a chart's star rating is the largest
    of its eight absolute technique stars, exactly (`synthesize_star_rating`). This operator
    survives for the *player-side* aggregate the profiler reports — a player's overall level
    across the dimensions they have been tested in, where the damping term expresses that being
    strong in several dimensions is worth more than being strong in one. Its two constants are
    therefore the profiler's, p and damping are required arguments, and neither is an engine
    option field: they do not move a chart's rating and so have no business in its fingerprint.
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
    Synthesizes the final Star Rating from the technique radar and the p90 strain profile.

    The composition is the **largest** of the eight absolute technique stars (issue #50), which
    is the extremum-dominant p-norm's own limit: the axes are on one scale now, so the chart's
    difficulty is the difficulty of its hardest technique measured in the units the Dan ladder
    is stated in. The p-norm's damping term was what let a chart collect a bonus for having
    several strong axes; measured over the benchmark with absolute anchors it lifts every tier's
    median clear of its band (0th 4.51 against [3.0, 4.0], 5th 6.05 against [5.0, 6.0], 10th
    8.36 against [7.0, 8.2]), so the composition retreats to the degenerate case rather than
    being re-tuned around the new scores — see `docs/adr/0016`.

    The strain rating stays as the fallback for a chart whose drivers are all zero and as the
    reported `raw_star_rating`: it is the same scale, read directly off the physical load, and
    is no longer part of the composition.
    """
    if options is None:
        options = RatingOptions()

    sr_raw = compute_raw_strain_star_rating(
        p90_strain,
        a=options.strain_a,
        b=options.strain_b,
        exp=options.strain_exp,
    )

    sr_norm = max(radar.to_vector())

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
