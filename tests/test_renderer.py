import pytest
from PIL import Image
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.window import extract_time_window
from proj7k.renderer import render_slice, RenderOptions

@pytest.fixture
def sample_window():
    tp = TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)
    objects = [
        HitObject(column=0, time=1000.0, note_type=NoteType.RICE),
        HitObject(column=1, time=1250.0, note_type=NoteType.RICE),
        HitObject(column=2, time=1500.0, note_type=NoteType.LN, end_time=2500.0),
        HitObject(column=3, time=2000.0, note_type=NoteType.RICE), # Center lane
        HitObject(column=6, time=2250.0, note_type=NoteType.RICE),
    ]
    bm = Beatmap7K(title="Render Test", timing_points=[tp], hit_objects=objects)
    return extract_time_window(bm, start_ms=1000.0, end_ms=3000.0)

def test_render_slice_generates_image_with_expected_dimensions(sample_window):
    opts = RenderOptions(
        pixels_per_second=500.0, # 2.0 seconds -> 1000 px height for time span
        column_width=40,        # 7 cols -> 280 px
        margin_left=60,
        margin_right=20,
        margin_top=30,
        margin_bottom=30,
    )
    img = render_slice(sample_window, opts)
    
    assert isinstance(img, Image.Image)
    assert img.mode == "RGB"
    expected_w = 60 + (7 * 40) + 20 # 360 px
    expected_h = 30 + 1000 + 30     # 1060 px
    assert img.size == (expected_w, expected_h)

def test_render_slice_contains_non_background_pixels(sample_window):
    opts = RenderOptions(pixels_per_second=400.0)
    img = render_slice(sample_window, opts)
    
    # Background is dark, notes and barlines should produce brighter pixels
    extrema = img.convert("L").getextrema()
    assert extrema[0] < 50  # dark background present
    assert extrema[1] > 200 # bright notes or barlines present
