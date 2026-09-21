from typing import List
import pytest

from proj7k.dan import CANONICAL_DAN_TIERS
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, parse_osu_7k
from proj7k.rating import RatingOptions
from proj7k.radar import (
    TECHNIQUE_NAMES,
    RawTechniqueDrivers,
    RadarOptions,
    TechniqueRadar,
    compute_raw_technique_drivers,
    compute_technique_radar,
)


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


def _mixed_chart() -> Beatmap7K:
    """Rice flow with an LN layer, so more than one technique dimension is live."""
    hos = [HitObject(column=i % 7, time=i * 80.0, note_type=NoteType.RICE) for i in range(60)]
    hos += [
        HitObject(column=2, time=5000.0 + i * 90.0, note_type=NoteType.LN, end_time=5400.0 + i * 90.0)
        for i in range(20)
    ]
    return _make_sample_beatmap(hos)


def test_raw_drivers_are_the_quantity_the_radar_is_built_from():
    """
    The driver extraction is a seam onto the same computation the radar runs, not a second
    opinion about it: the radar's dominant technique and its zero dimensions are the drivers'
    own, read off before any star mapping. If the two ever disagree, the drivers stop
    describing the radar they are supposed to explain.
    """
    bm = _mixed_chart()
    drivers = compute_raw_technique_drivers(bm)
    radar = compute_technique_radar(bm)

    assert isinstance(drivers, RawTechniqueDrivers)
    assert set(drivers.to_dict()) == set(TECHNIQUE_NAMES)

    driver_map = drivers.to_dict()
    dominant = max(driver_map, key=lambda k: driver_map[k])
    assert radar.dominant_technique == dominant
    assert radar.dominant_score == getattr(radar, dominant)

    # A dimension the operators found nothing in reads exactly zero, on both sides of the seam.
    for name in TECHNIQUE_NAMES:
        assert (getattr(radar, name) == 0.0) == (driver_map[name] == 0.0), name

    assert radar.tech_4d == drivers.tech_4d


def test_raw_drivers_are_physical_units_not_star_values(benchmark_manifest, benchmark_corpus):
    """
    Drivers are deliberately *not* on the star scale: on the frozen benchmark ladder the
    strongest driver runs an order of magnitude past the whole star scale's ceiling. That is
    why a driver's star rating has to be calibrated per technique rather than borrowed from the
    strain anchor law, whose inputs live in a different range entirely.

    The scale's ceiling is the soft cap's asymptote, not a separate clamp: the compression
    converges towards `soft_cap_threshold + soft_cap_scale` without reaching it.
    """
    rating = RatingOptions()
    ceiling = rating.soft_cap_threshold + rating.soft_cap_scale

    strongest = max(
        max(
            compute_raw_technique_drivers(
                parse_osu_7k(benchmark_corpus[int(benchmark_manifest[technique][tier]["id"])])
            ).to_dict().values()
        )
        for technique in benchmark_manifest
        for tier in (CANONICAL_DAN_TIERS[-1],)
    )

    assert strongest > 10.0 * ceiling


def test_raw_drivers_of_an_empty_beatmap_are_all_zero():
    """An empty chart has no demand in any technique — no drivers, so no star-scale scores."""
    drivers = compute_raw_technique_drivers(_make_sample_beatmap([]))

    for name in TECHNIQUE_NAMES:
        assert getattr(drivers, name) == 0.0, name


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

