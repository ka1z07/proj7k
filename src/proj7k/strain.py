"""
Dual-Hand Slicing Strain Accumulator & Modulation Engine for om7k.

Decouples 7-column layout into left hand (L3, L2, L1 + 50% S) and
right hand (R1, R2, R3 + 50% S), each maintaining an independent
strain accumulator with physiological exponential decay (tau = 1.2s),
micro-speed burst strain, and judgment-window-buffered cognitive modulation.
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math
from typing import Any, Dict, List, Optional, Tuple

from proj7k.calibration import DEFAULT_CALIBRATION, StrainStarCalibration
from proj7k.parser import Beatmap7K, NoteType, dominant_bpm
from proj7k.physics import (
    ANTIPHASE_ONSET_WINDOW_S,
    JACK_INTERVAL_PENALTY_MS,
    SPEED_BURST_INTERVAL_MS,
)

# 7K Symmetric Topological Track Layout:
# L3 (0), L2 (1), L1 (2) | S (3) | R1 (4), R2 (5), R3 (6)
LEFT_HAND_LANES: Tuple[int, int, int] = (0, 1, 2)
CENTER_SHARED_LANE: int = 3
RIGHT_HAND_LANES: Tuple[int, int, int] = (4, 5, 6)


@dataclass(frozen=True)
class StrainTimeseriesProfile:
    """Time-series strain profile and steady-state quantile pooling."""
    step_seconds: float
    times: List[float]
    left_hand_strain: List[float]
    right_hand_strain: List[float]
    combined_strain: List[float]
    p90_strain: float
    p95_strain: float
    peak_strain: float

    @property
    def sample_times_ms(self) -> List[float]:
        return [round(t * 1000.0, 2) for t in self.times]

    @property
    def left_strains(self) -> List[float]:
        return self.left_hand_strain

    @property
    def right_strains(self) -> List[float]:
        return self.right_hand_strain

    @property
    def p90(self) -> float:
        return self.p90_strain

    @property
    def p95(self) -> float:
        return self.p95_strain

    @property
    def top5_percent_strain(self) -> float:
        return self.p95_strain

    @property
    def top5_percent(self) -> float:
        return self.p95_strain

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_seconds": self.step_seconds,
            "times": self.times,
            "sample_times_ms": self.sample_times_ms,
            "left_strains": self.left_strains,
            "right_strains": self.right_strains,
            "left_hand_strain": self.left_hand_strain,
            "right_hand_strain": self.right_hand_strain,
            "combined_strain": self.combined_strain,
            "p90_strain": self.p90_strain,
            "p90": self.p90,
            "p95_strain": self.p95_strain,
            "p95": self.p95,
            "top5_percent_strain": self.top5_percent_strain,
            "top5_percent": self.top5_percent,
            "peak_strain": self.peak_strain,
        }


@dataclass(frozen=True)
class StrainOptions:
    """
    Configuration options for dual-hand strain accumulation and modulation.

    `bpm` is an explicit tempo override; when unset the chart's dominant BPM is read from
    `parser.dominant_bpm` (the single BPM source).
    """
    tau_time_constant_s: float = 1.2
    tau_half_life_s: float = 1.2  # Alias for backward compatibility
    window_s: float = 0.5
    step_s: float = 0.25
    jack_threshold_ms: float = JACK_INTERVAL_PENALTY_MS
    jack_weight: float = 0.18
    gap1_weight: float = 0.20
    gap1_threshold_s: float = 0.015
    speed_burst_threshold_ms: float = SPEED_BURST_INTERVAL_MS
    speed_burst_weight: float = 0.30
    w_judg_ms: float = 38.0
    alpha: float = 0.60
    gamma: float = 1.20
    bpm: Optional[float] = None


def _get_notes_in_window(sorted_times: List[float], start_s: float, end_s: float) -> List[float]:
    """Extracts notes within [start_s, end_s) using binary search."""
    i_left = bisect_left(sorted_times, start_s)
    i_right = bisect_left(sorted_times, end_s)
    return sorted_times[i_left:i_right]


def _build_locked_finger_counts(
    times_s: List[float],
    col_lns: Dict[int, List[Tuple[float, float]]],
    columns: Tuple[int, ...],
) -> List[int]:
    """
    Precomputes the locked-finger step function L(t) over the sampling grid.

    L(t) counts how many of `columns` are held at t, and it is a step function: it only
    changes at hold-interval boundaries. Building it once with a per-lane difference array
    costs O(intervals * log samples + samples) instead of rescanning every lane's interval
    list at every sample, and it produces exactly the same integer sequence — each interval
    contributes to precisely the samples k with st <= times_s[k] <= et, resolved by binary
    search against the very grid values the accumulator samples, so no float rounding is
    introduced.
    """
    counts = [0] * len(times_s)
    if not times_s:
        return counts

    for col in columns:
        intervals = col_lns.get(col)
        if not intervals:
            continue

        diff = [0] * (len(times_s) + 1)
        touched = False
        for st, et in intervals:
            i_start = bisect_left(times_s, st)
            i_end = bisect_right(times_s, et)
            if i_start < i_end:
                diff[i_start] += 1
                diff[i_end] -= 1
                touched = True

        if not touched:
            continue

        # A finger is locked once, however many of its own intervals overlap here.
        active = 0
        for k in range(len(times_s)):
            active += diff[k]
            if active > 0:
                counts[k] += 1

    return counts


def compute_judgment_overlap_buffer(bpm: float, w_judg_ms: float = 38.0) -> float:
    """
    Computes judgment window overlap ratio (eta):
    eta = min(0.75, W_judg / delta_t_action)
    where delta_t_action is 16th note striking window at given BPM.
    """
    delta_t_ms = 60000.0 / (bpm * 4.0) if bpm > 0 else 100.0
    return min(0.75, w_judg_ms / max(10.0, delta_t_ms))


def compute_high_speed_scaling_factor(bpm: float, eta: float) -> float:
    """
    Computes high-speed LN scaling factor with judgment buffer dampening:
    - BPM <= 145: (BPM / 145)^1.8 * 0.75 (low speed cognitive illusion cap)
    - 145 < BPM < 180: linear transition
    - BPM >= 180: sub-linear saturation damped by judgment buffer (1 - 0.45 * eta)
    """
    if bpm <= 145.0:
        return ((bpm / 145.0) ** 1.8) * 0.75
    elif bpm < 180.0:
        return 0.75 + 0.25 * ((bpm - 145.0) / 35.0)
    else:
        buffer_factor = 1.0 - 0.45 * eta
        return 1.0 + 0.65 * ((bpm - 180.0) / 40.0) * buffer_factor


def compute_micro_speed_burst(
    sorted_hit_times: List[float],
    min_interval_ms: float = 5.0,
    max_interval_ms: float = SPEED_BURST_INTERVAL_MS,
) -> float:
    """
    Computes non-linear micro-speed burst strain:
    Sum of ((110ms - dt) / 50ms)^1.35 for dt < 110ms.
    """
    speed_burst = 0.0
    if len(sorted_hit_times) >= 2:
        for k in range(len(sorted_hit_times) - 1):
            dt_ms = (sorted_hit_times[k + 1] - sorted_hit_times[k]) * 1000.0
            if min_interval_ms < dt_ms < max_interval_ms:
                speed_burst += math.pow((max_interval_ms - dt_ms) / 50.0, 1.35)
    return speed_burst


def _compute_hand_load(
    outer_hits: List[float],
    middle_hits: List[float],
    inner_hits: List[float],
    shared_hits: List[float],
    hand_lanes: Tuple[int, int, int],
    locked_fingers: int,
    w_start: float,
    w_end: float,
    col_releases: Dict[int, List[float]],
    scaling_factor: float,
    options: StrainOptions,
) -> float:
    """
    Computes modulated momentary load for a single hand:
    D_hand = L_phys * (1.0 + alpha * L_cog)^gamma
    """
    notes_count = len(outer_hits) + len(middle_hits) + len(inner_hits) + 0.5 * len(shared_hits)
    if notes_count == 0.0:
        return 0.0

    nps = notes_count / options.window_s

    # Micro-speed burst strain across all hand notes
    all_hand_hits = sorted(outer_hits + middle_hits + inner_hits)
    speed_burst = compute_micro_speed_burst(
        all_hand_hits,
        min_interval_ms=5.0,
        max_interval_ms=options.speed_burst_threshold_ms,
    )
    speed_multiplier = 1.0 + options.speed_burst_weight * (
        speed_burst / max(1.0, float(len(all_hand_hits)))
    )

    # Jack penalty on primary hand lanes
    jack_penalty = 0.0
    jack_thresh = options.jack_threshold_ms
    for col_hits in (outer_hits, middle_hits, inner_hits):
        if len(col_hits) >= 2:
            for k in range(len(col_hits) - 1):
                dt_ms = (col_hits[k + 1] - col_hits[k]) * 1000.0
                if dt_ms < jack_thresh:
                    jack_penalty += (jack_thresh - dt_ms) / jack_thresh

    # Shared center lane jacks (50% load allocation)
    if len(shared_hits) >= 2:
        for k in range(len(shared_hits) - 1):
            dt_ms = (shared_hits[k + 1] - shared_hits[k]) * 1000.0
            if dt_ms < jack_thresh:
                jack_penalty += 0.5 * ((jack_thresh - dt_ms) / jack_thresh)

    # Gap1 penalty: simultaneous outer & inner hits with middle finger unpressed ([gap:1] 抠空中指)
    gap1_penalty = 0
    gap1_thresh = options.gap1_threshold_s
    for t_out in outer_hits:
        for t_in in inner_hits:
            if abs(t_out - t_in) < gap1_thresh:
                is_middle_pressed = any(abs(t_mid - t_out) < gap1_thresh for t_mid in middle_hits)
                if not is_middle_pressed:
                    gap1_penalty += 1

    l_phys = nps * speed_multiplier * (
        1.0 + options.jack_weight * jack_penalty + options.gap1_weight * gap1_penalty
    )

    # Cognitive Impedance (L_cog)
    # 1. Locked fingers at time t (precomputed step function, see _build_locked_finger_counts)
    # 2. Antiphase articulation in current window
    antiphase = 0
    for c1 in hand_lanes:
        for c2 in hand_lanes:
            if c1 != c2:
                for rl in col_releases[c2]:
                    if w_start <= rl < w_end:
                        col_hits_c1 = outer_hits if c1 == hand_lanes[0] else (
                            middle_hits if c1 == hand_lanes[1] else inner_hits
                        )
                        for pr in col_hits_c1:
                            if abs(pr - rl) < ANTIPHASE_ONSET_WINDOW_S:
                                antiphase += 1

    l_cog = 0.35 * (locked_fingers / 3.0) * scaling_factor + 0.15 * antiphase

    return l_phys * math.pow(1.0 + options.alpha * l_cog, options.gamma)


def _calculate_percentile(values: List[float], q: float) -> float:
    """Calculates q-th percentile using linear interpolation."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = (len(sorted_v) - 1) * (q / 100.0)
    low = int(math.floor(idx))
    high = int(math.ceil(idx))
    if low == high:
        return float(sorted_v[low])
    weight = idx - low
    return float((1.0 - weight) * sorted_v[low] + weight * sorted_v[high])


def compute_raw_strain_star_rating(
    s_base: float,
    a: float = DEFAULT_CALIBRATION.strain_a,
    b: float = DEFAULT_CALIBRATION.strain_b,
    exp: float = DEFAULT_CALIBRATION.strain_exp,
) -> float:
    """
    Maps physical steady-state strain S_base to raw star rating:
    SR_raw = a * S_base^exp + b

    Defaults come from the canonical calibration; the law itself lives in
    `calibration.StrainStarCalibration.star_rating_from_strain`.
    """
    return StrainStarCalibration(a, b, exp).star_rating_from_strain(s_base)


def compute_dual_hand_strain(
    beatmap: Beatmap7K,
    options: Optional[StrainOptions] = None,
) -> StrainTimeseriesProfile:
    """
    Computes instantaneous combined strain time-series using dual-hand
    decoupled strain accumulators with physiological decay and cognitive modulation.
    """
    if options is None:
        options = StrainOptions()

    hit_objects = beatmap.hit_objects
    if not hit_objects:
        return StrainTimeseriesProfile(
            step_seconds=options.step_s,
            times=[0.0],
            left_hand_strain=[0.0],
            right_hand_strain=[0.0],
            combined_strain=[0.0],
            p90_strain=0.0,
            p95_strain=0.0,
            peak_strain=0.0,
        )

    # Effective BPM: explicit override, else the chart's single dominant tempo
    if options.bpm is not None and options.bpm > 0:
        bpm = options.bpm
    else:
        bpm = dominant_bpm(beatmap)

    eta = compute_judgment_overlap_buffer(bpm, options.w_judg_ms)
    scaling_factor = compute_high_speed_scaling_factor(bpm, eta)

    # Sort note timestamps by column in seconds
    col_notes: Dict[int, List[float]] = {c: [] for c in range(7)}
    for ho in hit_objects:
        col_notes[ho.column].append(ho.time / 1000.0)

    for c in range(7):
        col_notes[c].sort()

    # Active LN intervals per column
    col_lns: Dict[int, List[Tuple[float, float]]] = {c: [] for c in range(7)}
    for ho in hit_objects:
        if ho.note_type == NoteType.LN and ho.end_time:
            col_lns[ho.column].append((ho.time / 1000.0, ho.end_time / 1000.0))

    # Release events per column
    col_releases: Dict[int, List[float]] = {c: [] for c in range(7)}
    for ho in hit_objects:
        if ho.note_type == NoteType.LN and ho.end_time:
            col_releases[ho.column].append(ho.end_time / 1000.0)

    start_ms = min(ho.time for ho in hit_objects)
    end_ms = max(
        (ho.end_time if ho.note_type == NoteType.LN and ho.end_time else ho.time)
        for ho in hit_objects
    )

    start_s = start_ms / 1000.0
    end_s = max(end_ms / 1000.0, start_s + 0.5)

    step_s = options.step_s
    num_steps = max(1, int(math.ceil((end_s - start_s) / step_s)) + 1)
    times_s = [round(start_s + k * step_s, 5) for k in range(num_steps)]

    tau = options.tau_time_constant_s
    decay = math.exp(-step_s / tau)

    left_strain = 0.0
    right_strain = 0.0

    left_strains: List[float] = []
    right_strains: List[float] = []
    combined_strains: List[float] = []

    half_window = options.window_s / 2.0

    # Locked-finger step function per hand, precomputed once over the whole grid
    left_locked = _build_locked_finger_counts(times_s, col_lns, LEFT_HAND_LANES)
    right_locked = _build_locked_finger_counts(times_s, col_lns, RIGHT_HAND_LANES)

    for step_idx, t in enumerate(times_s):
        w_start = t - half_window
        w_end = t + half_window

        # Extract window notes for all 7 columns
        w_notes = [
            _get_notes_in_window(col_notes[c], w_start, w_end)
            for c in range(7)
        ]

        # Left Hand (L3:0, L2:1, L1:2) + Shared (S:3)
        d_left = _compute_hand_load(
            outer_hits=w_notes[0],
            middle_hits=w_notes[1],
            inner_hits=w_notes[2],
            shared_hits=w_notes[3],
            hand_lanes=LEFT_HAND_LANES,
            locked_fingers=left_locked[step_idx],
            w_start=w_start,
            w_end=w_end,
            col_releases=col_releases,
            scaling_factor=scaling_factor,
            options=options,
        )

        # Right Hand (R3:6, R2:5, R1:4) + Shared (S:3)
        d_right = _compute_hand_load(
            outer_hits=w_notes[6],
            middle_hits=w_notes[5],
            inner_hits=w_notes[4],
            shared_hits=w_notes[3],
            hand_lanes=RIGHT_HAND_LANES,
            locked_fingers=right_locked[step_idx],
            w_start=w_start,
            w_end=w_end,
            col_releases=col_releases,
            scaling_factor=scaling_factor,
            options=options,
        )

        # Decay and update accumulators
        left_strain = left_strain * decay + d_left
        right_strain = right_strain * decay + d_right

        # Space-parallel L2 norm: S(t) = sqrt(S_L^2 + S_R^2)
        combined_strain = math.sqrt(left_strain * left_strain + right_strain * right_strain)

        left_strains.append(left_strain)
        right_strains.append(right_strain)
        combined_strains.append(combined_strain)

    p90 = _calculate_percentile(combined_strains, 90.0)
    p95 = _calculate_percentile(combined_strains, 95.0)
    peak = max(combined_strains) if combined_strains else 0.0

    return StrainTimeseriesProfile(
        step_seconds=step_s,
        times=times_s,
        left_hand_strain=left_strains,
        right_hand_strain=right_strains,
        combined_strain=combined_strains,
        p90_strain=p90,
        p95_strain=p95,
        peak_strain=peak,
    )


@dataclass(frozen=True)
class TechniqueStrainTimeseries:
    """
    Continuous 8-dimensional instantaneous technique strain curves S_d(t) (ADR-0012).
    """
    step_seconds: float
    times: List[float]
    jack: List[float]
    tech: List[float]
    speed: List[float]
    stream: List[float]
    ln_general: List[float]
    ln_tech: List[float]
    ln_inverse: List[float]
    ln_release: List[float]

    def get_strains_at(self, time_s: float) -> Dict[str, float]:
        """Interpolates instantaneous 8-dimension strain values at time_s (in seconds)."""
        dims = ["jack", "tech", "speed", "stream", "ln_general", "ln_tech", "ln_inverse", "ln_release"]
        if not self.times:
            return {tech: 0.0 for tech in dims}

        if time_s <= self.times[0]:
            return {d: float(getattr(self, d)[0]) for d in dims}
        if time_s >= self.times[-1]:
            return {d: float(getattr(self, d)[-1]) for d in dims}

        rel = (time_s - self.times[0]) / self.step_seconds
        idx = int(math.floor(rel))
        idx = max(0, min(len(self.times) - 2, idx))
        w = rel - idx

        res: Dict[str, float] = {}
        for d in dims:
            curve = getattr(self, d)
            val = (1.0 - w) * curve[idx] + w * curve[idx + 1]
            res[d] = float(max(0.0, val))
        return res

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_seconds": self.step_seconds,
            "times": self.times,
            "jack": [round(v, 3) for v in self.jack],
            "tech": [round(v, 3) for v in self.tech],
            "speed": [round(v, 3) for v in self.speed],
            "stream": [round(v, 3) for v in self.stream],
            "ln_general": [round(v, 3) for v in self.ln_general],
            "ln_tech": [round(v, 3) for v in self.ln_tech],
            "ln_inverse": [round(v, 3) for v in self.ln_inverse],
            "ln_release": [round(v, 3) for v in self.ln_release],
        }


def compute_8d_strain_timeseries(
    beatmap: Beatmap7K,
    options: Optional[StrainOptions] = None,
    radar: Optional[Any] = None,
) -> TechniqueStrainTimeseries:
    """
    Computes continuous 8-dimensional instantaneous strain curves S_d(t) across all 8
    technique dimensions (Jack, Tech, Speed, Stream, LN General, LN Tech, LN Inverse, LN Release)
    calibrated against the physical strain scale and Canonical Dan Progression Hierarchy (ADR-0012).
    """
    from proj7k.radar import TECHNIQUE_NAMES, compute_technique_radar
    from proj7k.downscaler.mapper import star_rating_to_strain

    opts = options or StrainOptions()
    hit_objects = sorted(beatmap.hit_objects, key=lambda x: (x.time, x.column))
    dims = ["jack", "tech", "speed", "stream", "ln_general", "ln_tech", "ln_inverse", "ln_release"]

    if not hit_objects:
        return TechniqueStrainTimeseries(
            step_seconds=opts.step_s,
            times=[0.0],
            jack=[0.0],
            tech=[0.0],
            speed=[0.0],
            stream=[0.0],
            ln_general=[0.0],
            ln_tech=[0.0],
            ln_inverse=[0.0],
            ln_release=[0.0],
        )

    if radar is None:
        radar = compute_technique_radar(beatmap)

    # Time grid matching dual-hand strain profile
    start_s = min(ho.time for ho in hit_objects) / 1000.0
    end_s = max(
        (ho.end_time if ho.note_type == NoteType.LN and ho.end_time else ho.time)
        for ho in hit_objects
    ) / 1000.0
    end_s = max(end_s, start_s + 0.5)

    step_s = opts.step_s
    num_steps = max(1, int(math.ceil((end_s - start_s) / step_s)) + 1)
    times_s = [round(start_s + k * step_s, 5) for k in range(num_steps)]

    # 1. Jack impulse collection
    col_state: Dict[int, Tuple[float, int]] = {c: (-1e9, 1) for c in range(7)}
    jack_impulses: List[Tuple[float, float]] = []
    for ho in hit_objects:
        c = ho.column
        t_ms = ho.time
        last_t, run_len = col_state[c]
        dt = t_ms - last_t
        if dt <= opts.jack_threshold_ms:
            new_run = run_len + 1
            w_l = 1.0 + 1.5 * math.tanh((new_run - 2) / 3.0)
            s_f = math.pow(opts.jack_threshold_ms / max(35.0, dt), 1.25)
            imp = s_f * w_l
            jack_impulses.append((t_ms / 1000.0, imp))
            col_state[c] = (t_ms, new_run)
        else:
            col_state[c] = (t_ms, 1)

    # 2. Speed impulse collection
    speed_impulses: List[Tuple[float, float]] = []
    for i in range(len(hit_objects) - 1):
        h1 = hit_objects[i]
        h2 = hit_objects[i + 1]
        if h1.column != h2.column:
            dt = h2.time - h1.time
            if 5.0 < dt < opts.speed_burst_threshold_ms:
                imp = math.pow((opts.speed_burst_threshold_ms - dt) / 50.0, 1.35)
                speed_impulses.append((h2.time / 1000.0, imp))

    # 3. Stream impulse collection (flow notes across active lanes)
    stream_impulses: List[Tuple[float, float]] = []
    for i in range(len(hit_objects) - 1):
        h1 = hit_objects[i]
        h2 = hit_objects[i + 1]
        if h1.column != h2.column:
            dt = h2.time - h1.time
            if 50.0 < dt < 250.0:
                stream_impulses.append((h2.time / 1000.0, 1.0))

    # 4. LN intervals and releases
    col_lns: Dict[int, List[Tuple[float, float]]] = {c: [] for c in range(7)}
    col_releases: List[Tuple[float, int]] = []
    for ho in hit_objects:
        if ho.note_type == NoteType.LN and ho.end_time:
            st = ho.time / 1000.0
            et = ho.end_time / 1000.0
            col_lns[ho.column].append((st, et))
            col_releases.append((et, ho.column))

    col_releases.sort(key=lambda x: x[0])

    # Decay constants
    tau_s = opts.tau_time_constant_s
    decay = math.exp(-step_s / tau_s)

    raw_curves: Dict[str, List[float]] = {d: [] for d in dims}

    jack_acc = 0.0
    speed_acc = 0.0
    stream_acc = 0.0
    ln_rel_acc = 0.0

    j_idx = 0
    sp_idx = 0
    st_idx = 0
    rel_idx = 0

    half_w = opts.window_s / 2.0

    all_locked = _build_locked_finger_counts(times_s, col_lns, tuple(range(7)))

    for step_idx, t in enumerate(times_s):
        # Decay
        jack_acc *= decay
        speed_acc *= decay
        stream_acc *= decay
        ln_rel_acc *= decay

        # Jack impulses up to t
        while j_idx < len(jack_impulses) and jack_impulses[j_idx][0] <= t:
            jack_acc += jack_impulses[j_idx][1]
            j_idx += 1

        # Speed impulses up to t
        while sp_idx < len(speed_impulses) and speed_impulses[sp_idx][0] <= t:
            speed_acc += speed_impulses[sp_idx][1]
            sp_idx += 1

        # Stream impulses up to t
        while st_idx < len(stream_impulses) and stream_impulses[st_idx][0] <= t:
            stream_acc += stream_impulses[st_idx][1]
            st_idx += 1

        # LN Releases up to t
        while rel_idx < len(col_releases) and col_releases[rel_idx][0] <= t:
            ln_rel_acc += 1.0
            rel_idx += 1

        # Window notes for concurrent density
        w_start = t - half_w
        w_end = t + half_w
        notes_in_w = [ho for ho in hit_objects if (w_start * 1000.0) <= ho.time < (w_end * 1000.0)]
        local_nps = len(notes_in_w) / opts.window_s

        # Locked fingers at time t (precomputed step function)
        locked_fingers = all_locked[step_idx]
        has_ln = sum(1 for ho in notes_in_w if ho.note_type == NoteType.LN)
        hold_ratio = (has_ln / len(notes_in_w)) if notes_in_w else 0.0

        # Tech permutation metric Omega_irreg in window
        reversals = 0
        if len(notes_in_w) >= 3:
            for k in range(len(notes_in_w) - 2):
                c0 = notes_in_w[k].column
                c1 = notes_in_w[k + 1].column
                c2 = notes_in_w[k + 2].column
                d1 = c1 - c0
                d2 = c2 - c1
                if (d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0):
                    reversals += 1
        tort = reversals / max(1, len(notes_in_w))
        omega_local = 1.0 + 0.5 * tort + 0.25 * (1.0 if locked_fingers > 0 else 0.0)

        # Base kinetic strain at t
        k_base = max(stream_acc, speed_acc, jack_acc * 0.8)

        # Instantaneous raw dimensions
        raw_curves["jack"].append(jack_acc)
        raw_curves["speed"].append(speed_acc)
        raw_curves["stream"].append(stream_acc)
        raw_curves["tech"].append(k_base * max(0.0, omega_local - 1.0) * 2.5)

        # LN dimensions
        raw_curves["ln_general"].append(hold_ratio * local_nps * (1.0 + 0.3 * locked_fingers))
        raw_curves["ln_tech"].append(hold_ratio * k_base * max(0.0, omega_local - 1.0) * 2.0)
        inv_load = math.pow(max(0.0, locked_fingers - 1.5) / 1.5, 2.0) if locked_fingers >= 2 else 0.0
        raw_curves["ln_inverse"].append(local_nps * hold_ratio * inv_load)
        raw_curves["ln_release"].append(ln_rel_acc + (1.0 if locked_fingers > 0 else 0.0))

    # Calibrate each curve to the benchmark strain matching its calibrated radar score
    calibrated_curves: Dict[str, List[float]] = {}
    for d in dims:
        radar_score = getattr(radar, d, 0.0)
        target_strain = star_rating_to_strain(radar_score) if radar_score > 0.15 else 0.0
        raw_c = raw_curves[d]
        peak_raw = max(raw_c) if raw_c else 0.0

        if target_strain <= 0.0 or peak_raw <= 1e-4:
            calibrated_curves[d] = [0.0] * len(raw_c)
            continue

        active_vals = [v for v in raw_c if v > 1e-3]
        if active_vals:
            p90_active = _calculate_percentile(active_vals, 90.0)
        else:
            p90_active = peak_raw

        p90_all = _calculate_percentile(raw_c, 90.0)
        p_base = max(p90_all, p90_active)

        scale = target_strain / max(1e-4, p_base)
        # Safety bound: maximum peak strain cannot exceed 2.0x target strain
        max_allowed_scale = (2.0 * target_strain) / max(1e-4, peak_raw)
        scale = min(scale, max_allowed_scale)

        calibrated_curves[d] = [round(max(0.0, v * scale), 3) for v in raw_c]

    return TechniqueStrainTimeseries(
        step_seconds=step_s,
        times=times_s,
        jack=calibrated_curves["jack"],
        tech=calibrated_curves["tech"],
        speed=calibrated_curves["speed"],
        stream=calibrated_curves["stream"],
        ln_general=calibrated_curves["ln_general"],
        ln_tech=calibrated_curves["ln_tech"],
        ln_inverse=calibrated_curves["ln_inverse"],
        ln_release=calibrated_curves["ln_release"],
    )

