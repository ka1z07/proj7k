from typing import List

import pytest

from proj7k import physics, strain
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dominant_bpm, dominant_timing_point
from proj7k.radar import RadarOptions, _compute_jack_and_stream_raw
from proj7k.strain import StrainOptions, compute_dual_hand_strain


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


def test_strain_accumulator_reads_the_single_bpm_source():
    """
    The strain side must use the same dominant tempo as the rest of the engine — the
    retired path took the *first* uninherited timing point and disagreed on variable-BPM charts.
    """
    timing_points = [
        TimingPoint(time=0.0, beat_length=333.3333333333333, uninherited=True),   # 180 BPM, 10 s
        TimingPoint(time=10000.0, beat_length=272.7272727272727, uninherited=True),  # 220 BPM, 60 s
    ]
    hos: List[HitObject] = []
    for i, t in enumerate(range(0, 70000, 70)):
        if i % 6 == 0:
            # Holds make the cognitive term non-zero, which is where the tempo enters.
            hos.append(HitObject(column=0, time=float(t), note_type=NoteType.LN, end_time=float(t + 260)))
        else:
            hos.append(HitObject(column=(i % 6) + 1, time=float(t), note_type=NoteType.RICE))
    bm = _make_beatmap(hos, timing_points)

    dominant = dominant_bpm(bm)
    assert dominant == pytest.approx(220.0)

    implicit = compute_dual_hand_strain(bm)
    explicit = compute_dual_hand_strain(bm, options=StrainOptions(bpm=dominant))
    first_point = compute_dual_hand_strain(bm, options=StrainOptions(bpm=180.0))

    assert implicit.combined_strain == explicit.combined_strain
    assert implicit.peak_strain != first_point.peak_strain


def test_option_defaults_reference_shared_physics_constants():
    assert StrainOptions().jack_threshold_ms == physics.JACK_INTERVAL_PENALTY_MS
    assert RadarOptions().jack_threshold_ms == physics.CHORDJACK_STEP_INTERVAL_MS
    assert StrainOptions().speed_burst_threshold_ms == physics.SPEED_BURST_INTERVAL_MS
    assert RadarOptions().speed_burst_threshold_ms == physics.SPEED_BURST_INTERVAL_MS

    # The two jack thresholds grade different things and are documented as distinct;
    # collapsing them silently would re-calibrate every star rating.
    assert physics.JACK_INTERVAL_PENALTY_MS != physics.CHORDJACK_STEP_INTERVAL_MS


def test_antiphase_window_is_sourced_from_physics(monkeypatch):
    # LN releasing in lane 0 while lane 1 is struck 40 ms later.
    hos: List[HitObject] = []
    for i in range(12):
        t = i * 500.0
        hos.append(HitObject(column=0, time=t, note_type=NoteType.LN, end_time=t + 300.0))
        hos.append(HitObject(column=1, time=t + 340.0, note_type=NoteType.RICE))
    bm = _make_beatmap(hos, [TimingPoint(time=0.0, beat_length=400.0, uninherited=True)])

    # 40 ms gap is outside the shipped 25 ms onset window...
    assert physics.ANTIPHASE_ONSET_WINDOW_S == 0.025
    baseline = compute_dual_hand_strain(bm)

    # ...so widening the constant must change the accumulated strain: the window is read
    # from the shared constant, not baked into the accumulator as a literal.
    monkeypatch.setattr(strain, "ANTIPHASE_ONSET_WINDOW_S", 0.050)
    widened = compute_dual_hand_strain(bm)
    assert widened.peak_strain > baseline.peak_strain


def test_bracket_inversion_window_is_sourced_from_physics(monkeypatch):
    # Alternating [gap:1] {0,2} and [mid] {1} grip every 100 ms.
    hos: List[HitObject] = []
    for i in range(40):
        t = i * 100.0
        if i % 2 == 0:
            hos.append(HitObject(column=0, time=t, note_type=NoteType.RICE))
            hos.append(HitObject(column=2, time=t, note_type=NoteType.RICE))
        else:
            hos.append(HitObject(column=1, time=t, note_type=NoteType.RICE))
    bm = _make_beatmap(hos, [TimingPoint(time=0.0, beat_length=400.0, uninherited=True)])

    baseline = _compute_jack_and_stream_raw(bm, RadarOptions())
    assert baseline[-1] > 0.0  # bracket_density

    # Shrinking the shared window below the 100 ms step gap removes those inversions.
    monkeypatch.setattr("proj7k.radar.BRACKET_PHASE_INVERSION_WINDOW_MS", 50.0)
    narrowed = _compute_jack_and_stream_raw(bm, RadarOptions())
    assert narrowed[-1] == 0.0
