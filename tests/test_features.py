from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.features import extract_beatmap_features, BeatmapFeatures


def test_extract_beatmap_features_basic():
    # 120 BPM: 1 beat = 500ms, 1 measure = 2000ms, 4 measures = 8000ms
    tp = TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)
    hit_objects = []

    # Measures 0-3 (0ms to 8000ms): 1 note every 1000ms -> 8 notes in 8s = 1.0 NPS
    for t in range(0, 8000, 1000):
        hit_objects.append(HitObject(column=0, time=float(t), note_type=NoteType.RICE))

    # Measures 4-7 (8000ms to 16000ms): 1 note every 250ms (4 notes/s) -> 32 notes in 8s = 4.0 NPS
    for t in range(8000, 16000, 250):
        hit_objects.append(HitObject(column=1, time=float(t), note_type=NoteType.RICE))

    # Measure 8 (16000ms to 18000ms): 2 LN notes
    hit_objects.append(HitObject(column=2, time=16000.0, note_type=NoteType.LN, end_time=17500.0))
    hit_objects.append(HitObject(column=4, time=16500.0, note_type=NoteType.LN, end_time=18000.0))

    bm = Beatmap7K(
        title="Test Beatmap",
        version="Hard",
        mode=3,
        circle_size=7,
        timing_points=[tp],
        hit_objects=hit_objects,
    )

    features = extract_beatmap_features(bm)

    assert isinstance(features, BeatmapFeatures)
    assert features.total_notes == 42  # 8 + 32 + 2
    assert features.rice_count == 40
    assert features.ln_count == 2
    assert round(features.hold_pct, 2) == round((2 / 42) * 100.0, 2)  # ~4.76%

    # Duration: 0.0 to 18000.0 ms = 18.0 seconds
    assert features.duration_seconds == 18.0
    assert round(features.avg_nps, 2) == round(42 / 18.0, 2)  # ~2.33 NPS

    # Peak 4-Measure NPS: Measures 4..8 is 8000ms..16000ms: 32 notes in 8.0s = 4.0 NPS
    assert round(features.peak_4m_nps, 2) == 4.0


def test_extract_beatmap_features_empty():
    bm = Beatmap7K(title="Empty", mode=3, circle_size=7)
    features = extract_beatmap_features(bm)
    assert features.total_notes == 0
    assert features.avg_nps == 0.0
    assert features.peak_4m_nps == 0.0
    assert features.hold_pct == 0.0
    assert features.duration_seconds == 0.0


def test_extract_beatmap_features_short():
    # Only 2 measures, 120 BPM
    tp = TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)
    hit_objects = [
        HitObject(column=0, time=0.0, note_type=NoteType.RICE),
        HitObject(column=1, time=1000.0, note_type=NoteType.RICE),
        HitObject(column=2, time=2000.0, note_type=NoteType.RICE),
    ]
    bm = Beatmap7K(title="Short", mode=3, circle_size=7, timing_points=[tp], hit_objects=hit_objects)
    features = extract_beatmap_features(bm)
    assert features.total_notes == 3
    # Duration: 0.0 to 2000.0 = 2.0s -> avg_nps = 3 / 2.0 = 1.5
    assert features.avg_nps == 1.5
    # Less than 4 measures -> fallback to avg_nps
    assert features.peak_4m_nps == 1.5


def test_extract_hand_topology_gap1_and_adj():
    # 4-second sequence with explicit chord topologies
    # t=0: [0, 2] -> L gap:1 (1 gap1)
    # t=1000: [4, 5] -> R adj (1 adj)
    # t=2000: [0, 1, 4, 6] -> L adj + R gap:1 (1 adj, 1 gap1)
    # t=3000: [1, 2, 5, 6] -> L adj + R adj (2 adj)
    # t=4000: [3] -> Space only
    hit_objects = [
        HitObject(column=0, time=0.0, note_type=NoteType.RICE),
        HitObject(column=2, time=0.0, note_type=NoteType.RICE),
        HitObject(column=4, time=1000.0, note_type=NoteType.RICE),
        HitObject(column=5, time=1000.0, note_type=NoteType.RICE),
        HitObject(column=0, time=2000.0, note_type=NoteType.RICE),
        HitObject(column=1, time=2000.0, note_type=NoteType.RICE),
        HitObject(column=4, time=2000.0, note_type=NoteType.RICE),
        HitObject(column=6, time=2000.0, note_type=NoteType.RICE),
        HitObject(column=1, time=3000.0, note_type=NoteType.RICE),
        HitObject(column=2, time=3000.0, note_type=NoteType.RICE),
        HitObject(column=5, time=3000.0, note_type=NoteType.RICE),
        HitObject(column=6, time=3000.0, note_type=NoteType.RICE),
        HitObject(column=3, time=4000.0, note_type=NoteType.RICE),
    ]
    bm = Beatmap7K(title="Topology Test", mode=3, circle_size=7, hit_objects=hit_objects)
    features = extract_beatmap_features(bm)

    assert features.duration_seconds == 4.0
    assert features.gap1_count == 2
    assert features.gap1_density == 0.5
    assert features.adj_count == 4
    assert features.adj_density == 1.0


def test_extract_degree_of_freedom_suppression():
    # 0ms to 1000ms (101 samples at 10ms step: 0, 10, ..., 1000)
    # 0 ~ 500ms: 2 LNs active on cols 0, 1 -> 50 samples of lock=2
    # 500 ~ 1000ms: 1 LN active on col 2 -> 50 samples of lock=1
    # 1000ms: 1 sample of lock=0 (rice on col 3)
    hit_objects = [
        HitObject(column=0, time=0.0, note_type=NoteType.LN, end_time=500.0),
        HitObject(column=1, time=0.0, note_type=NoteType.LN, end_time=500.0),
        HitObject(column=2, time=500.0, note_type=NoteType.LN, end_time=1000.0),
        HitObject(column=3, time=1000.0, note_type=NoteType.RICE),
    ]
    bm = Beatmap7K(title="Lockout Test", mode=3, circle_size=7, hit_objects=hit_objects)
    features = extract_beatmap_features(bm)

    expected_mean = round(150.0 / 101.0, 4)  # ~1.4851
    assert features.mean_locked_fingers == expected_mean

    profile = features.lockout_profile
    assert len(profile) == 8
    assert profile[0] == round((1.0 / 101.0) * 100.0, 4)
    assert profile[1] == round((50.0 / 101.0) * 100.0, 4)
    assert profile[2] == round((50.0 / 101.0) * 100.0, 4)
    for k in range(3, 8):
        assert profile[k] == 0.0


def test_extract_antiphase_articulation():
    # 0ms to 2000ms (2.0s duration)
    # t=0: 2 LNs start on cols 0, 1 (end at 1000ms)
    # t=1000: cols 0, 1 release (2 notes) while Col 2 (Rice) and Col 4 (LN 1000~2000) press (2 notes).
    #   Under exclusive physical matching (ADR-0008), min(|R|, |P|) = min(2, 2) = 2 antiphase events
    # t=2000: Col 4 LN releases and Col 4 Rice presses (same track overlap -> 0 antiphase)
    hit_objects = [
        HitObject(column=0, time=0.0, note_type=NoteType.LN, end_time=1000.0),
        HitObject(column=1, time=0.0, note_type=NoteType.LN, end_time=1000.0),
        HitObject(column=2, time=1000.0, note_type=NoteType.RICE),
        HitObject(column=4, time=1000.0, note_type=NoteType.LN, end_time=2000.0),
        HitObject(column=4, time=2000.0, note_type=NoteType.RICE),
    ]
    bm = Beatmap7K(title="Antiphase Test", mode=3, circle_size=7, hit_objects=hit_objects)
    features = extract_beatmap_features(bm)

    assert features.duration_seconds == 2.0
    assert features.antiphase_count == 2
    assert features.antiphase_rate == 1.0


def test_extract_antiphase_concurrent_chord_no_cartesian_explosion():
    """
    Unit test for SPEC-P2.2-02 / ADR-0008:
    Verify that multi-note chord transitions do not suffer from Cartesian O(R x P) explosion.
    4 LNs releasing while 3 notes press should yield min(4, 3) = 3 antiphase events, NOT 12.
    """
    hit_objects = [
        HitObject(column=0, time=0.0, note_type=NoteType.LN, end_time=1000.0),
        HitObject(column=1, time=0.0, note_type=NoteType.LN, end_time=1000.0),
        HitObject(column=2, time=0.0, note_type=NoteType.LN, end_time=1000.0),
        HitObject(column=3, time=0.0, note_type=NoteType.LN, end_time=1000.0),
        HitObject(column=4, time=1000.0, note_type=NoteType.RICE),
        HitObject(column=5, time=1000.0, note_type=NoteType.RICE),
        HitObject(column=6, time=1000.0, note_type=NoteType.RICE),
    ]
    bm = Beatmap7K(title="Cartesian Fix Test", mode=3, circle_size=7, hit_objects=hit_objects)
    features = extract_beatmap_features(bm)
    assert features.antiphase_count == 3
