import pytest
from proj7k.slicer import parse_timestamp, main

def test_parse_timestamp():
    assert parse_timestamp("1000") == 1000.0
    assert parse_timestamp("1000.5") == 1000.5
    assert parse_timestamp("2.5s") == 2500.0
    assert parse_timestamp("01:30") == 90000.0
    assert parse_timestamp("01:30.500") == 90500.0
    assert parse_timestamp("02:15:30") == 8130000.0

def test_cli_end_to_end(tmp_path):
    osu_content = """osu file format v14

[General]
Mode: 3

[Difficulty]
CircleSize: 7
OverallDifficulty: 8

[TimingPoints]
0,500,4,2,1,60,1,0

[HitObjects]
36,192,1000,1,0,0:0:0:0:
109,192,1500,128,0,2500:0:0:0:0:
"""
    osu_file = tmp_path / "test.osu"
    osu_file.write_text(osu_content, encoding="utf-8")
    
    out_img = tmp_path / "out.png"
    
    # Run main with arguments
    args = [
        "--osu", str(osu_file),
        "--start", "500",
        "--end", "3000",
        "-o", str(out_img),
    ]
    ret = main(args)
    assert ret == 0
    assert out_img.exists()
    assert out_img.stat().st_size > 0
