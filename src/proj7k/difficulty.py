"""
Top-level intrinsic difficulty evaluation engine and CLI for osu!mania 7K.

Provides the single top-level entrypoint `evaluate_intrinsic_difficulty` and
command-line tool `python3 -m proj7k.difficulty <path>`.
"""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Union

from proj7k.features import extract_beatmap_features
from proj7k.parser import Beatmap7K, parse_osu_7k
from proj7k.radar import RadarOptions, TechniqueRadar, compute_technique_radar
from proj7k.rating import RatingOptions, synthesize_star_rating
from proj7k.strain import StrainOptions, StrainTimeseriesProfile, compute_dual_hand_strain


@dataclass(frozen=True)
class DifficultyOptions:
    strain_options: Optional[StrainOptions] = None
    radar_options: Optional[RadarOptions] = None
    rating_options: Optional[RatingOptions] = None


@dataclass(frozen=True)
class IntrinsicDifficultyResult:
    star_rating: float
    raw_star_rating: float
    radar: TechniqueRadar
    strain_profile: StrainTimeseriesProfile
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "star_rating": self.star_rating,
            "raw_star_rating": self.raw_star_rating,
            "radar": self.radar.to_dict(),
            "strain_profile": self.strain_profile.to_dict(),
            "metadata": self.metadata,
        }


def evaluate_intrinsic_difficulty(
    content_or_path: Union[str, Path, Beatmap7K],
    options: Optional[DifficultyOptions] = None,
) -> IntrinsicDifficultyResult:
    """
    Evaluates the intrinsic difficulty of an osu!mania 7K beatmap.

    Accepts file path (str or Path), raw .osu string content, or a Beatmap7K instance.
    Returns the complete IntrinsicDifficultyResult contract.
    """
    if options is None:
        options = DifficultyOptions()

    if isinstance(content_or_path, Beatmap7K):
        beatmap = content_or_path
    elif isinstance(content_or_path, Path):
        beatmap = parse_osu_7k(str(content_or_path))
    elif isinstance(content_or_path, str):
        beatmap = parse_osu_7k(content_or_path)
    else:
        raise TypeError(f"Expected str, Path, or Beatmap7K, got {type(content_or_path).__name__}")

    features = extract_beatmap_features(beatmap)
    strain_profile = compute_dual_hand_strain(beatmap, options=options.strain_options)
    radar = compute_technique_radar(
        beatmap,
        features=features,
        strain_profile=strain_profile,
        options=options.radar_options,
    )
    synthesis = synthesize_star_rating(
        radar,
        p90_strain=strain_profile.p90_strain,
        options=options.rating_options,
    )

    metadata: Dict[str, Any] = {
        "title": beatmap.title,
        "artist": beatmap.artist,
        "creator": beatmap.creator,
        "version": beatmap.version,
        "total_notes": features.total_notes,
        "hold_pct": features.hold_pct,
        "duration_seconds": features.duration_seconds,
        "avg_nps": features.avg_nps,
        "dominant_technique": synthesis.dominant_technique,
        "dominant_score": synthesis.dominant_score,
        "synergy_bonus": synthesis.synergy_bonus,
    }

    return IntrinsicDifficultyResult(
        star_rating=synthesis.star_rating,
        raw_star_rating=synthesis.uncompressed_rating,
        radar=radar,
        strain_profile=strain_profile,
        metadata=metadata,
    )


def format_cli_summary(res: IntrinsicDifficultyResult) -> str:
    """Formats a human-readable ANSI terminal overview."""
    meta = res.metadata
    title = f"{meta.get('artist', 'Unknown')} - {meta.get('title', 'Unknown')} [{meta.get('version', '7K')}]"
    r = res.radar

    lines = [
        "=" * 60,
        f" \033[1mPROJ7K INTRINSIC DIFFICULTY REPORT\033[0m",
        "=" * 60,
        f" Song       : \033[36m{title}\033[0m",
        f" Creator    : {meta.get('creator', 'Unknown')}",
        f" Notes      : {meta.get('total_notes', 0)} (LN: {meta.get('hold_pct', 0.0):.1f}%) | NPS: {meta.get('avg_nps', 0.0):.2f}",
        "-" * 60,
        f" \033[1;33m★ Star Rating\033[0m: \033[1;32m{res.star_rating:.2f}★\033[0m (Uncompressed: {res.raw_star_rating:.2f}★)",
        f" Dominance   : \033[35m{r.dominant_technique}\033[0m ({r.dominant_score:.2f}★, Synergy: +{meta.get('synergy_bonus', 0.0):.2f}★)",
        "-" * 60,
        " 8-Dimension Technique Radar:",
        f"   Jack       : {r.jack:5.2f}★    LN General : {r.ln_general:5.2f}★",
        f"   Tech       : {r.tech:5.2f}★    LN Tech    : {r.ln_tech:5.2f}★",
        f"   Speed      : {r.speed:5.2f}★    LN Inverse : {r.ln_inverse:5.2f}★",
        f"   Stream     : {r.stream:5.2f}★    LN Release : {r.ln_release:5.2f}★",
        "-" * 60,
        f" Strain Profile: P90={res.strain_profile.p90_strain:.2f} | Peak={res.strain_profile.peak_strain:.2f}",
        "=" * 60,
    ]
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m proj7k.difficulty",
        description="Evaluate intrinsic difficulty and 8D technique radar for an osu!mania 7K chart.",
    )
    parser.add_argument("path", help="Path to .osu beatmap file")
    parser.add_argument("--json", action="store_true", help="Output full evaluation as JSON")

    args = parser.parse_args(argv)

    if not os.path.exists(args.path):
        sys.stderr.write(f"Error: File not found: {args.path}\n")
        return 1

    try:
        result = evaluate_intrinsic_difficulty(args.path)
    except Exception as e:
        sys.stderr.write(f"Error evaluating beatmap: {e}\n")
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_cli_summary(result))

    return 0


if __name__ == "__main__":
    sys.exit(main())
