"""
Inverse BPM scaling law: the tempo-dependent factor applied to raw physical metrics.

Every calibration constant of the law is named here and listed in `CALIBRATION_CONSTANTS`,
so the engine fingerprint covers it (`difficulty.engine_fingerprint`) and the
literal-coverage guard can tell a named constant apart from a literal baked into an
operator (issue #48).
"""

import math
from typing import Tuple, Dict, Any, Optional, Literal
from dataclasses import dataclass, asdict

DEFAULT_DIVISOR: int = 4
LOW_SPEED_BPM_THRESHOLD: float = 145.0
HIGH_SPEED_BPM_THRESHOLD: float = 180.0
LOW_SPEED_CAP: float = 5.0
REFERENCE_BPM: float = 170.0

#: Tempo ratio exponent of the scaling law: (bpm / reference) ** REFERENCE_EXPONENT. Carries
#: the super-linear penalty a faster chart imposes on the same physical metric.
REFERENCE_EXPONENT: float = 1.5

#: Exponent applied to (bpm / low_speed_threshold) inside the low-speed truncation regime,
#: which is deliberately shallower than REFERENCE_EXPONENT — that branch is a generous
#: cognitive-threshold regime, not a penalty.
LOW_SPEED_EXPONENT: float = 2.0

#: Width, in BPM above HIGH_SPEED_BPM_THRESHOLD, over which the exponential penalty ramps.
PENALTY_RAMP_BPM: float = 20.0

#: Gain of the exponential penalty per ramp unit, and the weight of the locked-finger
#: amplification added on top of it once a chart sits in the penalty regime.
PENALTY_EXP_GAIN: float = 0.55
LOCK_AMPLIFICATION_GAIN: float = 0.35

#: NPS weight of the inverse score: a denser chart raises the cognitive cost of every locked
#: finger, so the calibrated metric is multiplied by (1 + INVERSE_NPS_GAIN * nps).
INVERSE_NPS_GAIN: float = 0.02

ScalingRegime = Literal["LOW_SPEED_TRUNCATION", "STANDARD", "EXPONENTIAL_PENALTY"]


@dataclass
class ScalingConfig:
    divisor: int = DEFAULT_DIVISOR
    low_bpm_threshold: float = LOW_SPEED_BPM_THRESHOLD
    high_bpm_threshold: float = HIGH_SPEED_BPM_THRESHOLD
    low_speed_cap: float = LOW_SPEED_CAP
    reference_bpm: float = REFERENCE_BPM


def normalize_notation_bpm(
    annotated_bpm: float, note_value: float, divisor: int = DEFAULT_DIVISOR
) -> float:
    """
    Re-expresses an annotated tempo on the single notation scale the action clock is defined on.

    A timing point states a tempo together with the note value the chart is written in, and
    charters do not agree on the latter: measured over the 120-chart benchmark corpus, a chart's
    median inter-onset interval spans 1/12 to 1/1 of its annotated beat, so the same physical
    spacing is annotated anywhere between four times slower and three times faster. Feeding the
    annotated number straight into `compute_action_window` — or into the `LOW_SPEED_*` /
    `HIGH_SPEED_BPM_THRESHOLD` regime switches — therefore reads a different note value per
    chart and misplaces every tempo-driven operator by up to 2x (issue #51).

    `note_value` is that observed fraction (see `parser.observed_note_value`). The result is the
    tempo that would state the same physical spacing if the chart were written in `1/divisor`
    notes, so the two notations of one chart normalize to the same number. It is deliberately
    unrounded: this number reaches the strain accumulator, where a 1-ulp shift moves every
    downstream sample.
    """
    if annotated_bpm <= 0.0 or note_value <= 0.0 or divisor <= 0:
        return annotated_bpm
    return annotated_bpm / (divisor * note_value)


def compute_action_window(bpm: float, divisor: int = DEFAULT_DIVISOR) -> float:
    """
    Computes the micro-action clock window in milliseconds:
    delta_t = 60000.0 / (bpm * divisor)

    `bpm` is expected to be on the notation scale this window is defined on, i.e. the output of
    `normalize_notation_bpm` (via `parser.notation_normalized_bpm`) rather than a raw annotation.
    """
    if bpm <= 0:
        return float("inf")
    return round(60000.0 / (bpm * divisor), 4)


def compute_inverse_scaling_factor(
    bpm: float,
    mean_locked_fingers: float = 0.0,
    low_bpm_threshold: float = LOW_SPEED_BPM_THRESHOLD,
    high_bpm_threshold: float = HIGH_SPEED_BPM_THRESHOLD,
    reference_bpm: float = REFERENCE_BPM,
) -> Tuple[float, ScalingRegime]:
    """
    Computes the scaling factor and regime according to the Inverse BPM Scaling Law:
    1. Low speed (<= 145 BPM): Cognitive threshold regime, generous delta_t, dampened scaling.
    2. Mid speed (145 < BPM < 180): Standard transition regime.
    3. High speed (>= 180 BPM): Extreme micro-tolerance compression, exponential penalty.
    """
    if bpm <= 0:
        return (0.0, "LOW_SPEED_TRUNCATION")

    if bpm <= low_bpm_threshold:
        regime: ScalingRegime = "LOW_SPEED_TRUNCATION"
        base = ((bpm / low_bpm_threshold) ** LOW_SPEED_EXPONENT) * (
            (low_bpm_threshold / reference_bpm) ** REFERENCE_EXPONENT
        )
        factor = round(base, 4)
    elif bpm < high_bpm_threshold:
        regime = "STANDARD"
        base = (bpm / reference_bpm) ** REFERENCE_EXPONENT
        factor = round(base, 4)
    else:
        regime = "EXPONENTIAL_PENALTY"
        base = (high_bpm_threshold / reference_bpm) ** REFERENCE_EXPONENT
        bpm_ratio = (bpm - high_bpm_threshold) / PENALTY_RAMP_BPM
        exp_penalty = math.exp(PENALTY_EXP_GAIN * bpm_ratio)
        lock_amplification = 1.0 + LOCK_AMPLIFICATION_GAIN * (
            min(max(mean_locked_fingers, 0.0), 7.0) / 7.0
        )
        factor = round(base * exp_penalty * lock_amplification, 4)

    return (factor, regime)


def apply_inverse_bpm_scaling(
    raw_value: float,
    bpm: float,
    divisor: int = DEFAULT_DIVISOR,
    low_bpm_threshold: float = LOW_SPEED_BPM_THRESHOLD,
    high_bpm_threshold: float = HIGH_SPEED_BPM_THRESHOLD,
    low_speed_cap: float = LOW_SPEED_CAP,
    reference_bpm: float = REFERENCE_BPM,
) -> Tuple[float, float, ScalingRegime]:
    """
    Applies the Inverse BPM Scaling Law non-linear gating operator to a raw physical metric
    (such as mean_locked_fingers).

    Enforces:
    - Hard ceiling truncation at low speed (<= 145 BPM) to prevent false inflation from
      the Negative-Space Inversion Illusion (strictly capped at low_speed_cap ~ 5★).
    - Exponential penalty at high speed (>= 180~200+ BPM) due to micro-tolerance compression.

    Returns:
        (calibrated_value, scaling_factor, regime)
    """
    factor, regime = compute_inverse_scaling_factor(
        bpm=bpm,
        mean_locked_fingers=raw_value,
        low_bpm_threshold=low_bpm_threshold,
        high_bpm_threshold=high_bpm_threshold,
        reference_bpm=reference_bpm,
    )
    calibrated = raw_value * factor
    if regime == "LOW_SPEED_TRUNCATION":
        calibrated = min(calibrated, low_speed_cap)

    return (round(calibrated, 4), factor, regime)


def compute_inverse_score(
    mean_locked_fingers: float,
    bpm: float,
    nps: float = 0.0,
    divisor: int = DEFAULT_DIVISOR,
    low_speed_cap: float = LOW_SPEED_CAP,
) -> float:
    """
    Computes InverseScore = f(MeanLocked, delta_t_action, NPS)
    as specified in ADR-0006 and Master Specification.
    Strictly preserves the 5★ ceiling under low-speed truncation.
    """
    calibrated, _, regime = apply_inverse_bpm_scaling(
        raw_value=mean_locked_fingers,
        bpm=bpm,
        divisor=divisor,
        low_speed_cap=low_speed_cap,
    )
    nps_factor = 1.0 + INVERSE_NPS_GAIN * max(nps, 0.0)
    score = calibrated * nps_factor
    if regime == "LOW_SPEED_TRUNCATION":
        score = min(score, low_speed_cap)

    return round(score, 4)


@dataclass
class ClockWindowRecord:
    tier: str
    bpm: float
    delta_t_ms: float
    raw_value: float
    calibrated_value: float
    scaling_factor: float
    regime: ScalingRegime
    id: Optional[int] = None
    song: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


#: Names of every calibration constant above, for the methodology fingerprint — see
#: `calibration.block_fingerprint_constants`. The coverage guard keeps this list complete.
CALIBRATION_CONSTANTS: Tuple[str, ...] = (
    "DEFAULT_DIVISOR",
    "LOW_SPEED_BPM_THRESHOLD",
    "HIGH_SPEED_BPM_THRESHOLD",
    "LOW_SPEED_CAP",
    "REFERENCE_BPM",
    "REFERENCE_EXPONENT",
    "LOW_SPEED_EXPONENT",
    "PENALTY_RAMP_BPM",
    "PENALTY_EXP_GAIN",
    "LOCK_AMPLIFICATION_GAIN",
    "INVERSE_NPS_GAIN",
)
