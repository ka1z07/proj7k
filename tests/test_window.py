import pytest
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.window import extract_time_window, SliceWindow

@pytest.fixture
def sample_beatmap():
    # BPM = 120 (beat_length = 500ms, meter = 4 -> 1 measure = 2000ms)
    tp = TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)
    
    # Notes:
    # h1: Rice at 500 (before window 1000..3000)
    # h2: Rice at 1500 (inside window)
    # h3: LN from 800 to 1800 (starts before, ends inside -> OVERLAPS)
    # h4: LN from 2500 to 3500 (starts inside, ends after -> OVERLAPS)
    # h5: Rice at 3500 (after window)
    objects = [
        HitObject(column=0, time=500.0, note_type=NoteType.RICE),
        HitObject(column=1, time=1500.0, note_type=NoteType.RICE),
        HitObject(column=2, time=800.0, note_type=NoteType.LN, end_time=1800.0),
        HitObject(column=3, time=2500.0, note_type=NoteType.LN, end_time=3500.0),
        HitObject(column=4, time=3500.0, note_type=NoteType.RICE),
    ]
    return Beatmap7K(
        title="Window Test",
        timing_points=[tp],
        hit_objects=objects
    )

def test_extract_window_filters_objects_correctly(sample_beatmap):
    window = extract_time_window(sample_beatmap, start_ms=1000.0, end_ms=3000.0)
    
    assert window.start_ms == 1000.0
    assert window.end_ms == 3000.0
    
    # Should include h2 (Rice 1500), h3 (LN 800-1800), h4 (LN 2500-3500)
    # Should NOT include h1 (500) or h5 (3500)
    assert len(window.hit_objects) == 3
    times = [(ho.time, ho.end_time) for ho in window.hit_objects]
    assert (1500.0, None) in times
    assert (800.0, 1800.0) in times
    assert (2500.0, 3500.0) in times

def test_extract_window_computes_barlines(sample_beatmap):
    # 0..2000 is measure 0; 2000..4000 is measure 1;
    # beats at 0, 500, 1000, 1500, 2000, 2500, 3000, 3500...
    window = extract_time_window(sample_beatmap, start_ms=1000.0, end_ms=3000.0)
    
    # Measure lines within [1000, 3000]: 2000 (measure 1 start)
    measure_lines = [b for b in window.barlines if b.is_measure_start]
    assert len(measure_lines) == 1
    assert measure_lines[0].time == 2000.0
    assert measure_lines[0].measure_index == 1

    # Beat lines within [1000, 3000]: 1000, 1500, 2000, 2500, 3000
    beat_times = [b.time for b in window.barlines]
    assert 1000.0 in beat_times
    assert 1500.0 in beat_times
    assert 2000.0 in beat_times
    assert 2500.0 in beat_times
    assert 3000.0 in beat_times


def test_parse_measure_arg():
    from proj7k.window import parse_measure_arg
    assert parse_measure_arg("12-16") == (12, 16)
    assert parse_measure_arg("M12-M16") == (12, 16)
    assert parse_measure_arg("12") == (12, 13)
    assert parse_measure_arg("m5") == (5, 6)
