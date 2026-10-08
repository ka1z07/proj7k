"""
The single-chart command line (`python3 -m proj7k.difficulty`): its numbers are the engine's.
"""

import json
from pathlib import Path

from proj7k.dan import estimate_canonical_dan
from proj7k.difficulty import evaluate_chart, format_cli_summary, main
from proj7k.engine import evaluate_osu

SAMPLE = Path(__file__).resolve().parents[1] / "docs" / "sample_7k.osu"


def test_the_report_is_the_engines_reading():
    content = SAMPLE.read_text(encoding="utf-8")
    report = evaluate_chart(content)
    profile = evaluate_osu(content)

    assert report["star_rating"] == profile.total_stars
    assert report["profile"] == profile.to_dict()
    assert report["dan_tier"] == estimate_canonical_dan(profile.total_stars)
    assert report["metadata"]["total_notes"] == 43
    assert 1 <= len(report["hot_spots"]) <= 3


def test_the_summary_names_every_skill_and_the_star_rating():
    report = evaluate_chart(SAMPLE.read_text(encoding="utf-8"))
    text = format_cli_summary(report)
    for label in ("Jack", "Tech", "Speed", "Stream", "LN General", "LN Tech", "LN Inverse", "LN Release"):
        assert label in text
    assert f"{report['star_rating']:.2f}★" in text


def test_json_output_and_a_missing_file(capsys, tmp_path):
    assert main([str(SAMPLE), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["profile"]["skills"]) == {
        "rc_jack", "rc_tech", "rc_speed", "rc_stamina", "ln_general", "ln_tech", "ln_inverse", "ln_release",
    }
    assert main([str(tmp_path / "missing.osu")]) == 1
