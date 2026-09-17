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
from proj7k.parser import Beatmap7K, HitObject, NoteType
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
class Tech4DComponents:
    """
    Four-dimensional unorthodox permutation components (Omega_irreg, ADR-0008).
    - tortuosity: flow reversals ratio
    - bracket_shear: bracket inversion density and inter-finger shear strain
    - spatial_entropy: spatial transition Shannon entropy
    - rhythm_irreg: rhythmic irregularity and microtiming jerk
    """
    tortuosity: float
    bracket_shear: float
    spatial_entropy: float
    rhythm_irreg: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "tortuosity": round(self.tortuosity, 4),
            "bracket_shear": round(self.bracket_shear, 4),
            "spatial_entropy": round(self.spatial_entropy, 4),
            "rhythm_irreg": round(self.rhythm_irreg, 4),
        }


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
    tech_4d: Optional[Tech4DComponents] = None

    def to_vector(self) -> List[float]:
        return [
            self.jack,
            self.tech,
            self.speed,
            self.stream,
            self.ln_general,
            self.ln_tech,
            self.ln_inverse,
            self.ln_release,
        ]

    def to_dict(self) -> Dict[str, Any]:
        d = {
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
        if self.tech_4d is not None:
            d["tech_4d"] = self.tech_4d.to_dict()
        return d


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
    jack_decay_tau_s: float = 1.0
    jack_quantile_p90_weight: float = 0.70
    jack_quantile_top5_weight: float = 0.30
    stream_tort_weight: float = 0.50
    stream_bracket_weight: float = 0.40
    ln_gen_concurrent_weight: float = 0.30
    ln_inv_score_weight: float = 2.50
    ln_inv_lock_weight: float = 1.30
    ln_release_rate_weight: float = 0.75
    ln_release_antiphase_weight: float = 1.20
    tech_coupling_gamma: float = 1.25
    tech_coupling_lambda: float = 2.85
    tech_tort_weight: float = 0.35
    tech_bracket_weight: float = 0.70
    tech_spatial_weight: float = 0.30
    tech_rhythm_weight: float = 1.20
    ln_tech_coupling_lambda: float = 3.30


def _partition_chord_steps(beatmap: Beatmap7K, chord_eps_ms: float = 8.0) -> List[List[HitObject]]:
    """Partitions beatmap hit objects into discrete chord steps S_0, S_1, ..., S_M."""
    hos = sorted(beatmap.hit_objects, key=lambda x: (x.time, x.column))
    if not hos:
        return []
    steps: List[List[HitObject]] = []
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
) -> Tuple[float, float, float, int, int, float, float]:
    """
    Computes decoupled raw Chordjack and Stream intensities using discrete step distance,
    continuous strain decay accumulation, and stream topological modulation (ADR-0007, ADR-0008).

    Returns:
        (r_jack, r_stream, jack_ratio, max_run_length, jack_count)
    """
    hos = beatmap.hit_objects
    if not hos:
        return 0.0, 0.0, 0.0, 1, 0

    duration_s = max(0.5, (max(ho.time for ho in hos) - min(ho.time for ho in hos)) / 1000.0)
    steps = _partition_chord_steps(beatmap, options.chord_eps_ms)

    # col_state: Dict[column, Tuple[last_step_k, last_time, run_length]]
    col_state: Dict[int, Tuple[int, float, int]] = {c: (-999, -1e9, 1) for c in range(7)}
    jack_count = 0
    max_run_length = 1
    step_impulses: List[Tuple[float, float]] = []

    flow_history: List[Tuple[float, int]] = []
    reversals = 0
    bracket_inversions = 0

    prev_left_cols: set = set()
    prev_right_cols: set = set()
    prev_step_time = -1e9

    for k, step in enumerate(steps):
        c_size = len(step)
        step_time = step[0].time
        step_cols = {ho.column for ho in step}
        curr_left = {c for c in step_cols if c in (0, 1, 2)}
        curr_right = {c for c in step_cols if c in (4, 5, 6)}

        # Bracket phase inversion detection between consecutive steps (dt < 120ms per CONTEXT.md)
        dt_step = step_time - prev_step_time
        if 0.0 < dt_step < 120.0:
            # Left hand: outer/inner {0, 2} vs mid {1}
            if ({0, 2}.issubset(prev_left_cols) and 1 in curr_left) or (1 in prev_left_cols and {0, 2}.issubset(curr_left)):
                bracket_inversions += 1
            # Right hand: outer/inner {4, 6} vs mid {5}
            if ({4, 6}.issubset(prev_right_cols) and 5 in curr_right) or (5 in prev_right_cols and {4, 6}.issubset(curr_right)):
                bracket_inversions += 1

        prev_left_cols = curr_left
        prev_right_cols = curr_right
        prev_step_time = step_time

        step_impulse = 0.0
        for ho in step:
            c = ho.column
            last_step_k, last_time_ms, run_length = col_state[c]
            dk = k - last_step_k
            dt = ho.time - last_time_ms

            # Discrete step-distance criterion: Delta k == 1 and Delta t <= jack_threshold_ms
            if dk == 1 and dt <= options.jack_threshold_ms:
                jack_count += 1
                new_run_length = run_length + 1
                if new_run_length > max_run_length:
                    max_run_length = new_run_length

                # Chordjack run-length saturation W(L) = 1.0 + M_max * tanh((L - 2) / tau)
                w_l = 1.0 + options.jack_m_max * math.tanh((new_run_length - 2) / options.jack_tau)
                # Multi-key chord arm vibration load
                c_factor = 1.0 + options.jack_chord_boost * (c_size - 1)
                # Frequency strain
                s_factor = math.pow(options.jack_threshold_ms / max(35.0, dt), 1.25)

                imp = s_factor * w_l * c_factor
                step_impulse += imp
                col_state[c] = (k, ho.time, new_run_length)
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

        if step_impulse > 0.0:
            step_impulses.append((step_time / 1000.0, step_impulse))

    # Continuous strain decay accumulation S(t) = S(t - dt) * exp(-dt / tau) + dS (ADR-0008)
    dt_grid = 0.25
    decay = math.exp(-dt_grid / options.jack_decay_tau_s)
    t_start = min(ho.time for ho in hos) / 1000.0
    t_end = max(ho.time for ho in hos) / 1000.0

    strains: List[float] = []
    t = t_start
    imp_idx = 0
    curr_s = 0.0
    while t <= t_end + dt_grid:
        curr_s *= decay
        while imp_idx < len(step_impulses) and step_impulses[imp_idx][0] <= t:
            curr_s += step_impulses[imp_idx][1]
            imp_idx += 1
        strains.append(curr_s)
        t += dt_grid

    # Quantile pooling: 0.70 * P90 + 0.30 * Top5%Mean (ADR-0008)
    sorted_s = sorted(strains) if strains else [0.0]
    p90 = sorted_s[int(len(sorted_s) * 0.90)]
    n_top5 = max(1, int(len(sorted_s) * 0.05))
    top5_mean = sum(sorted_s[-n_top5:]) / n_top5
    pooled_jack = options.jack_quantile_p90_weight * p90 + options.jack_quantile_top5_weight * top5_mean

    total_notes = len(hos)
    active_lanes = len({ho.column for ho in hos})
    lane_spread = max(0.0, min(1.0, (active_lanes - 2) / 2.0))

    jack_ratio = jack_count / total_notes if total_notes else 0.0
    flow_count = total_notes - jack_count
    flow_nps = flow_count / duration_s

    tortuosity = reversals / max(1, flow_count)
    bracket_density = bracket_inversions / max(1, len(steps))
    t_stream = 1.0 + options.stream_tort_weight * tortuosity + options.stream_bracket_weight * bracket_density

    # Jack raw driver combining sustained duration rate and local pooled burst strain
    c_syn = 1.0 + 3.0 * max(0.0, jack_ratio - 0.08)
    duration_rate = (sum(x[1] for x in step_impulses) / duration_s) * 1.48 * c_syn
    burst_driver = pooled_jack * 2.0 * c_syn
    r_jack = max(duration_rate, burst_driver)

    # Stream raw driver with dominant chordjack suppression
    supp = max(0.05, 1.0 - 3.6 * max(0.0, jack_ratio - 0.11))
    r_stream = flow_nps * t_stream * lane_spread * 0.85 * supp

    return r_jack, r_stream, jack_ratio, max_run_length, jack_count, tortuosity, bracket_density


def _compute_jack_raw(beatmap: Beatmap7K, jack_threshold_ms: float = 220.0) -> float:
    """Computes raw Jack intensity via _compute_jack_and_stream_raw."""
    r_jack, *_ = _compute_jack_and_stream_raw(beatmap, RadarOptions(jack_threshold_ms=jack_threshold_ms))
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

    hold_ratio = features.hold_pct / 100.0
    p90_strain = strain_profile.p90_strain
    sr_base = compute_raw_strain_star_rating(p90_strain)

    # --- 1. Compute Raw Drivers ---
    # Decoupled Jack and Stream drivers via discrete step distance, continuous strain decay,
    # and topological modulation (ADR-0007, ADR-0008)
    (
        r_jack,
        r_stream,
        jack_ratio,
        max_run,
        jack_count,
        tortuosity,
        bracket_density,
    ) = _compute_jack_and_stream_raw(beatmap, options)

    # Speed (micro-speed burst tapping rate)
    r_speed = _compute_speed_raw(beatmap, options.speed_burst_threshold_ms, features.avg_nps)

    active_lanes = len({ho.column for ho in hos})

    # Rule C on speed and jack before Rule D
    if r_jack > 3.0:
        if r_jack > r_speed * 0.7:
            r_speed = max(0.0, r_speed - (r_jack * 0.6))

    # Effective Jack for kinetic base (reflecting Rule D soft-cap on stream noise)
    if r_stream > 3.0 and jack_ratio < 0.14:
        if max_run >= 2 and jack_count >= 10:
            eff_jack_base = min(r_jack, r_stream * 0.82)
            r_jack = min(r_jack, r_stream * 0.82)
        else:
            eff_jack_base = max(0.0, r_jack - (r_stream * 0.40))
            r_jack = max(0.0, r_jack - (r_stream * 0.40))
    else:
        eff_jack_base = r_jack

    # Kinetic base energy K_base = max(r_stream, r_speed, min(eff_jack, r_stream * 1.25)) (ADR-0008)
    k_base = max(r_stream, r_speed, min(eff_jack_base, r_stream * 1.25))

    # Four-dimensional Unorthodox Permutation Operator Omega_irreg (ADR-0008)
    # 1. Flow tortuosity (reversals)
    t_tort = max(0.0, tortuosity - 0.45)
    # 2. Bracket and shear
    shear_ratio = (features.gap1_density + 0.5 * features.adj_density) / max(1.0, features.avg_nps)
    b_bracket = bracket_density * min(1.0, features.rhythm_irreg * 2.0) + shear_ratio * 0.30
    # 3. Spatial transition entropy
    s_spatial = max(0.0, features.spatial_entropy - 0.88) / 0.12
    # 4. Rhythmic irregularity
    r_rhythm = max(0.0, features.rhythm_irreg - 0.20)

    tech_4d = Tech4DComponents(
        tortuosity=round(tortuosity, 4),
        bracket_shear=round(b_bracket, 4),
        spatial_entropy=round(features.spatial_entropy, 4),
        rhythm_irreg=round(features.rhythm_irreg, 4),
    )

    omega_irreg = (
        1.0
        + options.tech_tort_weight * t_tort
        + options.tech_bracket_weight * b_bracket
        + options.tech_spatial_weight * s_spatial
        + options.tech_rhythm_weight * r_rhythm
    )

    # Tech (kinetic technique coupling) (ADR-0008)
    tech_excess = max(0.0, omega_irreg - 1.0)
    raw_mult = math.pow(tech_excess, options.tech_coupling_gamma) * options.tech_coupling_lambda
    if raw_mult > 1.0:
        mult = 1.0 + 0.15 * math.tanh((raw_mult - 1.0) / 0.20)
        r_tech = k_base * mult
        r_jack = max(0.0, r_jack - (r_tech * 0.40))
    else:
        r_tech = k_base * raw_mult * 0.90

    # LN General (overall hold presence, sustained hold chords, and concurrent spatial flux) (ADR-0008)
    concurrent_factor = 1.0 + options.ln_gen_concurrent_weight * features.mean_locked_fingers
    r_ln_gen = hold_ratio * features.avg_nps * concurrent_factor * 1.50

    # LN Tech (kinetic coupling with LN flux and unorthodox permutation) (ADR-0008)
    base_ln_flux = hold_ratio * features.avg_nps * 1.50
    finger_freedom = (features.gap1_density + 0.8) / max(1.0, features.mean_locked_fingers)
    antiphase_boost = 1.0 + 0.05 * features.antiphase_rate
    raw_ln_mult = (
        math.pow(tech_excess, 1.15)
        * options.ln_tech_coupling_lambda
        * finger_freedom
        * antiphase_boost
    )
    is_ln_tech_chart = (
        (tech_excess >= 0.55 and finger_freedom >= 0.75 and features.mean_locked_fingers < 2.85)
        or (features.gap1_density >= 1.60 and finger_freedom >= 0.80)
    )
    if raw_ln_mult > 1.0 and hold_ratio >= options.min_rice_hold_threshold:
        ln_mult = 1.0 + 0.15 * math.tanh((raw_ln_mult - 1.0) / 0.20)
        r_ln_tech = max(base_ln_flux, r_ln_gen) * ln_mult if is_ln_tech_chart else (base_ln_flux * ln_mult)
    else:
        r_ln_tech = (base_ln_flux * raw_ln_mult * 0.90) if hold_ratio >= options.min_rice_hold_threshold else 0.0

    # LN Inverse (high locked finger density, inverse score under micro-action scaling) (ADR-0006, ADR-0008)
    lock_load = math.pow(max(0.0, features.mean_locked_fingers - 2.0) / 2.0, 2.0)
    r_ln_inv = (
        features.inverse_score * options.ln_inv_score_weight
        + features.avg_nps * hold_ratio * lock_load * options.ln_inv_lock_weight
    ) * min(1.0, hold_ratio * 2.0)

    # LN Release (staccato release rate, exclusive antiphase rate, peak burst release) (ADR-0008)
    duration_s = max(0.5, features.duration_seconds)
    release_rate = features.ln_count / duration_s
    r_ln_rel = (
        release_rate * options.ln_release_rate_weight
        + features.antiphase_rate * options.ln_release_antiphase_weight
        + hold_ratio * features.peak_1b_nps * 0.08
    ) * min(1.0, hold_ratio * 2.0)

    # --- 2. Orthogonal Cross-Suppression ---
    # Rule A: Pure Rice charts (hold_ratio < min_rice_hold_threshold)
    if hold_ratio < options.min_rice_hold_threshold:
        r_ln_gen = 0.0
        r_ln_tech = 0.0
        r_ln_inv = 0.0
        r_ln_rel = 0.0
    elif hold_ratio > 0.30:
        r_jack *= max(0.0, 1.0 - (hold_ratio - 0.30) / 0.20)

    # Rule C: Lane spread gating and Pure Jack specialization
    if active_lanes <= 2:
        r_stream = 0.0
        r_tech = 0.0
        r_speed = 0.0
    else:
        # Rule C: Genuine Jack dominance suppresses competing stream/tech dimensions
        if r_jack > 3.0:
            if jack_ratio >= 0.15 and r_jack > r_stream * 1.05:
                r_stream = max(0.0, r_stream - (r_jack * 0.5))
            if r_jack > r_tech * 1.5:
                r_tech = max(0.0, r_tech - (r_jack * 0.3))

    # Rule E: Inverse specialization gating (ADR-0008)
    # When a chart enters the severe inverted state (mean_locked_fingers >= 4.0 and hold_ratio >= 0.85),
    # the motor-cognitive burden is dominated by Inverse rather than General hold volume.
    if features.mean_locked_fingers >= 4.0 and hold_ratio >= 0.85:
        if r_ln_inv > r_ln_gen * 0.80:
            r_ln_gen = max(0.0, r_ln_gen - (r_ln_inv * 0.35))
            r_ln_tech = max(0.0, r_ln_tech - (r_ln_inv * 0.35))

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

    # Determine dominant technique and score (based on raw uncompressed intensity to break ceiling ties)
    if max_raw <= 1e-6:
        max_tech = "None"
        max_score = 0.0
    else:
        max_tech = max(raw_scores.keys(), key=lambda k: raw_scores[k])
        max_score = scores[max_tech]

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
        tech_4d=tech_4d,
    )


def compute_tech_4d_components(
    beatmap: Beatmap7K,
    features: BeatmapFeatures,
    options: Optional[RadarOptions] = None,
) -> Tech4DComponents:
    """
    Extracts normalized 4D unorthodox permutation components:
    - tortuosity: flow reversal ratio
    - bracket_shear: bracket inversion density and finger shear ratio
    - spatial_entropy: spatial transition entropy
    - rhythm_irreg: rhythmic irregularity and microtiming jerk
    """
    opts = options or RadarOptions()
    *_, tortuosity, bracket_density = _compute_jack_and_stream_raw(beatmap, opts)
    shear_ratio = (features.gap1_density + 0.5 * features.adj_density) / max(1.0, features.avg_nps)
    b_bracket = bracket_density * min(1.0, features.rhythm_irreg * 2.0) + shear_ratio * 0.30
    return Tech4DComponents(
        tortuosity=round(tortuosity, 4),
        bracket_shear=round(b_bracket, 4),
        spatial_entropy=round(features.spatial_entropy, 4),
        rhythm_irreg=round(features.rhythm_irreg, 4),
    )
