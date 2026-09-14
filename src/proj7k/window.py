from dataclasses import dataclass, field
import math
from typing import List, Optional, Tuple
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint


def parse_measure_arg(measure_str: str) -> Tuple[int, int]:
    """Parse measure range string (e.g. '12-16', 'M12-M16', or '12') into (start_m, end_m)."""
    m_str = measure_str.strip().upper().replace("M", "")
    if "-" in m_str:
        parts = m_str.split("-", 1)
        start_m = int(parts[0])
        end_m = int(parts[1])
    else:
        start_m = int(m_str)
        end_m = start_m + 1
    return start_m, end_m



@dataclass
class Barline:
    time: float
    measure_index: int
    is_measure_start: bool
    beat_index: int = 0


@dataclass
class SliceWindow:
    beatmap: Beatmap7K
    start_ms: float
    end_ms: float
    hit_objects: List[HitObject] = field(default_factory=list)
    barlines: List[Barline] = field(default_factory=list)


def _generate_all_barlines(beatmap: Beatmap7K, max_time_ms: float) -> List[Barline]:
    """Generate global continuous barlines accumulating measure numbers across all BPM changes."""
    uninherited_tps = [tp for tp in beatmap.timing_points if tp.uninherited and tp.beat_length > 0]
    if not uninherited_tps:
        return []

    uninherited_tps.sort(key=lambda tp: tp.time)
    all_barlines: List[Barline] = []
    accumulated_measure_idx = 0

    for i, tp in enumerate(uninherited_tps):
        tp_start = tp.time
        has_next = (i + 1 < len(uninherited_tps))
        tp_end = uninherited_tps[i + 1].time if has_next else max(max_time_ms + 10000.0, tp_start + 10000.0)

        beat_len = tp.beat_length
        meter = tp.meter if tp.meter > 0 else 4

        beat_num = 0
        while True:
            curr_time = round(tp_start + beat_num * beat_len, 2)
            # Boundary condition: if next timing point exists, strictly stop before tp_end
            # to prevent duplicate barlines on the transition millisecond
            if has_next and curr_time >= tp_end:
                break
            if not has_next and curr_time > tp_end:
                break

            beat_in_measure = beat_num % meter
            measure_offset = beat_num // meter
            is_measure = (beat_in_measure == 0)

            all_barlines.append(
                Barline(
                    time=curr_time,
                    measure_index=accumulated_measure_idx + measure_offset,
                    is_measure_start=is_measure,
                    beat_index=beat_in_measure,
                )
            )
            beat_num += 1

        # Advance accumulated measures for next timing point segment
        if beat_num > 0:
            measures_completed = math.ceil(beat_num / meter)
            accumulated_measure_idx += measures_completed

    # Sort and guarantee no duplicates
    all_barlines.sort(key=lambda b: b.time)
    deduped: List[Barline] = []
    seen_times = set()
    for b in all_barlines:
        if b.time not in seen_times:
            seen_times.add(b.time)
            deduped.append(b)
    return deduped


def extract_time_window(
    beatmap: Beatmap7K,
    start_ms: float,
    end_ms: float,
) -> SliceWindow:
    if start_ms > end_ms:
        start_ms, end_ms = end_ms, start_ms

    filtered_objects: List[HitObject] = []
    for ho in beatmap.hit_objects:
        if ho.note_type == NoteType.RICE:
            if start_ms <= ho.time <= end_ms:
                filtered_objects.append(ho)
        elif ho.note_type == NoteType.LN:
            end_t = ho.end_time if ho.end_time is not None else ho.time
            if ho.time <= end_ms and end_t >= start_ms:
                filtered_objects.append(ho)

    all_barlines = _generate_all_barlines(beatmap, max_time_ms=end_ms)
    window_barlines = [b for b in all_barlines if start_ms <= b.time <= end_ms]

    return SliceWindow(
        beatmap=beatmap,
        start_ms=start_ms,
        end_ms=end_ms,
        hit_objects=filtered_objects,
        barlines=window_barlines,
    )


def extract_measure_window(
    beatmap: Beatmap7K,
    start_measure: int,
    end_measure: int,
) -> SliceWindow:
    """Extract a window based on measure numbers (0-indexed or 1-indexed, auto-detected)."""
    # Generate barlines well beyond estimated end
    estimated_end = 600000.0  # 10 minutes default
    all_barlines = _generate_all_barlines(beatmap, max_time_ms=estimated_end)
    measure_starts = [b for b in all_barlines if b.is_measure_start]

    if not measure_starts:
        # Fallback to empty
        return extract_time_window(beatmap, 0.0, 0.0)

    # Convert to 0-indexed if user passed 1-indexed
    min_idx = measure_starts[0].measure_index
    max_idx = measure_starts[-1].measure_index

    # Find time for start_measure and end_measure
    start_match = next((b for b in measure_starts if b.measure_index == start_measure), None)
    end_match = next((b for b in measure_starts if b.measure_index == end_measure), None)

    if start_match is None:
        # If start_measure not found, check 1-indexed (e.g. M1 -> index 0)
        start_match = next((b for b in measure_starts if b.measure_index == start_measure - 1), measure_starts[0])

    if end_match is None:
        end_match = next((b for b in measure_starts if b.measure_index == end_measure - 1), None)
        if end_match is None:
            # End after the start measure + 1 measure length
            end_t = start_match.time + 2000.0
        else:
            end_t = end_match.time
    else:
        end_t = end_match.time

    return extract_time_window(beatmap, start_match.time, end_t)
