from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from proj7k.parser import Beatmap7K, NoteType
from proj7k.window import generate_all_barlines


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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_beatmap_features(beatmap: Beatmap7K) -> BeatmapFeatures:
    """
    Extracts baseline spatiotemporal density and timing features:
    - total_notes, rice_count, ln_count
    - hold_pct (LN notes percentage)
    - avg_nps (Full-beatmap average NPS)
    - peak_4m_nps (Peak 4-measure rolling window NPS)
    - peak_1b_nps (Peak 1-beat burst window NPS)
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
            peak_1b_nps=0.0,
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
        # Iterate through all 4-measure rolling windows that fit within beatmap span
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

    return BeatmapFeatures(
        total_notes=total_notes,
        rice_count=rice_count,
        ln_count=ln_count,
        hold_pct=round(hold_pct, 4),
        avg_nps=round(avg_nps, 4),
        peak_4m_nps=round(peak_4m_nps, 4),
        duration_seconds=round(duration_s, 4),
        peak_1b_nps=round(peak_1b_nps, 4),
    )
