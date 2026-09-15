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

from proj7k.parser import Beatmap7K, NoteType

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_seconds": self.step_seconds,
            "times": self.times,
            "left_hand_strain": self.left_hand_strain,
            "right_hand_strain": self.right_hand_strain,
            "combined_strain": self.combined_strain,
            "p90_strain": self.p90_strain,
            "p95_strain": self.p95_strain,
            "peak_strain": self.peak_strain,
        }


@dataclass(frozen=True)
class StrainOptions:
    """Configuration options for dual-hand strain accumulation and modulation."""
    tau_time_constant_s: float = 1.2
    tau_half_life_s: float = 1.2  # Alias for backward compatibility
    window_s: float = 0.5
    step_s: float = 0.25
    jack_threshold_ms: float = 160.0
    jack_weight: float = 0.18
    gap1_weight: float = 0.20
    gap1_threshold_s: float = 0.015
    speed_burst_threshold_ms: float = 110.0
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
    max_interval_ms: float = 110.0,
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
    t: float,
    w_start: float,
    w_end: float,
    col_lns: Dict[int, List[Tuple[float, float]]],
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
    # 1. Locked fingers at time t
    locked_fingers = sum(
        1 for c in hand_lanes
        if any(st <= t <= et for st, et in col_lns[c])
    )

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
                            if abs(pr - rl) < 0.025:
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

    # Determine effective BPM
    if options.bpm is not None and options.bpm > 0:
        bpm = options.bpm
    else:
        bpm = 150.0
        for tp in beatmap.timing_points:
            if tp.uninherited and tp.bpm is not None and tp.bpm > 0:
                bpm = tp.bpm
                break

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

    for t in times_s:
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
            t=t,
            w_start=w_start,
            w_end=w_end,
            col_lns=col_lns,
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
            t=t,
            w_start=w_start,
            w_end=w_end,
            col_lns=col_lns,
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
