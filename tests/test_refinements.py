import pytest
from proj7k.parser import parse_osu_7k, Beatmap7K, TimingPoint, HitObject, NoteType
from proj7k.window import extract_time_window, extract_measure_window
from proj7k.slicer import parse_timestamp, main
from proj7k.renderer import format_timestamp, ScrollDirection, render_slice, RenderOptions

def test_parser_raises_file_not_found_for_missing_file():
    with pytest.raises(FileNotFoundError):
        parse_osu_7k("non_existent_chart.osu")

def test_window_accumulates_measures_across_bpm_changes():
    # Segment 1: at 0ms, 120BPM (500ms beat, 4 beats = 2000ms measure)
    # Segment 2: at 4000ms (after 2 full measures: M1, M2), 150BPM (400ms beat, 4 beats = 1600ms measure)
    tp1 = TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)
    tp2 = TimingPoint(time=4000.0, beat_length=400.0, meter=4, uninherited=True)
    bm = Beatmap7K(title="BPM Change", timing_points=[tp1, tp2])

    window = extract_time_window(bm, start_ms=0.0, end_ms=8000.0)
    measure_lines = [b for b in window.barlines if b.is_measure_start]
    
    # Measure numbers should increment continuously (1, 2, 3, 4, 5...)
    measure_indices = [b.measure_index for b in measure_lines]
    assert measure_indices == [0, 1, 2, 3, 4]
    
    # Check no duplicate barlines
    bar_times = [b.time for b in window.barlines]
    assert len(bar_times) == len(set(bar_times))

def test_extract_measure_window():
    tp = TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True) # 2000ms per measure
    bm = Beatmap7K(title="Measure Window", timing_points=[tp])
    
    # Measure 1 to 3 (0-indexed measure 1 to 2 -> 2000ms to 6000ms)
    window = extract_measure_window(bm, start_measure=1, end_measure=3)
    assert window.start_ms == 2000.0
    assert window.end_ms == 6000.0

def test_format_timestamp():
    assert format_timestamp(0) == "00:00.00"
    assert format_timestamp(65500) == "01:05.50"
    assert format_timestamp(3661250) == "01:01:01.25"
