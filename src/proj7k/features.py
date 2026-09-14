from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from proj7k.parser import Beatmap7K, NoteType
from proj7k.window import _generate_all_barlines


@dataclass
class BeatmapFeatures:
    total_notes: int
    rice_count: int
    ln_count: int
    hold_pct: float
    avg_nps: float
    peak_4m_nps: float
    duration_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_beatmap_features(beatmap: Beatmap7K) -> BeatmapFeatures:
    """
    Extracts baseline spatiotemporal density and timing features:
    - total_notes, rice_count, ln_count
    - hold_pct (LN notes percentage)
    - avg_nps (Full-beatmap average NPS)
    - peak_4m_nps (Peak 4-measure rolling window NPS)
    - duration_seconds
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

    # Calculate Peak-4M NPS (4-measure rolling window)
    peak_4m_nps = avg_nps
    barlines = _generate_all_barlines(beatmap, max_time_ms=end_ms + 30000.0)
    measure_starts = [b for b in barlines if b.is_measure_start]

    if len(measure_starts) >= 5:
        # Check if the beatmap duration spans at least one 4-measure interval
        first_4m_duration = measure_starts[4].time - measure_starts[0].time
        if (end_ms - start_ms) >= first_4m_duration:
            max_window_nps = 0.0
            # Iterate through all 4-measure rolling windows
            for i in range(len(measure_starts) - 4):
                w_start = measure_starts[i].time
                w_end = measure_starts[i + 4].time
                if w_start > end_ms:
                    break
                if w_end < start_ms:
                    continue
                w_dur = (w_end - w_start) / 1000.0
                if w_dur <= 0:
                    continue

                # Count notes starting within [w_start, w_end)
                w_notes = sum(1 for ho in beatmap.hit_objects if w_start <= ho.time < w_end)
                w_nps = w_notes / w_dur
                if w_nps > max_window_nps:
                    max_window_nps = w_nps

            peak_4m_nps = max_window_nps

    return BeatmapFeatures(
        total_notes=total_notes,
        rice_count=rice_count,
        ln_count=ln_count,
        hold_pct=round(hold_pct, 4),
        avg_nps=round(avg_nps, 4),
        peak_4m_nps=round(peak_4m_nps, 4),
        duration_seconds=round(duration_s, 4),
    )
