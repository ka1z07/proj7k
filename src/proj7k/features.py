from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
from proj7k.parser import Beatmap7K, NoteType
from proj7k.window import generate_all_barlines
from proj7k.scaling import compute_action_window, compute_inverse_score


@dataclass
class BeatmapFeatures:
    total_notes: int
    rice_count: int
    ln_count: int
    hold_pct: float
    avg_nps: float
    peak_4m_nps: float
    duration_seconds: float
    peak_1b_nps: float = 0.0

    # Hand topology
    gap1_count: int = 0
    gap1_density: float = 0.0
    adj_count: int = 0
    adj_density: float = 0.0

    # Degree-of-freedom suppression
    mean_locked_fingers: float = 0.0
    lockout_profile: Dict[int, float] = field(default_factory=lambda: {i: 0.0 for i in range(8)})

    # Antiphase articulation
    antiphase_count: int = 0
    antiphase_rate: float = 0.0

    # Action clock window and calibrated inverse score
    delta_t_action: float = 0.0
    inverse_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["lockout_profile"] = {str(k): v for k, v in self.lockout_profile.items()}
        return d


def _calc_rate(count: int, duration_s: float) -> float:
    return round(count / duration_s, 4) if duration_s > 0 else 0.0


def get_dominant_bpm(beatmap: Beatmap7K, default: float = 150.0) -> float:
    """Extracts dominant BPM from uninherited timing points."""
    uninherited = [tp for tp in beatmap.timing_points if tp.uninherited and tp.beat_length > 0]
    if not uninherited:
        return default
    if len(uninherited) == 1:
        return round(60000.0 / uninherited[0].beat_length, 2)
    durations: Dict[float, float] = {}
    for i, tp in enumerate(uninherited):
        bpm_val = round(60000.0 / tp.beat_length, 2)
        end_t = uninherited[i + 1].time if (i + 1 < len(uninherited)) else (
            max(ho.time for ho in beatmap.hit_objects) if beatmap.hit_objects else tp.time + 10000.0
        )
        span = max(0.0, end_t - tp.time)
        durations[bpm_val] = durations.get(bpm_val, 0.0) + span
    return max(durations.keys(), key=lambda b: durations[b])


def extract_beatmap_features(
    beatmap: Beatmap7K,
    step_ms: int = 10,
    bpm: Optional[float] = None,
) -> BeatmapFeatures:
    """
    Extracts baseline spatiotemporal density and timing features, as well as
    physiological topology, degree-of-freedom suppression, and antiphase events:
    - total_notes, rice_count, ln_count
    - hold_pct (LN notes percentage)
    - avg_nps (Full-beatmap average NPS)
    - peak_4m_nps (Peak 4-measure rolling window NPS)
    - peak_1b_nps (Peak 1-beat burst window NPS)
    - duration_seconds
    - gap1_count, gap1_density
    - adj_count, adj_density
    - mean_locked_fingers, lockout_profile
    - antiphase_count, antiphase_rate
    """
    total_notes = len(beatmap.hit_objects)
    if total_notes == 0:
        return BeatmapFeatures(
            total_notes=0,
            rice_count=0,
            ln_count=0,
            hold_pct=0.0,
            avg_nps=0.0,
            peak_4m_nps=0.0,
            duration_seconds=0.0,
            peak_1b_nps=0.0,
            gap1_count=0,
            gap1_density=0.0,
            adj_count=0,
            adj_density=0.0,
            mean_locked_fingers=0.0,
            lockout_profile={i: 0.0 for i in range(8)},
            antiphase_count=0,
            antiphase_rate=0.0,
            delta_t_action=0.0,
            inverse_score=0.0,
        )

    rice_count = sum(1 for ho in beatmap.hit_objects if ho.note_type == NoteType.RICE)
    ln_count = total_notes - rice_count
    hold_pct = (ln_count / total_notes) * 100.0

    # Calculate overall duration
    start_ms = min(ho.time for ho in beatmap.hit_objects)
    end_ms = max(
        (ho.end_time if (ho.note_type == NoteType.LN and ho.end_time is not None) else ho.time)
        for ho in beatmap.hit_objects
    )

    duration_s = max((end_ms - start_ms) / 1000.0, 0.0)
    avg_nps = (total_notes / duration_s) if duration_s > 0 else 0.0

    barlines = generate_all_barlines(beatmap, max_time_ms=end_ms + 30000.0)
    measure_starts = [b for b in barlines if b.is_measure_start]

    # 1. Calculate Peak-4M NPS (4-measure rolling window)
    peak_4m_nps = avg_nps
    if len(measure_starts) >= 5:
        max_window_nps = 0.0
        found_valid_window = False
        for i in range(len(measure_starts) - 4):
            window_start = measure_starts[i].time
            window_end = measure_starts[i + 4].time
            if window_start > end_ms:
                break
            if window_start < start_ms or window_end > end_ms:
                continue
            window_duration_s = (window_end - window_start) / 1000.0
            if window_duration_s <= 0:
                continue

            found_valid_window = True
            window_notes_count = sum(1 for ho in beatmap.hit_objects if window_start <= ho.time < window_end)
            window_nps = window_notes_count / window_duration_s
            if window_nps > max_window_nps:
                max_window_nps = window_nps

        if found_valid_window:
            peak_4m_nps = max_window_nps

    # 2. Calculate Peak-1B NPS (1-beat burst window)
    peak_1b_nps = avg_nps
    if len(barlines) >= 2:
        max_beat_nps = 0.0
        found_valid_beat = False
        for i in range(len(barlines) - 1):
            beat_start = barlines[i].time
            beat_end = barlines[i + 1].time
            if beat_start > end_ms:
                break
            if beat_start < start_ms or beat_end > end_ms:
                continue
            beat_duration_s = (beat_end - beat_start) / 1000.0
            if beat_duration_s <= 0:
                continue

            found_valid_beat = True
            beat_notes_count = sum(1 for ho in beatmap.hit_objects if beat_start <= ho.time < beat_end)
            beat_nps = beat_notes_count / beat_duration_s
            if beat_nps > max_beat_nps:
                max_beat_nps = beat_nps

        if found_valid_beat:
            peak_1b_nps = max_beat_nps

    # Single pass over hit objects to collect chord press ticks and release ticks
    notes_by_time: Dict[int, List[int]] = {}
    ticks_released: Dict[int, List[int]] = {}

    for ho in beatmap.hit_objects:
        st_tick = int(round(ho.time))
        notes_by_time.setdefault(st_tick, []).append(ho.column)
        if ho.note_type == NoteType.LN and ho.end_time is not None:
            et_tick = int(round(ho.end_time))
            ticks_released.setdefault(et_tick, []).append(ho.column)

    # 3. Calculate hand topology metrics ([gap:1] and [adj])
    gap1_count = 0
    adj_count = 0
    for t_key, cols in notes_by_time.items():
        unique_cols = sorted(set(cols))
        l_cols = [c for c in unique_cols if c in (0, 1, 2)]
        r_cols = [c for c in unique_cols if c in (4, 5, 6)]

        # Left hand evaluation
        if len(l_cols) == 2:
            if l_cols == [0, 2]:
                gap1_count += 1
            elif l_cols in ([0, 1], [1, 2]):
                adj_count += 1

        # Right hand evaluation
        if len(r_cols) == 2:
            if r_cols == [4, 6]:
                gap1_count += 1
            elif r_cols in ([4, 5], [5, 6]):
                adj_count += 1

    gap1_density = _calc_rate(gap1_count, duration_s)
    adj_density = _calc_rate(adj_count, duration_s)

    # 4. Compute finger lockout profile and mean locked fingers (sampling every step_ms)
    # Merge overlapping LN intervals per column to guarantee each physical finger is at most locked once
    merged_ln_intervals: List[Tuple[float, float]] = []
    for col in range(7):
        col_lns = sorted(
            [
                (ho.time, ho.end_time)
                for ho in beatmap.hit_objects
                if ho.column == col and ho.note_type == NoteType.LN and ho.end_time is not None
            ],
            key=lambda x: x[0],
        )
        if not col_lns:
            continue
        cur_st, cur_et = col_lns[0]
        for st, et in col_lns[1:]:
            if st <= cur_et:
                cur_et = max(cur_et, et)
            else:
                merged_ln_intervals.append((cur_st, cur_et))
                cur_st, cur_et = st, et
        merged_ln_intervals.append((cur_st, cur_et))

    lock_counts: Dict[int, int] = {i: 0 for i in range(8)}
    total_samples = 0
    sum_locked = 0

    if step_ms > 0 and end_ms >= start_ms:
        import math
        num_samples = int((end_ms - start_ms) // step_ms) + 1
        total_samples = num_samples

        if merged_ln_intervals:
            diff = [0] * (num_samples + 1)
            for st, et in merged_ln_intervals:
                if et <= start_ms or st >= end_ms:
                    continue
                j_start = max(0, math.ceil((st - start_ms) / step_ms))
                j_end = min(num_samples, math.ceil((et - start_ms) / step_ms))
                if j_start < j_end:
                    diff[j_start] += 1
                    diff[j_end] -= 1

            cur_holds = 0
            for j in range(num_samples):
                cur_holds += diff[j]
                active = min(max(cur_holds, 0), 7)
                lock_counts[active] += 1
                sum_locked += active
        else:
            lock_counts[0] = num_samples
    else:
        total_samples = 1
        lock_counts[0] = 1

    mean_locked_fingers = round(sum_locked / max(total_samples, 1), 4)
    lockout_profile = {
        k: round((v / max(total_samples, 1)) * 100.0, 4) for k, v in lock_counts.items()
    }

    # 5. Detect and count antiphase articulation events
    # An antiphase event occurs when at the same tick (rounded ms), one track releases (LN tail)
    # while another track (different column) is pressed (Rice or LN head).
    antiphase_count = 0
    for tick, released_cols in ticks_released.items():
        if tick in notes_by_time:
            pressed_cols = notes_by_time[tick]
            for r_col in released_cols:
                for p_col in pressed_cols:
                    if r_col != p_col:
                        antiphase_count += 1

    antiphase_rate = _calc_rate(antiphase_count, duration_s)

    effective_bpm = bpm if (bpm is not None and bpm > 0) else get_dominant_bpm(beatmap)
    delta_t_action = compute_action_window(effective_bpm)
    inverse_score = compute_inverse_score(mean_locked_fingers, bpm=effective_bpm, nps=avg_nps)

    return BeatmapFeatures(
        total_notes=total_notes,
        rice_count=rice_count,
        ln_count=ln_count,
        hold_pct=round(hold_pct, 4),
        avg_nps=round(avg_nps, 4),
        peak_4m_nps=round(peak_4m_nps, 4),
        duration_seconds=round(duration_s, 4),
        peak_1b_nps=round(peak_1b_nps, 4),
        gap1_count=gap1_count,
        gap1_density=gap1_density,
        adj_count=adj_count,
        adj_density=adj_density,
        mean_locked_fingers=mean_locked_fingers,
        lockout_profile=lockout_profile,
        antiphase_count=antiphase_count,
        antiphase_rate=antiphase_rate,
        delta_t_action=delta_t_action,
        inverse_score=inverse_score,
    )
