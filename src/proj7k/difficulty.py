"""
The single-chart command line: one `.osu` file in, the spec v0.2 engine's reading out.

    PYTHONPATH=src python3 -m proj7k.difficulty chart.osu [--json]

Stars, tier and the eight skills are the engine's (`engine.evaluate_osu`, ADR-0017); the hardest
stretches are its difficulty field's (`field.ChartField.hot_spots`, ADR-0020). Note count, LN share
and density are the raw features', shown for orientation only.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from proj7k.dan import estimate_canonical_dan
from proj7k.engine import engine_version
from proj7k.engine.skills import SKILL_TECH_KEY
from proj7k.features import extract_beatmap_features
from proj7k.field import trace_osu
from proj7k.parser import parse_osu_7k

#: The row each skill is printed on, left column then right.
_SKILL_LABEL = {
    "rc_jack": "Jack", "rc_tech": "Tech", "rc_speed": "Speed", "rc_stamina": "Stream",
    "ln_general": "LN General", "ln_tech": "LN Tech", "ln_inverse": "LN Inverse", "ln_release": "LN Release",
}
_ROWS = (("rc_jack", "ln_general"), ("rc_tech", "ln_tech"), ("rc_speed", "ln_inverse"), ("rc_stamina", "ln_release"))

#: How many of the hardest stretches the report lists.
HOT_SPOTS = 3


def evaluate_chart(content: str) -> Dict[str, Any]:
    """Everything the report prints, as one JSON-ready dictionary."""
    field = trace_osu(content)
    profile = field.profile
    beatmap = parse_osu_7k(content)
    features = extract_beatmap_features(beatmap)
    return {
        "engine_version": engine_version(),
        "metadata": {
            "title": beatmap.title,
            "artist": beatmap.artist,
            "creator": beatmap.creator,
            "version": beatmap.version,
            "total_notes": features.total_notes,
            "hold_pct": features.hold_pct,
            "avg_nps": features.avg_nps,
            "duration_seconds": features.duration_seconds,
        },
        "star_rating": profile.total_stars,
        "dan_tier": estimate_canonical_dan(profile.total_stars),
        "dominant_technique": SKILL_TECH_KEY[profile.dominant_skill],
        "profile": profile.to_dict(),
        "hot_spots": [h.to_dict() for h in field.hot_spots(top=HOT_SPOTS)],
    }


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"


def format_cli_summary(report: Dict[str, Any]) -> str:
    """A human-readable terminal overview of `evaluate_chart`'s result."""
    meta = report["metadata"]
    skills = report["profile"]["skills"]
    dominant = report["profile"]["dominant_skill"]
    title = f"{meta['artist'] or 'Unknown'} - {meta['title'] or 'Unknown'} [{meta['version'] or '7K'}]"

    lines = [
        "=" * 60,
        " \033[1mPROJ7K INTRINSIC DIFFICULTY REPORT\033[0m",
        "=" * 60,
        f" Song       : \033[36m{title}\033[0m",
        f" Creator    : {meta['creator'] or 'Unknown'}",
        f" Notes      : {meta['total_notes']} (LN: {meta['hold_pct']:.1f}%) | NPS: {meta['avg_nps']:.2f}",
        "-" * 60,
        f" \033[1;33m★ Star Rating\033[0m: \033[1;32m{report['star_rating']:.2f}★\033[0m ({report['dan_tier']} Dan)",
        f" Dominance   : \033[35m{report['dominant_technique']}\033[0m ({skills[dominant]['stars']:.2f}★)",
        "-" * 60,
        " 8-Skill Technique Radar:",
    ]
    for left, right in _ROWS:
        lines.append(
            f"   {_SKILL_LABEL[left]:<10} : {skills[left]['stars']:5.2f}★    "
            f"{_SKILL_LABEL[right]:<10} : {skills[right]['stars']:5.2f}★"
        )
    if report["hot_spots"]:
        lines.append("-" * 60)
        lines.append(" Hardest stretches:")
        for h in report["hot_spots"]:
            lines.append(
                f"   {_clock(h['start_s'])} - {_clock(h['end_s'])}  "
                f"{h['share'] * 100:4.1f}% of the risk, carried by {SKILL_TECH_KEY[h['skill']]}"
            )
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.difficulty",
        description="Evaluate the intrinsic difficulty and the eight skills of an osu!mania 7K chart.",
    )
    parser.add_argument("path", help="Path to .osu beatmap file")
    parser.add_argument("--json", action="store_true", help="Output the full evaluation as JSON")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        sys.stderr.write(f"Error: File not found: {args.path}\n")
        return 1

    try:
        report = evaluate_chart(path.read_text(encoding="utf-8"))
    except Exception as e:
        sys.stderr.write(f"Error evaluating beatmap: {e}\n")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_cli_summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
