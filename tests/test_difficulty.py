import json
from pathlib import Path
import pytest
from proj7k.difficulty import (
    DifficultyOptions,
    IntrinsicDifficultyResult,
    evaluate_intrinsic_difficulty,
    main as difficulty_cli_main,
)
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint


def _make_dummy_beatmap() -> Beatmap7K:
    bm = Beatmap7K(
        title="Test Song",
        artist="Test Artist",
        creator="Mapper",
        version="Hard",
        timing_points=[TimingPoint(time=0.0, beat_length=400.0)],
        hit_objects=[
            HitObject(column=i % 7, time=i * 100.0, note_type=NoteType.RICE)
            for i in range(50)
        ],
    )
    return bm


def _make_dummy_osu_content() -> str:
    lines = [
        "osu file format v14",
        "[General]",
        "Mode: 3",
        "[Metadata]",
        "Title:Test Song",
        "Artist:Test Artist",
        "Creator:Mapper",
        "Version:Hard",
        "[Difficulty]",
        "CircleSize:7",
        "[TimingPoints]",
        "0,400,4,2,0,50,1,0",
        "[HitObjects]",
    ]
    for i in range(50):
        col_x = [36, 109, 182, 256, 329, 402, 475][i % 7]
        t = i * 100
        lines.append(f"{col_x},192,{t},1,0,0:0:0:0:")
    return "\n".join(lines)


def test_evaluate_intrinsic_difficulty_from_beatmap():
    bm = _make_dummy_beatmap()
    res = evaluate_intrinsic_difficulty(bm)
    assert isinstance(res, IntrinsicDifficultyResult)
    assert res.star_rating > 0.0
    assert res.raw_star_rating > 0.0
    assert res.metadata["title"] == "Test Song"
    assert res.metadata["total_notes"] == 50

    d = res.to_dict()
    assert "star_rating" in d
    assert "raw_star_rating" in d
    assert "radar" in d
    assert "strain_profile" in d
    assert "metadata" in d
    # JSON serializable
    json_str = json.dumps(d)
    assert len(json_str) > 0


def test_evaluate_intrinsic_difficulty_from_content_str():
    content = _make_dummy_osu_content()
    res = evaluate_intrinsic_difficulty(content)
    assert isinstance(res, IntrinsicDifficultyResult)
    assert res.star_rating > 0.0
    assert res.metadata["creator"] == "Mapper"


def test_evaluate_intrinsic_difficulty_from_path(tmp_path):
    f_path = tmp_path / "test.osu"
    f_path.write_text(_make_dummy_osu_content(), encoding="utf-8")

    # Pass as Path
    res_path = evaluate_intrinsic_difficulty(f_path)
    assert isinstance(res_path, IntrinsicDifficultyResult)
    assert res_path.star_rating > 0.0

    # Pass as str path
    res_str = evaluate_intrinsic_difficulty(str(f_path))
    assert res_str.star_rating == res_path.star_rating


def test_evaluate_intrinsic_difficulty_invalid_type():
    with pytest.raises(TypeError, match="Expected str, Path, or Beatmap7K"):
        evaluate_intrinsic_difficulty(12345)  # type: ignore


def test_cli_execution(tmp_path, capsys):
    f_path = tmp_path / "test.osu"
    f_path.write_text(_make_dummy_osu_content(), encoding="utf-8")

    # Test default human readable summary
    code = difficulty_cli_main([str(f_path)])
    assert code == 0
    out, err = capsys.readouterr()
    assert "PROJ7K INTRINSIC DIFFICULTY REPORT" in out
    assert "Star Rating" in out

    # Test --json flag
    code_json = difficulty_cli_main([str(f_path), "--json"])
    assert code_json == 0
    out_json, _ = capsys.readouterr()
    data = json.loads(out_json)
    assert "star_rating" in data
    assert "radar" in data

    # Test nonexistent file
    code_err = difficulty_cli_main(["nonexistent.osu"])
    assert code_err == 1
    _, err_msg = capsys.readouterr()
    assert "Error: File not found" in err_msg

    # Test error handling during evaluation
    bad_file = tmp_path / "bad.osu"
    bad_file.write_text("invalid content without osu header", encoding="utf-8")
    code_bad = difficulty_cli_main([str(bad_file)])
    assert code_bad in (0, 1)  # if parser handles it or raises
