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
