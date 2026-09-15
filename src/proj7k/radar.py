"""
8-Dimension Technique Radar Calibration & Orthogonal Cross-Suppression for om7k.

Calibrates the 8 canonical technique ratings:
- Regular: Jack, Tech, Speed, Stream
- LN: LN General, LN Tech, LN Inverse, LN Release

Applies orthogonal cross-suppression based on the separation matrix to eliminate
noise in pure specialized charts (e.g. 0 LN in pure Rice maps).
"""

from dataclasses import dataclass
import math
from typing import Any, Dict, List, Optional, Tuple

from proj7k.features import BeatmapFeatures, extract_beatmap_features
from proj7k.parser import Beatmap7K, NoteType
from proj7k.scaling import compute_inverse_score
from proj7k.strain import (
    StrainOptions,
    StrainTimeseriesProfile,
    compute_dual_hand_strain,
    compute_micro_speed_burst,
)

TECHNIQUE_NAMES: Tuple[str, ...] = (
    "jack",
    "tech",
    "speed",
    "stream",
    "ln_general",
    "ln_tech",
    "ln_inverse",
    "ln_release",
)


@dataclass(frozen=True)
class TechniqueRadar:
    """8-dimension normalized technique capability radar."""
    jack: float
    tech: float
    speed: float
    stream: float
    ln_general: float
    ln_tech: float
    ln_inverse: float
    ln_release: float
    dominant_technique: str
    dominant_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "jack": round(self.jack, 4),
            "tech": round(self.tech, 4),
            "speed": round(self.speed, 4),
            "stream": round(self.stream, 4),
            "ln_general": round(self.ln_general, 4),
            "ln_tech": round(self.ln_tech, 4),
            "ln_inverse": round(self.ln_inverse, 4),
            "ln_release": round(self.ln_release, 4),
            "dominant_technique": self.dominant_technique,
            "dominant_score": round(self.dominant_score, 4),
        }


@dataclass(frozen=True)
class RadarOptions:
    """Configuration options for technique radar calibration and suppression."""
    min_rice_hold_threshold: float = 0.02
    jack_threshold_ms: float = 160.0
    speed_burst_threshold_ms: float = 110.0
    w_judg_ms: float = 38.0


def _compute_jack_raw(beatmap: Beatmap7K, jack_threshold_ms: float) -> float:
    """Computes raw Jack intensity from same-column repeat intervals."""
    hos = beatmap.hit_objects
    col_times: Dict[int, List[float]] = {c: [] for c in range(7)}
    for ho in hos:
        col_times[ho.column].append(ho.time)

    for c in range(7):
        col_times[c].sort()

    total_jack_strain = 0.0
    jack_intervals_count = 0

    for c in range(7):
        times = col_times[c]
        if len(times) >= 2:
            for k in range(len(times) - 1):
                dt_ms = times[k + 1] - times[k]
                if 5.0 < dt_ms < jack_threshold_ms:
                    weight = (jack_threshold_ms - dt_ms) / jack_threshold_ms
                    # Quadratic emphasis for very tight jacks (< 120ms)
                    total_jack_strain += weight * (1.0 + max(0.0, (120.0 - dt_ms) / 60.0))
                    jack_intervals_count += 1

    if jack_intervals_count == 0:
        return 0.0

    duration_s = max(0.5, (max(ho.time for ho in hos) - min(ho.time for ho in hos)) / 1000.0)
    jack_rate = total_jack_strain / duration_s
    # Map jack_rate to difficulty scale [0, ~12]
    return min(12.0, math.pow(jack_rate, 0.65) * 1.85)


def _compute_speed_raw(beatmap: Beatmap7K, speed_threshold_ms: float) -> float:
    """Computes raw Speed intensity from rapid successive note presses."""
    hos = beatmap.hit_objects
    sorted_times_s = sorted(ho.time / 1000.0 for ho in hos)
    burst = compute_micro_speed_burst(
        sorted_times_s,
        min_interval_ms=5.0,
        max_interval_ms=speed_threshold_ms,
    )
    duration_s = max(0.5, (max(ho.time for ho in hos) - min(ho.time for ho in hos)) / 1000.0)
    burst_rate = burst / duration_s

    # High speed single note density
    nps = len(hos) / duration_s
    speed_factor = burst_rate * 0.45 + max(0.0, nps - 10.0) * 0.35
    return min(12.0, math.pow(max(0.0, speed_factor), 0.70) * 1.65)


def compute_technique_radar(
    beatmap: Beatmap7K,
    features: Optional[BeatmapFeatures] = None,
    strain_profile: Optional[StrainTimeseriesProfile] = None,
    options: Optional[RadarOptions] = None,
) -> TechniqueRadar:
    """
    Computes calibrated 8-dimension technique radar scores with cross-suppression.
    """
    if options is None:
        options = RadarOptions()

    hos = beatmap.hit_objects
    if not hos:
        return TechniqueRadar(
            jack=0.0,
            tech=0.0,
            speed=0.0,
            stream=0.0,
            ln_general=0.0,
            ln_tech=0.0,
            ln_inverse=0.0,
            ln_release=0.0,
            dominant_technique="None",
            dominant_score=0.0,
        )

    if features is None:
        features = extract_beatmap_features(beatmap)

    if strain_profile is None:
        strain_profile = compute_dual_hand_strain(beatmap)

    hold_pct = features.hold_pct
    p90_strain = strain_profile.p90_strain

    # --- 1. Compute Raw Drivers ---
    # Jack
    r_jack = _compute_jack_raw(beatmap, options.jack_threshold_ms)

    # Tech (finger decoupling, gap:1 shear, complex track transitions)
    gap1_factor = features.gap1_density * 3.2
    adj_factor = features.adj_density * 1.2
    r_tech = min(12.0, math.pow(gap1_factor + adj_factor, 0.70) * 1.60)

    # Speed (micro-speed burst tapping rate)
    r_speed = _compute_speed_raw(beatmap, options.speed_burst_threshold_ms)

    # Stream (sustained physical throughput with high average and peak NPS across multiple lanes)
    active_lanes = len({ho.column for ho in hos})
    lane_spread = max(0.0, min(1.0, (active_lanes - 2) / 2.0))
    stream_metric = features.avg_nps * 0.50 + features.peak_4m_nps * 0.50
    r_stream = min(12.0, math.pow(max(0.0, stream_metric), 0.68) * 0.75) * lane_spread

    # LN General (overall hold presence and sustained hold chords)
    ln_chord_density = hold_pct * features.avg_nps
    r_ln_gen = min(12.0, math.pow(ln_chord_density * 2.5, 0.72) * 1.50)

    # LN Tech (LN with gap1 and finger coordination constraints)
    ln_tech_factor = hold_pct * (features.gap1_density * 2.5 + features.adj_density * 1.5)
    r_ln_tech = min(12.0, math.pow(ln_tech_factor * 2.0, 0.70) * 1.60)

    # LN Inverse (high locked finger density, inverse score under BPM scaling)
    inverse_score = features.inverse_score
    locked_factor = features.mean_locked_fingers * 2.5
    r_ln_inv = min(12.0, math.pow(max(0.0, inverse_score * 3.5 + locked_factor), 0.72) * 1.75)

    # LN Release (antiphase rate and high-frequency release density)
    release_factor = features.antiphase_rate * 4.0 + hold_pct * (features.peak_1b_nps * 0.15)
    r_ln_rel = min(12.0, math.pow(max(0.0, release_factor * 2.2), 0.70) * 1.65)

    # --- 2. Orthogonal Cross-Suppression ---
    # Rule A: Pure Rice charts (hold_pct < min_rice_hold_threshold)
    # LN techniques are strictly suppressed to 0.0 (no LN exists)
    if hold_pct < options.min_rice_hold_threshold:
        r_ln_gen = 0.0
        r_ln_tech = 0.0
        r_ln_inv = 0.0
        r_ln_rel = 0.0

    # Rule B: Rice Jack vs LN cross-inhibition
    # Regular Jack is strictly a Rice discipline (centroid hold_pct = 0.016)
    # When LN dominates (hold_pct > 0.25), Rice Jack is cross-suppressed
    if hold_pct > 0.25:
        rice_ratio = max(0.0, 1.0 - (hold_pct - 0.25) / 0.25)
        r_jack = r_jack * rice_ratio

    # Rule C: Pure Jack specialization
    # If jack is overwhelmingly dominant and other rice features are minimal, suppress noise
    if active_lanes <= 2:
        r_stream = 0.0
        r_tech = 0.0
    elif r_jack > 3.0 and r_jack > r_stream * 2.0 and r_jack > r_tech * 1.5:
        r_stream = max(0.0, r_stream - (r_jack * 0.3))
        r_tech = max(0.0, r_tech - (r_jack * 0.2))

    # Ensure all values are non-negative
    scores: Dict[str, float] = {
        "jack": max(0.0, r_jack),
        "tech": max(0.0, r_tech),
        "speed": max(0.0, r_speed),
        "stream": max(0.0, r_stream),
        "ln_general": max(0.0, r_ln_gen),
        "ln_tech": max(0.0, r_ln_tech),
        "ln_inverse": max(0.0, r_ln_inv),
        "ln_release": max(0.0, r_ln_rel),
    }

    # Determine dominant technique and score
    max_tech = "None"
    max_score = 0.0
    for t_name, sc in scores.items():
        if sc > max_score:
            max_score = sc
            max_tech = t_name

    return TechniqueRadar(
        jack=scores["jack"],
        tech=scores["tech"],
        speed=scores["speed"],
        stream=scores["stream"],
        ln_general=scores["ln_general"],
        ln_tech=scores["ln_tech"],
        ln_inverse=scores["ln_inverse"],
        ln_release=scores["ln_release"],
        dominant_technique=max_tech,
        dominant_score=max_score,
    )
