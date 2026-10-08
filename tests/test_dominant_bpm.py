from typing import List

import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dominant_bpm, dominant_timing_point


def _make_beatmap(hit_objects: List[HitObject], timing_points: List[TimingPoint]) -> Beatmap7K:
    return Beatmap7K(
        title="Test Physics Beatmap",
        artist="Test Artist",
        creator="Tester",
        version="Test Version",
        hit_objects=hit_objects,
        timing_points=timing_points,
    )


def _rice_chart(end_ms: float, interval_ms: float = 100.0) -> List[HitObject]:
    return [
        HitObject(column=i % 7, time=t, note_type=NoteType.RICE)
        for i, t in enumerate(range(0, int(end_ms), int(interval_ms)))
    ]


def test_dominant_bpm_is_duration_weighted():
    # 100s at 120 BPM, then 20s at 200 BPM: the tempo heard for most of the chart wins,
    # regardless of where it sits in the timing point list.
    slow_first = [
        TimingPoint(time=0.0, beat_length=500.0, uninherited=True),
        TimingPoint(time=100000.0, beat_length=300.0, uninherited=True),
    ]
    fast_first = [
        TimingPoint(time=0.0, beat_length=300.0, uninherited=True),
        TimingPoint(time=20000.0, beat_length=500.0, uninherited=True),
    ]
    hos = _rice_chart(end_ms=120000.0)

    assert dominant_bpm(_make_beatmap(hos, slow_first)) == pytest.approx(120.0)
    assert dominant_bpm(_make_beatmap(hos, fast_first)) == pytest.approx(120.0)

    # A chart with no uninherited points falls back to the default tempo
    assert dominant_bpm(_make_beatmap(hos, [])) == 150.0
    assert dominant_timing_point(_make_beatmap(hos, [])) is None
