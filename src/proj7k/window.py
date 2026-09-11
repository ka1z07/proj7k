from dataclasses import dataclass, field
import math
from typing import List, Optional
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint


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


def extract_time_window(
    beatmap: Beatmap7K,
    start_ms: float,
    end_ms: float,
) -> SliceWindow:
    if start_ms > end_ms:
        start_ms, end_ms = end_ms, start_ms

    # 1. Filter hit objects
    filtered_objects: List[HitObject] = []
    for ho in beatmap.hit_objects:
        if ho.note_type == NoteType.RICE:
            if start_ms <= ho.time <= end_ms:
                filtered_objects.append(ho)
        elif ho.note_type == NoteType.LN:
            end_t = ho.end_time if ho.end_time is not None else ho.time
            # LN overlaps if it starts before window end and ends after window start
            if ho.time <= end_ms and end_t >= start_ms:
                filtered_objects.append(ho)

    # 2. Compute barlines and beats
    uninherited_tps = [tp for tp in beatmap.timing_points if tp.uninherited and tp.beat_length > 0]
    barlines: List[Barline] = []

    if uninherited_tps:
        for i, tp in enumerate(uninherited_tps):
            tp_start = tp.time
            tp_end = uninherited_tps[i + 1].time if i + 1 < len(uninherited_tps) else max(end_ms + 1000.0, tp_start + 10000.0)

            # Check if this timing point segment overlaps with [start_ms, end_ms]
            seg_start = max(tp_start, start_ms - 5000.0)
            seg_end = min(tp_end, end_ms + 5000.0)

            if seg_start > seg_end:
                continue

            beat_len = tp.beat_length
            meter = tp.meter if tp.meter > 0 else 4

            # Calculate first beat at or before seg_start
            delta = seg_start - tp_start
            start_beat_num = int(math.floor(delta / beat_len)) if delta > 0 else 0
            
            beat_num = start_beat_num
            while True:
                curr_time = round(tp_start + beat_num * beat_len, 2)
                if curr_time > seg_end or curr_time > tp_end:
                    break

                if start_ms <= curr_time <= end_ms:
                    measure_idx = beat_num // meter
                    beat_in_measure = beat_num % meter
                    is_measure = (beat_in_measure == 0)

                    barlines.append(
                        Barline(
                            time=curr_time,
                            measure_index=measure_idx,
                            is_measure_start=is_measure,
                            beat_index=beat_in_measure,
                        )
                    )
                beat_num += 1

    barlines.sort(key=lambda b: b.time)

    return SliceWindow(
        beatmap=beatmap,
        start_ms=start_ms,
        end_ms=end_ms,
        hit_objects=filtered_objects,
        barlines=barlines,
    )
