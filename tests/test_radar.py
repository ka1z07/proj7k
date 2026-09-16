from typing import List
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.radar import TechniqueRadar, RadarOptions, compute_technique_radar


def _make_sample_beatmap(
    hit_objects: List[HitObject],
    bpm: float = 150.0,
) -> Beatmap7K:
    beat_length = 60000.0 / bpm if bpm > 0 else 500.0
    return Beatmap7K(
        title="Test Radar Beatmap",
        artist="Test Artist",
        creator="Tester",
        version="Test Version",
        hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=beat_length, meter=4, uninherited=True)],
    )


def test_empty_beatmap_radar():
    bm = _make_sample_beatmap([])
    radar = compute_technique_radar(bm)

    assert isinstance(radar, TechniqueRadar)
    assert radar.dominant_score == 0.0
    assert radar.dominant_technique == "None"
    assert radar.jack == 0.0
    assert radar.tech == 0.0
    assert radar.speed == 0.0
    assert radar.stream == 0.0
    assert radar.ln_general == 0.0
    assert radar.ln_tech == 0.0
    assert radar.ln_inverse == 0.0
    assert radar.ln_release == 0.0

    d = radar.to_dict()
    assert d["dominant_technique"] == "None"
    assert d["dominant_score"] == 0.0
    assert len(d) == 10


def test_pure_rice_cross_inhibition():
    # Pure rice streaming chart without any long notes (hold_pct == 0.0)
    hos: List[HitObject] = []
    for i in range(100):
        hos.append(HitObject(column=i % 7, time=i * 100.0, note_type=NoteType.RICE))
    bm = _make_sample_beatmap(hos)
    radar = compute_technique_radar(bm)

    # All LN dimensions must be cross-inhibited to exactly 0.0
    assert radar.ln_general == 0.0
    assert radar.ln_tech == 0.0
    assert radar.ln_inverse == 0.0
    assert radar.ln_release == 0.0
    assert radar.dominant_technique in ("stream", "speed", "tech", "jack")
    assert radar.dominant_score > 0.0


def test_pure_jack_dominance():
    # Rapid 100ms jacks on columns 0 and 6, no long notes
    hos: List[HitObject] = []
    for i in range(30):
        hos.append(HitObject(column=0, time=i * 100.0, note_type=NoteType.RICE))
        hos.append(HitObject(column=6, time=i * 100.0, note_type=NoteType.RICE))
    bm = _make_sample_beatmap(hos)
    radar = compute_technique_radar(bm)

    assert radar.dominant_technique == "jack"
    assert radar.jack > 4.0
    # LN dimensions must be completely suppressed
    assert radar.ln_general == 0.0
    assert radar.ln_inverse == 0.0


def test_pure_speed_burst_dominance():
    # 300 BPM fast single notes (50ms interval) across alternating lanes
    hos: List[HitObject] = []
    for i in range(60):
        hos.append(HitObject(column=(i * 2) % 7, time=i * 50.0, note_type=NoteType.RICE))
    bm = _make_sample_beatmap(hos, bpm=300.0)
    radar = compute_technique_radar(bm)

    assert radar.dominant_technique == "speed"
    assert radar.speed > 4.5
    assert radar.ln_general == 0.0


def test_ln_inverse_and_release_dominance():
    # 220 BPM high density LN inverse:
    # 2 locked fingers per hand held as LN, 1 finger tapping rapidly
    # with antiphase releases
    hos: List[HitObject] = [
        HitObject(column=0, time=0.0, note_type=NoteType.LN, end_time=2000.0),
        HitObject(column=1, time=0.0, note_type=NoteType.LN, end_time=2000.0),
        HitObject(column=5, time=0.0, note_type=NoteType.LN, end_time=2000.0),
        HitObject(column=6, time=0.0, note_type=NoteType.LN, end_time=2000.0),
    ]
    # Tapping on col 2 and 4 with micro releases
    for i in range(25):
        t = i * 70.0
        hos.append(HitObject(column=2, time=t, note_type=NoteType.LN, end_time=t + 65.0))
        hos.append(HitObject(column=4, time=t, note_type=NoteType.LN, end_time=t + 65.0))

    bm = _make_sample_beatmap(hos, bpm=220.0)
    radar = compute_technique_radar(bm)

    assert radar.ln_inverse > 4.0
    assert radar.ln_release > 1.5
    assert radar.dominant_technique in ("ln_inverse", "ln_release", "ln_tech", "ln_general")
    # Jack should be suppressed
    assert radar.jack < 1.0


def test_pure_jack_suppresses_stream_and_tech_noise():
    # Only column 0 hit rapidly (60ms intervals), no notes on any other column
    hos = [HitObject(column=0, time=i * 60.0, note_type=NoteType.RICE) for i in range(50)]
    bm = _make_sample_beatmap(hos)
    radar = compute_technique_radar(bm)

    assert radar.dominant_technique == "jack"
    assert radar.jack > 6.0
    assert radar.stream == 0.0
    assert radar.tech == 0.0


def test_multilane_jack_suppression():
    # 3 lanes hit simultaneously as chordjacks (active_lanes > 2, gap >= 2)
    hos = []
    for i in range(40):
        t = i * 80.0
        hos.append(HitObject(column=0, time=t, note_type=NoteType.RICE))
        hos.append(HitObject(column=3, time=t, note_type=NoteType.RICE))
        hos.append(HitObject(column=6, time=t, note_type=NoteType.RICE))
    bm = _make_sample_beatmap(hos)
    radar = compute_technique_radar(bm)
    assert radar.dominant_technique == "jack"
    assert radar.jack > 5.0


def test_radar_data_contract():
    hos = [HitObject(column=i % 7, time=i * 80.0, note_type=NoteType.RICE) for i in range(50)]
    bm = _make_sample_beatmap(hos)
    radar = compute_technique_radar(bm)

    d = radar.to_dict()
    for key in (
        "jack", "tech", "speed", "stream",
        "ln_general", "ln_tech", "ln_inverse", "ln_release",
        "dominant_technique", "dominant_score",
    ):
        assert key in d
    assert isinstance(d["dominant_technique"], str)
    assert isinstance(d["dominant_score"], float)


def test_isolated_two_note_jack_not_zeroed_by_stream():
    """
    Unit test for SPEC-P2.2-01:
    A chart with mostly stream notes but interspersed with 16th-note 2-note jacks
    must retain jack > 0, rather than being completely zeroed out by Rule D.
    """
    hos = []
    # 200 stream notes at 150ms intervals across columns 0..6
    for i in range(200):
        hos.append(HitObject(column=(i % 7), time=i * 150.0, note_type=NoteType.RICE))
    # Interspersed with 10 pairs of 2-note jacks at 120ms intervals
    for j in range(10):
        t_base = 35000.0 + j * 2000.0
        hos.append(HitObject(column=3, time=t_base, note_type=NoteType.RICE))
        hos.append(HitObject(column=3, time=t_base + 120.0, note_type=NoteType.RICE))
    
    bm = _make_sample_beatmap(hos)
    radar = compute_technique_radar(bm)
    assert radar.jack > 1.0, f"Expected jack > 1.0 for interspersed 2-note jacks, got {radar.jack}"

