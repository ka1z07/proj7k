import pytest
from proj7k.parser import parse_osu_7k, HitObject, NoteType, TimingPoint

SAMPLE_7K_OSU = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 3

[Metadata]
Title:Test Song
Artist:Test Artist
Creator:Jinjin
Version:7K Dan 1st

[Difficulty]
CircleSize:7
OverallDifficulty:8

[TimingPoints]
1000,500,4,2,1,60,1,0
2000,-100,4,2,1,60,0,0

[HitObjects]
36,192,1000,1,0,0:0:0:0:
109,192,1250,1,0,0:0:0:0:
182,192,1500,128,0,2500:0:0:0:0:
256,192,2000,1,0,0:0:0:0:
"""

def test_parse_valid_7k_osu():
    bm = parse_osu_7k(SAMPLE_7K_OSU)
    assert bm.title == "Test Song"
    assert bm.artist == "Test Artist"
    assert bm.mode == 3
    assert bm.circle_size == 7
    assert bm.overall_difficulty == 8.0

    # Timing points
    assert len(bm.timing_points) == 2
    tp1 = bm.timing_points[0]
    assert tp1.time == 1000.0
    assert tp1.beat_length == 500.0
    assert tp1.uninherited is True
    assert tp1.bpm == 120.0  # 60000 / 500

    tp2 = bm.timing_points[1]
    assert tp2.time == 2000.0
    assert tp2.uninherited is False

    # Hit objects
    assert len(bm.hit_objects) == 4
    # Column formula: floor(x * 7 / 512)
    # x=36 -> 36 * 7 / 512 = 0.492 -> 0
    # x=109 -> 109 * 7 / 512 = 1.490 -> 1
    # x=182 -> 182 * 7 / 512 = 2.488 -> 2
    # x=256 -> 256 * 7 / 512 = 3.5 -> 3
    h1 = bm.hit_objects[0]
    assert h1.column == 0
    assert h1.time == 1000.0
    assert h1.note_type == NoteType.RICE

    h2 = bm.hit_objects[1]
    assert h2.column == 1
    assert h2.time == 1250.0
    assert h2.note_type == NoteType.RICE

    h3 = bm.hit_objects[2]
    assert h3.column == 2
    assert h3.time == 1500.0
    assert h3.end_time == 2500.0
    assert h3.note_type == NoteType.LN

    h4 = bm.hit_objects[3]
    assert h4.column == 3
    assert h4.time == 2000.0
    assert h4.note_type == NoteType.RICE

def test_parse_rejects_non_7k_or_non_mania():
    osu_4k = SAMPLE_7K_OSU.replace("CircleSize:7", "CircleSize:4")
    with pytest.raises(ValueError, match="Expected 7K"):
        parse_osu_7k(osu_4k)

    osu_std = SAMPLE_7K_OSU.replace("Mode: 3", "Mode: 0")
    with pytest.raises(ValueError, match="Expected Mode 3"):
        parse_osu_7k(osu_std)
