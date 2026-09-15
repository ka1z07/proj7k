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
    jack_threshold_ms: float = 220.0
    speed_burst_threshold_ms: float = 110.0
    w_judg_ms: float = 38.0
    chord_eps_ms: float = 8.0
    jack_m_max: float = 1.5
    jack_tau: float = 3.0
    jack_chord_boost: float = 0.35
    stream_tort_weight: float = 0.60
    stream_gap1_weight: float = 0.30


def _partition_chord_steps(beatmap: Beatmap7K, chord_eps_ms: float = 8.0) -> List[List]:
    """Partitions beatmap hit objects into discrete chord steps S_0, S_1, ..., S_M."""
    hos = sorted(beatmap.hit_objects, key=lambda x: (x.time, x.column))
    if not hos:
        return []
    steps: List[List] = []
    curr = [hos[0]]
    curr_t = hos[0].time
    for ho in hos[1:]:
        if abs(ho.time - curr_t) <= chord_eps_ms:
            curr.append(ho)
        else:
            steps.append(curr)
            curr = [ho]
            curr_t = ho.time
    steps.append(curr)
    return steps


def _compute_jack_and_stream_raw(
    beatmap: Beatmap7K,
    options: RadarOptions,
) -> Tuple[float, float, float]:
    """
    Computes decoupled raw Chordjack and Stream intensities using discrete step distance
    and stream topological modulation.

    Returns:
        (r_jack, r_stream, jack_ratio)
    """
    hos = beatmap.hit_objects
    if not hos:
        return 0.0, 0.0, 0.0

    duration_s = max(0.5, (max(ho.time for ho in hos) - min(ho.time for ho in hos)) / 1000.0)
    steps = _partition_chord_steps(beatmap, options.chord_eps_ms)

    col_state: Dict[int, Tuple[int, float, int]] = {c: (-999, -1e9, 1) for c in range(7)}
    jack_count = 0
    jack_strain_total = 0.0

    flow_history: List[Tuple[float, int]] = []
    reversals = 0
    gap1_count = 0

    for k, step in enumerate(steps):
        c_size = len(step)
        for ho in step:
            c = ho.column
            lk, lt, run_l = col_state[c]
            dk = k - lk
            dt = ho.time - lt

            # Discrete step-distance criterion: Delta k == 1 and Delta t <= jack_threshold_ms
            if dk == 1 and dt <= options.jack_threshold_ms:
                jack_count += 1
                new_run_l = run_l + 1

                # Chordjack run-length saturation W(L) = 1.0 + M_max * tanh((L - 2) / tau)
                w_l = 1.0 + options.jack_m_max * math.tanh((new_run_l - 2) / options.jack_tau)
                # Multi-key chord arm vibration load
                c_factor = 1.0 + options.jack_chord_boost * (c_size - 1)
                # Frequency strain
                s_factor = math.pow(options.jack_threshold_ms / max(35.0, dt), 1.25)

                jack_strain_total += s_factor * w_l * c_factor
                col_state[c] = (k, ho.time, new_run_l)
            else:
                col_state[c] = (k, ho.time, 1)
                flow_history.append((ho.time, c))
                if len(flow_history) >= 3:
                    t0, c0 = flow_history[-3]
                    t1, c1 = flow_history[-2]
                    t2, c2 = flow_history[-1]
                    if (t2 - t0) <= 250.0:
                        d1 = c1 - c0
                        d2 = c2 - c1
                        if (d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0):
                            reversals += 1
                        if abs(c1 - c0) == 2 or abs(c2 - c1) == 2:
                            gap1_count += 1

    total_notes = len(hos)
    active_lanes = len({ho.column for ho in hos})
    lane_spread = max(0.0, min(1.0, (active_lanes - 2) / 2.0))

    jack_ratio = jack_count / total_notes if total_notes else 0.0
    flow_count = total_notes - jack_count
    flow_nps = flow_count / duration_s

    tortuosity = reversals / max(1, flow_count)
    gap1_ratio = gap1_count / max(1, flow_count)
    t_stream = 1.0 + options.stream_tort_weight * tortuosity + options.stream_gap1_weight * gap1_ratio

    # Jack raw driver with chordjack density synergy
    c_syn = 1.0 + 3.0 * max(0.0, jack_ratio - 0.08)
    r_jack = (jack_strain_total / duration_s) * 1.48 * c_syn

    # Stream raw driver with dominant chordjack suppression
    supp = max(0.05, 1.0 - 3.6 * max(0.0, jack_ratio - 0.11))
    r_stream = flow_nps * t_stream * lane_spread * 0.85 * supp

    return r_jack, r_stream, jack_ratio


def _compute_jack_raw(beatmap: Beatmap7K, jack_threshold_ms: float = 220.0) -> float:
    """Computes raw Jack intensity via _compute_jack_and_stream_raw."""
    r_jack, _, _ = _compute_jack_and_stream_raw(beatmap, RadarOptions(jack_threshold_ms=jack_threshold_ms))
    return r_jack



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
    # Decoupled Jack and Stream drivers via discrete step distance and topological modulation
    r_jack, r_stream, jack_ratio = _compute_jack_and_stream_raw(beatmap, options)

    # Tech (finger decoupling, gap:1 shear, complex track transitions)
    r_tech = features.gap1_density * 2.0 + features.adj_density * 0.80

    # Speed (micro-speed burst tapping rate)
    r_speed = _compute_speed_raw(beatmap, options.speed_burst_threshold_ms, features.avg_nps)

    active_lanes = len({ho.column for ho in hos})

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

    # Rule C: Lane spread gating & Pure Jack/Stream specialization
    if active_lanes <= 2:
        r_stream = 0.0
        r_tech = 0.0
        r_speed = 0.0
    else:
        if r_jack > 3.0:
            if r_jack > r_stream * 1.5:
                r_stream = max(0.0, r_stream - (r_jack * 0.5))
            if r_jack > r_tech * 1.5:
                r_tech = max(0.0, r_tech - (r_jack * 0.3))
            if r_jack > r_speed * 0.7:
                r_speed = max(0.0, r_speed - (r_jack * 0.6))
        # Rule D: Dominant Stream cross-suppression of residual jack noise
        if r_stream > 3.0 and jack_ratio < 0.13:
            if r_stream > r_jack * 0.9:
                r_jack = max(0.0, r_jack - (r_stream * 0.40))


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
