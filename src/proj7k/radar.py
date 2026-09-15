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
    compute_raw_strain_star_rating,
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
    min_rice_hold_threshold: float = 0.05
    jack_threshold_ms: float = 160.0
    speed_burst_threshold_ms: float = 110.0
    w_judg_ms: float = 38.0


def _compute_jack_raw(beatmap: Beatmap7K, jack_threshold_ms: float) -> float:
    """Computes raw Jack intensity from same-column repeat intervals."""
    hos = beatmap.hit_objects
    col_times: Dict[int, List[float]] = {c: [] for c in range(7)}
    for ho in hos:
        col_times[ho.column].append(ho.time)

    total_jack_strain = 0.0
    jack_intervals_count = 0
    for col, times in col_times.items():
        if len(times) >= 2:
            stimes = sorted(times)
            for k in range(len(stimes) - 1):
                dt_ms = stimes[k + 1] - stimes[k]
                if dt_ms < jack_threshold_ms:
                    total_jack_strain += (jack_threshold_ms - dt_ms) / jack_threshold_ms
                    jack_intervals_count += 1

    if jack_intervals_count == 0:
        return 0.0

    duration_s = max(0.5, (max(ho.time for ho in hos) - min(ho.time for ho in hos)) / 1000.0)
    return total_jack_strain / duration_s


def _compute_speed_raw(beatmap: Beatmap7K, speed_threshold_ms: float, avg_nps: float) -> float:
    """Computes raw Speed intensity from rapid successive note presses across different columns."""
    hos = sorted(beatmap.hit_objects, key=lambda x: x.time)
    burst = 0.0
    for k in range(len(hos) - 1):
        if hos[k].column != hos[k + 1].column:
            dt_ms = hos[k + 1].time - hos[k].time
            if 5.0 < dt_ms < speed_threshold_ms:
                burst += math.pow((speed_threshold_ms - dt_ms) / 50.0, 1.35)

    duration_s = max(0.5, (max(ho.time for ho in hos) - min(ho.time for ho in hos)) / 1000.0) if hos else 1.0
    burst_rate = burst / duration_s
    return burst_rate * 1.50 + max(0.0, avg_nps - 10.0) * 0.60


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

    hold_ratio = (features.hold_pct / 100.0) if features.hold_pct > 1.0 else features.hold_pct
    p90_strain = strain_profile.p90_strain
    sr_base = compute_raw_strain_star_rating(p90_strain)

    # --- 1. Compute Raw Drivers ---
    # Jack
    r_jack = _compute_jack_raw(beatmap, options.jack_threshold_ms)

    # Tech (finger decoupling, gap:1 shear, complex track transitions)
    r_tech = features.gap1_density * 2.0 + features.adj_density * 0.80

    # Speed (micro-speed burst tapping rate)
    r_speed = _compute_speed_raw(beatmap, options.speed_burst_threshold_ms, features.avg_nps)

    # Stream (sustained physical throughput with high average and peak NPS across multiple lanes, penalized by jacks)
    active_lanes = len({ho.column for ho in hos})
    lane_spread = max(0.0, min(1.0, (active_lanes - 2) / 2.0))
    raw_stream = (features.avg_nps * 0.60 + features.peak_4m_nps * 0.40) - (r_jack * 0.80)
    r_stream = max(0.0, raw_stream) * lane_spread

    # LN General (overall hold presence and sustained hold chords)
    r_ln_gen = hold_ratio * features.avg_nps * 1.50

    # LN Tech (LN with gap1 and finger coordination constraints)
    r_ln_tech = hold_ratio * (features.gap1_density * 2.0 + features.adj_density * 1.0) * 2.0

    # LN Inverse (high locked finger density, inverse score under BPM scaling)
    r_ln_inv = (features.inverse_score * 0.80 + features.mean_locked_fingers * 1.50) * min(1.0, hold_ratio * 2.0)

    # LN Release (antiphase rate, release rate and high-frequency release density)
    duration_s = max(0.5, features.duration_seconds)
    release_rate = features.ln_count / duration_s
    r_ln_rel = (release_rate * 0.80 + features.antiphase_rate * 1.50 + hold_ratio * features.peak_1b_nps * 0.10) * min(1.0, hold_ratio * 2.0)

    # --- 2. Orthogonal Cross-Suppression ---
    # Rule A: Pure Rice charts (hold_ratio < min_rice_hold_threshold)
    if hold_ratio < options.min_rice_hold_threshold:
        r_ln_gen = 0.0
        r_ln_tech = 0.0
        r_ln_inv = 0.0
        r_ln_rel = 0.0
    elif hold_ratio > 0.30:
        r_jack *= max(0.0, 1.0 - (hold_ratio - 0.30) / 0.20)

    # Rule C: Lane spread gating & Pure Jack specialization
    if active_lanes <= 2:
        r_stream = 0.0
        r_tech = 0.0
        r_speed = 0.0
    elif r_jack > 3.0:
        if r_jack > r_stream * 1.5:
            r_stream = max(0.0, r_stream - (r_jack * 0.5))
        if r_jack > r_tech * 1.5:
            r_tech = max(0.0, r_tech - (r_jack * 0.3))
        if r_jack > r_speed * 0.7:
            r_speed = max(0.0, r_speed - (r_jack * 0.6))

    raw_scores: Dict[str, float] = {
        "jack": max(0.0, r_jack),
        "tech": max(0.0, r_tech),
        "speed": max(0.0, r_speed),
        "stream": max(0.0, r_stream),
        "ln_general": max(0.0, r_ln_gen),
        "ln_tech": max(0.0, r_ln_tech),
        "ln_inverse": max(0.0, r_ln_inv),
        "ln_release": max(0.0, r_ln_rel),
    }

    max_raw = max(raw_scores.values()) if raw_scores else 0.0
    if max_raw <= 1e-6 or sr_base <= 1e-6:
        scores = {k: 0.0 for k in raw_scores}
    else:
        scores = {
            k: min(12.0, sr_base * math.pow(v / max_raw, 0.75))
            for k, v in raw_scores.items()
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
