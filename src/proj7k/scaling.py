import math
from typing import Tuple, Dict, Any, Optional, Literal
from dataclasses import dataclass, asdict

DEFAULT_DIVISOR: int = 4
LOW_SPEED_BPM_THRESHOLD: float = 145.0
HIGH_SPEED_BPM_THRESHOLD: float = 180.0
LOW_SPEED_CAP: float = 5.0
REFERENCE_BPM: float = 170.0

ScalingRegime = Literal["LOW_SPEED_TRUNCATION", "STANDARD", "EXPONENTIAL_PENALTY"]


@dataclass
class ScalingConfig:
    divisor: int = DEFAULT_DIVISOR
    low_bpm_threshold: float = LOW_SPEED_BPM_THRESHOLD
    high_bpm_threshold: float = HIGH_SPEED_BPM_THRESHOLD
    low_speed_cap: float = LOW_SPEED_CAP
    reference_bpm: float = REFERENCE_BPM


def compute_action_window(bpm: float, divisor: int = DEFAULT_DIVISOR) -> float:
    """
    Computes the micro-action clock window in milliseconds:
    delta_t = 60000.0 / (bpm * divisor)
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
        base = ((bpm / low_bpm_threshold) ** 2) * ((low_bpm_threshold / reference_bpm) ** 1.5)
        factor = round(base, 4)
    elif bpm < high_bpm_threshold:
        regime = "STANDARD"
        base = (bpm / reference_bpm) ** 1.5
        factor = round(base, 4)
    else:
        regime = "EXPONENTIAL_PENALTY"
        base = (high_bpm_threshold / reference_bpm) ** 1.5
        bpm_ratio = (bpm - high_bpm_threshold) / 20.0
        exp_penalty = math.exp(0.55 * bpm_ratio)
        lock_amplification = 1.0 + 0.35 * (min(max(mean_locked_fingers, 0.0), 7.0) / 7.0)
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
    nps_factor = 1.0 + 0.02 * max(nps, 0.0)
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
