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

from proj7k.calibration import DEFAULT_CALIBRATION, StrainStarCalibration
from proj7k.features import BeatmapFeatures, extract_beatmap_features
from proj7k.parser import Beatmap7K, HitObject, NoteType
from proj7k.physics import (
    BRACKET_PHASE_INVERSION_WINDOW_MS,
    CHORDJACK_STEP_INTERVAL_MS,
    SPEED_BURST_EXPONENT,
    SPEED_BURST_INTERVAL_MS,
    SPEED_BURST_MIN_INTERVAL_MS,
    SPEED_BURST_REFERENCE_MS,
)
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
class RawTechniqueDrivers:
    """
    The 8 techniques' physical drivers after orthogonal cross-suppression, still in the raw
    units each technique operator produces.

    This is what the technique operators measure, before any of it is put on the star scale.
    Splitting it out gives the driver magnitudes an interface of their own: they can be
    measured, compared and calibrated without a star mapping in the way — which is what the
    8 dimensions' separability has to be judged on.

    The eight driver fields follow `TECHNIQUE_NAMES` and are unrounded. `tech_4d` rides along
    as the diagnostic breakdown of the Tech operator's own inputs: rounded for display, and
    not one of the eight drivers.
    """
    jack: float
    tech: float
    speed: float
    stream: float
    ln_general: float
    ln_tech: float
    ln_inverse: float
    ln_release: float
    tech_4d: Tech4DComponents

    def to_dict(self) -> Dict[str, float]:
        """The eight drivers keyed by technique name, unrounded — the star mapping consumes these."""
        return {name: getattr(self, name) for name in TECHNIQUE_NAMES}


@dataclass(frozen=True)
class RadarOptions:
    """
    Configuration options for technique radar calibration and suppression.

    Every number the radar's operators read is a field here. The operators used to carry their
    calibration as literals inside their own bodies, which meant a change to one of them moved
    star ratings without moving the engine version — stale ratings surviving in osu!lazer
    (ADR-0014), the exact failure the version token exists to prevent. Fields added for issue
    #48 are grouped by the operator that reads them.

    Two kinds of number are deliberately *not* fields, and are registered with a reason in the
    literal-coverage guard instead: algebraic identities and indices (the 1 in `1 + w * x`, the
    0 in a floor, `range(7)`'s lane count), and unit conversions (ms to s). Neither is a
    calibration decision, and neither can be retuned to a different value without the guard
    failing first.
    """
    min_rice_hold_threshold: float = 0.05
    jack_threshold_ms: float = CHORDJACK_STEP_INTERVAL_MS
    speed_burst_threshold_ms: float = SPEED_BURST_INTERVAL_MS
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
    ln_inv_score_weight: float = 3.50
    ln_inv_lock_weight: float = 1.30
    tech_coupling_gamma: float = 1.25
    tech_coupling_lambda: float = 2.85
    tech_tort_weight: float = 0.35
    tech_bracket_weight: float = 0.70
    tech_spatial_weight: float = 0.30
    tech_rhythm_weight: float = 1.20
    ln_tech_coupling_lambda: float = 3.30

    # --- Jack and stream (`_compute_jack_and_stream_raw`) ---
    #: The jack run-length saturation law W(L) = 1 + M_max * tanh((L - JACK_RUN_OFFSET) / tau),
    #: and the floor and exponent of its frequency strain (T_jack / max(FLOOR, dt)) ** EXP.
    jack_run_offset: float = 2.0
    jack_frequency_floor_ms: float = 35.0
    jack_frequency_exp: float = 1.25

    #: A flow reversal counts when three consecutive flow notes span at most this long.
    flow_reversal_window_ms: float = 250.0

    #: Step, in seconds, of the decay-accumulation grid the jack strain is sampled on.
    decay_grid_s: float = 0.25

    #: Quantile pooling of the sampled jack strain: 0.70 * P90 + 0.30 * top-5% mean.
    jack_p90_index: float = 0.90
    jack_top5_fraction: float = 0.05

    #: Lane-spread factor (active_lanes - FLOOR) / SPAN, clamped to [0, 1]: a chart spread over
    #: four or more lanes is read as freely flowing, one confined to the two outer-or-inner
    #: lanes is not.
    lane_spread_floor_lanes: float = 2.0
    lane_spread_span_lanes: float = 2.0

    #: Chord-jack synergy, applied to both jack drivers as c_syn = 1 + GAIN * max(0, ratio - THRESHOLD).
    jack_chord_syn_threshold: float = 0.08
    jack_chord_syn_gain: float = 3.0

    #: The two jack drivers: sustained duration rate and locally pooled burst strain.
    jack_duration_rate_gain: float = 1.48
    jack_burst_driver_gain: float = 2.0

    #: Stream's dominant-chordjack suppression, max(FLOOR, 1 - GAIN * max(0, ratio - THRESHOLD)),
    #: and the flow-rate gain it is applied to.
    stream_jack_supp_threshold: float = 0.11
    stream_jack_supp_gain: float = 3.6
    stream_jack_supp_floor: float = 0.05
    stream_flow_gain: float = 0.85

    #: Shortest chart duration the rate-based drivers divide by, so a near-empty chart cannot
    #: produce an arbitrarily large per-second rate.
    min_duration_s: float = 0.5

    # --- Speed (`_compute_speed_raw`) ---
    #: Scale of the micro-speed burst rate, which is the whole driver. A sustained-density term
    #: (`max(0, avg_nps - offset) * gain`) used to be added to it, and it is what made Speed a
    #: second copy of Stream: measured over the corpus, the burst rate per flow note is 0.61 on
    #: the Speed ladder against 0.12 on the Stream ladder, but the note rate behind it is the
    #: same on both — so the density term buried the one quantity that tells them apart.
    speed_rate_gain: float = 3.50

    # --- Rule C: kinetic-base reconciliation ---
    #: Below this raw jack the speed/stream corrections are skipped entirely.
    rule_speed_jack_gate: float = 3.0
    rule_speed_jack_ratio: float = 0.7
    rule_speed_jack_penalty: float = 0.6

    #: Stream noise is a jack's, not a stream's, when the jack rate is this high and this many
    #: jack notes sit in runs of at least `rule_stream_jack_min_run`.
    rule_stream_jack_gate: float = 3.0
    rule_stream_jack_ratio: float = 0.14
    rule_stream_jack_min_run: int = 2
    rule_stream_jack_min_count: int = 10
    rule_jack_stream_clamp: float = 0.82
    rule_jack_stream_penalty: float = 0.40

    #: Ceiling of the effective jack used for the kinetic base, as a multiple of stream.
    kinetic_jack_stream_cap: float = 1.25

    # --- Four-dimensional unorthodox permutation operator ---
    tech_tort_offset: float = 0.45
    tech_adj_shear_weight: float = 0.5
    tech_bracket_rhythm_scale: float = 2.0
    tech_shear_weight: float = 0.30
    tech_spatial_offset: float = 0.88
    tech_spatial_span: float = 0.12
    tech_rhythm_offset: float = 0.20

    # --- Kinetic technique coupling ---
    #: Saturation of the coupling multiplier: 1 + GAIN * tanh((raw - 1) / SCALE) above unity,
    #: LINEAR_GAIN * raw below it. GAIN is how far past the carrier the technique load may go on
    #: an extremely irregular chart — ADR-0008's "反超基础物理动能" — and at 0.15 it capped the
    #: overtake at 15%, which is inside the noise of everything else the chart is doing. At 0.8
    #: an unorthodox chart reaches ~1.8x its carrier while an ordinary one stays below it.
    tech_saturation_gain: float = 0.8
    tech_saturation_scale: float = 0.20
    tech_linear_gain: float = 0.90
    tech_jack_penalty: float = 0.40

    # --- LN flux ---
    #: hold_ratio * avg_nps * FLUX_GAIN, the LN volume base both LN General and LN Tech build on.
    ln_gen_flux_gain: float = 1.50
    ln_tech_flux_gain: float = 1.50
    ln_tech_freedom_offset: float = 0.8
    ln_tech_antiphase_gain: float = 0.05
    ln_tech_excess_exp: float = 1.15
    ln_tech_saturation_gain: float = 0.15
    ln_tech_saturation_scale: float = 0.20
    ln_tech_linear_gain: float = 0.90

    #: The two shapes of a genuine LN-tech chart: permutation-heavy with free fingers, or
    #: gap-1 dense. Either one lets the LN Tech driver inherit the kinetic base.
    ln_tech_chart_excess_gate: float = 0.55
    ln_tech_chart_freedom_gate: float = 0.75
    ln_tech_chart_lock_gate: float = 2.85
    ln_tech_chart_gap1_gate: float = 1.60
    ln_tech_chart_gap1_freedom_gate: float = 0.80

    # --- LN inverse and release ---
    #: Inverse load ((locked - CENTER) / SPAN) ** EXP.
    ln_inv_lock_center: float = 2.0
    ln_inv_lock_span: float = 2.0
    ln_inv_lock_exp: float = 3.0

    #: LN Release's 尾判难度+卡手 modifier on the General flux base (ADR-0008):
    #: `r_ln_gen * GAIN * (release_lock_depth / REF_DEPTH)`. `REF_DEPTH` is how tied up the hand
    #: typically is when a lift lands, so the modifier sits at 1.0 on an ordinary hold chart and
    #: the axis wins exactly where the chart's lifts are more constrained than that — measured on
    #: the frozen ladder, 13 of the Release ladder's 15 tiers clear the General ladder's own.
    ln_release_gain: float = 1.0
    ln_release_ref_depth: float = 0.5

    #: A chart is read as hold-dominant once its hold ratio passes this, and the LN dimensions
    #: are then scaled by min(1, hold_ratio * HOLD_PRESENCE_GAIN).
    hold_presence_gain: float = 2.0

    # --- Orthogonal cross-suppression ---
    #: Above this hold ratio the jack driver is faded out over the next `ln_decline_span`.
    ln_decline_start: float = 0.30
    ln_decline_span: float = 0.20

    #: Genuine jack dominance: the jack rate, the jack-note ratio, and the margins by which a
    #: dominant jack suppresses stream and tech. The suppression is a fractional factor that
    #: starts at 1.0 at the ratio threshold and decays with how far past it the chart sits, down
    #: to a floor — the same shape Rule D already uses for a jack's claim on stream noise. It
    #: used to be a subtraction of a share of the jack driver, which zeroes an axis outright
    #: whenever the jack is big enough: that is what flattened Tech to 0.00 on `EGOISM 440`
    #: (jack-note ratio 0.181, barely past the threshold) at the very tier where the technique
    #: axis is the point of the chart.
    jack_dominance_gate: float = 3.0
    jack_dominance_ratio: float = 0.15
    jack_stream_adv: float = 1.05
    jack_tech_adv: float = 1.5
    jack_dominance_gain: float = 6.0
    jack_dominance_floor: float = 0.05

    #: Charts confined to this many lanes or fewer are pure-jack: no stream, tech or speed.
    pure_lane_gate: int = 2

    #: Rule E: at this locked-finger count and hold ratio the chart is severely inverted, and
    #: LN Inverse displaces LN General over `inv_gen_adv` of its score.
    inv_lock_gate: float = 4.0
    inv_hold_gate: float = 0.85
    inv_gen_adv: float = 0.80
    inv_gen_penalty: float = 0.35

    # --- Star-scale projection ---
    #: Ceiling a single technique score is clamped to before p-norm aggregation. It is a
    #: defensive bound, not the star scale's ceiling: the scale's own ceiling is the soft cap's
    #: asymptote (`soft_cap_threshold + soft_cap_scale`), which is unreachable by construction.
    score_ceiling: float = 12.0


def _partition_chord_steps(
    beatmap: Beatmap7K, chord_eps_ms: float = RadarOptions().chord_eps_ms
) -> List[List[HitObject]]:
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

    duration_s = max(
        options.min_duration_s, (max(ho.time for ho in hos) - min(ho.time for ho in hos)) / 1000.0
    )
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

        # Bracket phase inversion detection between consecutive steps (CONTEXT.md 括号拓扑相变)
        dt_step = step_time - prev_step_time
        if 0.0 < dt_step < BRACKET_PHASE_INVERSION_WINDOW_MS:
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

                # Chordjack run-length saturation W(L) = 1.0 + M_max * tanh((L - offset) / tau)
                w_l = 1.0 + options.jack_m_max * math.tanh(
                    (new_run_length - options.jack_run_offset) / options.jack_tau
                )
                # Multi-key chord arm vibration load
                c_factor = 1.0 + options.jack_chord_boost * (c_size - 1)
                # Frequency strain
                s_factor = math.pow(
                    options.jack_threshold_ms / max(options.jack_frequency_floor_ms, dt),
                    options.jack_frequency_exp,
                )

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
                    if (t2 - t0) <= options.flow_reversal_window_ms:
                        d1 = c1 - c0
                        d2 = c2 - c1
                        if (d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0):
                            reversals += 1

        if step_impulse > 0.0:
            step_impulses.append((step_time / 1000.0, step_impulse))

    # Continuous strain decay accumulation S(t) = S(t - dt) * exp(-dt / tau) + dS (ADR-0008)
    dt_grid = options.decay_grid_s
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
    p90 = sorted_s[int(len(sorted_s) * options.jack_p90_index)]
    n_top5 = max(1, int(len(sorted_s) * options.jack_top5_fraction))
    top5_mean = sum(sorted_s[-n_top5:]) / n_top5
    pooled_jack = options.jack_quantile_p90_weight * p90 + options.jack_quantile_top5_weight * top5_mean

    total_notes = len(hos)
    active_lanes = len({ho.column for ho in hos})
    lane_spread = max(
        0.0,
        min(
            1.0,
            (active_lanes - options.lane_spread_floor_lanes) / options.lane_spread_span_lanes,
        ),
    )

    jack_ratio = jack_count / total_notes if total_notes else 0.0
    flow_count = total_notes - jack_count
    flow_nps = flow_count / duration_s

    tortuosity = reversals / max(1, flow_count)
    bracket_density = bracket_inversions / max(1, len(steps))
    t_stream = 1.0 + options.stream_tort_weight * tortuosity + options.stream_bracket_weight * bracket_density

    # Jack raw driver combining sustained duration rate and local pooled burst strain
    c_syn = 1.0 + options.jack_chord_syn_gain * max(
        0.0, jack_ratio - options.jack_chord_syn_threshold
    )
    duration_rate = (
        (sum(x[1] for x in step_impulses) / duration_s) * options.jack_duration_rate_gain * c_syn
    )
    burst_driver = pooled_jack * options.jack_burst_driver_gain * c_syn
    r_jack = max(duration_rate, burst_driver)

    # Stream raw driver with dominant chordjack suppression
    supp = max(
        options.stream_jack_supp_floor,
        1.0
        - options.stream_jack_supp_gain * max(0.0, jack_ratio - options.stream_jack_supp_threshold),
    )
    r_stream = flow_nps * t_stream * lane_spread * options.stream_flow_gain * supp

    return r_jack, r_stream, jack_ratio, max_run_length, jack_count, tortuosity, bracket_density


def _compute_jack_raw(
    beatmap: Beatmap7K,
    jack_threshold_ms: float = CHORDJACK_STEP_INTERVAL_MS,
) -> float:
    """Computes raw Jack intensity via _compute_jack_and_stream_raw."""
    r_jack, *_ = _compute_jack_and_stream_raw(beatmap, RadarOptions(jack_threshold_ms=jack_threshold_ms))
    return r_jack


def _compute_speed_raw(beatmap: Beatmap7K, options: RadarOptions) -> float:
    """
    Computes raw Speed intensity from rapid successive note presses across different columns.

    The driver is `physics`' micro-speed burst law — the same reference interval and exponent
    the strain side accumulates — summed over the chart and rated per second. It is the only
    term: the speed this axis is about is how fast the fastest presses come, not how many notes
    the chart holds, and mixing in a note-rate term is what left the axis measuring density.
    """
    hos = sorted(beatmap.hit_objects, key=lambda x: x.time)
    burst = 0.0
    for k in range(len(hos) - 1):
        if hos[k].column != hos[k + 1].column:
            dt_ms = hos[k + 1].time - hos[k].time
            if SPEED_BURST_MIN_INTERVAL_MS < dt_ms < options.speed_burst_threshold_ms:
                burst += math.pow(
                    (options.speed_burst_threshold_ms - dt_ms) / SPEED_BURST_REFERENCE_MS,
                    SPEED_BURST_EXPONENT,
                )

    duration_s = (
        max(
            options.min_duration_s,
            (max(ho.time for ho in hos) - min(ho.time for ho in hos)) / 1000.0,
        )
        if hos
        else 1.0
    )
    burst_rate = burst / duration_s
    return burst_rate * options.speed_rate_gain


def compute_raw_technique_drivers(
    beatmap: Beatmap7K,
    features: Optional[BeatmapFeatures] = None,
    options: Optional[RadarOptions] = None,
) -> RawTechniqueDrivers:
    """
    Computes the 8 techniques' raw physical drivers with orthogonal cross-suppression applied
    (Rule A rice/LN gating, Rule C lane-spread and jack dominance, Rule D stream noise, Rule E
    inverse specialization).

    These are the quantities the technique operators actually measure, in their own units. The
    star mapping that turns a driver into a *score* is a separate step, applied by
    `compute_technique_radar`; nothing here has been put on the star scale yet.
    """
    if options is None:
        options = RadarOptions()

    if not beatmap.hit_objects:
        return RawTechniqueDrivers(
            jack=0.0,
            tech=0.0,
            speed=0.0,
            stream=0.0,
            ln_general=0.0,
            ln_tech=0.0,
            ln_inverse=0.0,
            ln_release=0.0,
            tech_4d=Tech4DComponents(0.0, 0.0, 0.0, 0.0),
        )

    if features is None:
        features = extract_beatmap_features(beatmap)

    hos = beatmap.hit_objects
    hold_ratio = features.hold_pct / 100.0

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
    r_speed = _compute_speed_raw(beatmap, options)

    active_lanes = len({ho.column for ho in hos})

    # Rule C on speed and jack before Rule D. Same precondition as the suppression further down:
    # a jack has to be what the chart is made of before it is allowed to eat the speed axis.
    if r_jack > options.rule_speed_jack_gate and jack_ratio >= options.jack_dominance_ratio:
        if r_jack > r_speed * options.rule_speed_jack_ratio:
            r_speed = max(0.0, r_speed - (r_jack * options.rule_speed_jack_penalty))

    # Effective Jack for kinetic base (reflecting Rule D soft-cap on stream noise)
    if r_stream > options.rule_stream_jack_gate and jack_ratio < options.rule_stream_jack_ratio:
        if max_run >= options.rule_stream_jack_min_run and jack_count >= options.rule_stream_jack_min_count:
            eff_jack_base = min(r_jack, r_stream * options.rule_jack_stream_clamp)
            r_jack = min(r_jack, r_stream * options.rule_jack_stream_clamp)
        else:
            eff_jack_base = max(0.0, r_jack - (r_stream * options.rule_jack_stream_penalty))
            r_jack = max(0.0, r_jack - (r_stream * options.rule_jack_stream_penalty))
    else:
        eff_jack_base = r_jack

    # Kinetic base energy K_base = max(r_stream, min(eff_jack, r_stream * cap)) (ADR-0008, revised
    # by #47). Raw speed used to be the third term of this max, which made every speed chart a
    # tech chart carrying a speed-shaped carrier: tech is `k_base * multiplier`, and on a chart
    # whose burst is what makes it hard, `k_base` *is* `r_speed`, so tech came out at 1.07-1.15x
    # speed and won the Speed ladder on margins of 7-15%. A chart whose load is burst speed is
    # read by players as speed, not as a tech chart wearing a speed carrier — tech takes over
    # only where the arrangement itself is what is irregular (see ADR-0008's revision note).
    k_base = max(r_stream, min(eff_jack_base, r_stream * options.kinetic_jack_stream_cap))

    # Four-dimensional Unorthodox Permutation Operator Omega_irreg (ADR-0008)
    # 1. Flow tortuosity (reversals)
    t_tort = max(0.0, tortuosity - options.tech_tort_offset)
    # 2. Bracket and shear
    shear_ratio = (
        features.gap1_density + options.tech_adj_shear_weight * features.adj_density
    ) / max(1.0, features.avg_nps)
    b_bracket = bracket_density * min(
        1.0, features.rhythm_irreg * options.tech_bracket_rhythm_scale
    ) + shear_ratio * options.tech_shear_weight
    # 3. Spatial transition entropy
    s_spatial = (
        max(0.0, features.spatial_entropy - options.tech_spatial_offset) / options.tech_spatial_span
    )
    # 4. Rhythmic irregularity
    r_rhythm = max(0.0, features.rhythm_irreg - options.tech_rhythm_offset)

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
        mult = 1.0 + options.tech_saturation_gain * math.tanh(
            (raw_mult - 1.0) / options.tech_saturation_scale
        )
        r_tech = k_base * mult
        r_jack = max(0.0, r_jack - (r_tech * options.tech_jack_penalty))
    else:
        r_tech = k_base * raw_mult * options.tech_linear_gain

    # LN General (overall hold presence, sustained hold chords, and concurrent spatial flux) (ADR-0008)
    concurrent_factor = 1.0 + options.ln_gen_concurrent_weight * features.mean_locked_fingers
    r_ln_gen = hold_ratio * features.avg_nps * concurrent_factor * options.ln_gen_flux_gain

    # LN Tech (kinetic coupling with LN flux and unorthodox permutation) (ADR-0008)
    base_ln_flux = hold_ratio * features.avg_nps * options.ln_tech_flux_gain
    finger_freedom = (features.gap1_density + options.ln_tech_freedom_offset) / max(
        1.0, features.mean_locked_fingers
    )
    antiphase_boost = 1.0 + options.ln_tech_antiphase_gain * features.antiphase_rate
    raw_ln_mult = (
        math.pow(tech_excess, options.ln_tech_excess_exp)
        * options.ln_tech_coupling_lambda
        * finger_freedom
        * antiphase_boost
    )
    is_ln_tech_chart = (
        (
            tech_excess >= options.ln_tech_chart_excess_gate
            and finger_freedom >= options.ln_tech_chart_freedom_gate
            and features.mean_locked_fingers < options.ln_tech_chart_lock_gate
        )
        or (
            features.gap1_density >= options.ln_tech_chart_gap1_gate
            and finger_freedom >= options.ln_tech_chart_gap1_freedom_gate
        )
    )
    if raw_ln_mult > 1.0 and hold_ratio >= options.min_rice_hold_threshold:
        ln_mult = 1.0 + options.ln_tech_saturation_gain * math.tanh(
            (raw_ln_mult - 1.0) / options.ln_tech_saturation_scale
        )
        r_ln_tech = max(base_ln_flux, r_ln_gen) * ln_mult if is_ln_tech_chart else (base_ln_flux * ln_mult)
    else:
        r_ln_tech = (
            (base_ln_flux * raw_ln_mult * options.ln_tech_linear_gain)
            if hold_ratio >= options.min_rice_hold_threshold
            else 0.0
        )

    # LN Inverse (high locked finger density, inverse score under micro-action scaling) (ADR-0006, ADR-0008)
    lock_load = math.pow(
        max(0.0, features.mean_locked_fingers - options.ln_inv_lock_center) / options.ln_inv_lock_span,
        options.ln_inv_lock_exp,
    )
    r_ln_inv = (
        features.inverse_score * options.ln_inv_score_weight
        + features.avg_nps * hold_ratio * lock_load * options.ln_inv_lock_weight
    ) * min(1.0, hold_ratio * options.hold_presence_gain)

    # LN Release (ADR-0008): the lift-precision load, as a modifier on the General flux base —
    # "仅在纯跳音释放谱面中超越 General", which is the whole point of the axis. What the modifier
    # reads is how many of the chart's lifts are *unaccompanied*: a tail with another key pressed
    # at the same tick rides along with a motion the hand is already making, while an isolated
    # tail has to be placed on its own. Over half the lifts unaccompanied is what makes a chart
    # release-dominant, so the reference is the majority point rather than a fitted number.
    #
    # The operator this replaces summed release rate, antiphase rate and a peak term. Neither of
    # the first two is lift precision — antiphase counts lifts that *coincide* with a press, i.e.
    # the anchored ones — and an addend of that shape cannot exceed the base it is meant to
    # overtake: 0/15 of its own ladder, at ρ 0.987 with LN General.
    r_ln_rel = (
        r_ln_gen
        * options.ln_release_gain
        * (features.release_lock_depth / options.ln_release_ref_depth)
    )

    # --- 2. Orthogonal Cross-Suppression ---
    # Rule A: Pure Rice charts (hold_ratio < min_rice_hold_threshold)
    if hold_ratio < options.min_rice_hold_threshold:
        r_ln_gen = 0.0
        r_ln_tech = 0.0
        r_ln_inv = 0.0
        r_ln_rel = 0.0
    elif hold_ratio > options.ln_decline_start:
        r_jack *= max(
            0.0,
            1.0 - (hold_ratio - options.ln_decline_start) / options.ln_decline_span,
        )

    # Rule C: Lane spread gating and Pure Jack specialization
    if active_lanes <= options.pure_lane_gate:
        r_stream = 0.0
        r_tech = 0.0
        r_speed = 0.0
    else:
        # Rule C: Genuine Jack dominance suppresses competing stream/tech dimensions. Both
        # branches need the jack-ratio precondition as well as the magnitude comparison: the
        # ratio is what says the chart is *built* out of stagnation, and without it a chart
        # merely carrying one dense jack run has its other axes suppressed.
        if r_jack > options.jack_dominance_gate and jack_ratio >= options.jack_dominance_ratio:
            jack_supp = max(
                options.jack_dominance_floor,
                1.0 - options.jack_dominance_gain * (jack_ratio - options.jack_dominance_ratio),
            )
            if r_jack > r_stream * options.jack_stream_adv:
                r_stream *= jack_supp
            if r_jack > r_tech * options.jack_tech_adv:
                r_tech *= jack_supp

    # Rule E: Inverse specialization gating (ADR-0008)
    # When a chart enters the severe inverted state, the motor-cognitive burden is dominated by
    # Inverse rather than General hold volume.
    if features.mean_locked_fingers >= options.inv_lock_gate and hold_ratio >= options.inv_hold_gate:
        if r_ln_inv > r_ln_gen * options.inv_gen_adv:
            r_ln_gen = max(0.0, r_ln_gen - (r_ln_inv * options.inv_gen_penalty))
            r_ln_tech = max(0.0, r_ln_tech - (r_ln_inv * options.inv_gen_penalty))

    return RawTechniqueDrivers(
        jack=max(0.0, r_jack),
        tech=max(0.0, r_tech),
        speed=max(0.0, r_speed),
        stream=max(0.0, r_stream),
        ln_general=max(0.0, r_ln_gen),
        ln_tech=max(0.0, r_ln_tech),
        ln_inverse=max(0.0, r_ln_inv),
        ln_release=max(0.0, r_ln_rel),
        tech_4d=tech_4d,
    )


def compute_technique_radar(
    beatmap: Beatmap7K,
    features: Optional[BeatmapFeatures] = None,
    strain_profile: Optional[StrainTimeseriesProfile] = None,
    options: Optional[RadarOptions] = None,
    calibration: Optional[StrainStarCalibration] = None,
) -> TechniqueRadar:
    """
    Computes calibrated 8-dimension technique radar scores with cross-suppression.

    `calibration` carries the star-scale constants (anchor law + driver back-pressure
    exponent) that every technique score is expressed in. Callers pass the calibration of
    the options object driving the evaluation (see `rating.RatingOptions.calibration`) so
    that the radar vector and the synthesized star rating can never drift apart; when
    omitted, the canonical `calibration.DEFAULT_CALIBRATION` is used.
    """
    if options is None:
        options = RadarOptions()
    if calibration is None:
        calibration = DEFAULT_CALIBRATION

    if not beatmap.hit_objects:
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

    if strain_profile is None:
        strain_profile = compute_dual_hand_strain(beatmap)

    drivers = compute_raw_technique_drivers(beatmap, features=features, options=options)
    sr_base = calibration.star_rating_from_strain(strain_profile.p90_strain)

    raw_scores = drivers.to_dict()
    max_raw = max(raw_scores.values()) if raw_scores else 0.0
    if max_raw <= 1e-6 or sr_base <= 1e-6:
        scores = {k: 0.0 for k in raw_scores}
    else:
        scores = {
            k: min(
                options.score_ceiling,
                sr_base * math.pow(v / max_raw, calibration.driver_backpressure_exp),
            )
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
        tech_4d=drivers.tech_4d,
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
    # The same three weights the technique operator applies — read from the options rather than
    # restated, so the exposed breakdown cannot drift from the driver it breaks down.
    shear_ratio = (
        features.gap1_density + opts.tech_adj_shear_weight * features.adj_density
    ) / max(1.0, features.avg_nps)
    b_bracket = bracket_density * min(
        1.0, features.rhythm_irreg * opts.tech_bracket_rhythm_scale
    ) + shear_ratio * opts.tech_shear_weight
    return Tech4DComponents(
        tortuosity=round(tortuosity, 4),
        bracket_shear=round(b_bracket, 4),
        spatial_entropy=round(features.spatial_entropy, 4),
        rhythm_irreg=round(features.rhythm_irreg, 4),
    )
