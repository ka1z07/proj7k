"""
Top-level intrinsic difficulty evaluation engine and CLI for osu!mania 7K.

Provides the single top-level entrypoint `evaluate_intrinsic_difficulty` and
command-line tool `python3 -m proj7k.difficulty <path>`.
"""

import argparse
import ast
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Sequence, Tuple, Union

from proj7k import physics, scaling, strain
from proj7k.calibration import block_fingerprint_constants, compute_methodology_fingerprint
from proj7k.features import BeatmapFeatures, FeatureOptions, extract_beatmap_features
from proj7k.parser import Beatmap7K, parse_osu_7k
from proj7k.radar import (
    RadarOptions,
    RawTechniqueDrivers,
    TechniqueRadar,
    compute_raw_technique_drivers,
    compute_technique_radar,
)
from proj7k.rating import RatingOptions, synthesize_star_rating
from proj7k.strain import StrainOptions, StrainTimeseriesProfile, compute_dual_hand_strain


#: The modules whose arithmetic decides a chart's star rating, and which are therefore held to
#: the literal registry. `parser` and `window` are here because they shape the note stream and
#: the beat grid the operators read; changing either moves ratings on every chart.
#:
#: This one list is the definition of "the rating path" for both mechanisms that need one:
#: `tests/test_engine_literal_registry.py` scans exactly these files for unregistered literals,
#: and `rating_path_source_digest` hashes exactly these files into the engine version.
RATING_PATH_MODULES: Tuple[str, ...] = (
    "calibration.py",
    "features.py",
    "parser.py",
    "radar.py",
    "rating.py",
    "scaling.py",
    "strain.py",
    "window.py",
)


def _strip_docstrings(node: ast.AST) -> None:
    """Removes the docstring statement of every module, class and function, in place."""
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for child in ast.walk(node):
        if not isinstance(child, holders):
            continue
        body = child.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            del body[0]


def _source_digest(paths: Sequence[Path]) -> str:
    """
    Digest of Python source that ignores comments, docstrings and layout but sees every
    expression.

    Hashing the raw bytes would be simpler and would also close the gap; parsing first is what
    keeps a reworded comment from marking every injected beatmap stale and rewriting its
    difficulty name for nothing.
    """
    digest = hashlib.sha256()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _strip_docstrings(tree)
        digest.update(path.name.encode("utf-8"))
        digest.update(ast.dump(tree, include_attributes=False).encode("utf-8"))
    return digest.hexdigest()


@lru_cache(maxsize=None)
def rating_path_source_digest() -> str:
    """
    Digest of the rating path's own source, folded into the engine version.

    The methodology fingerprint summarizes the engine's *constants*, and the guards around it
    (ADR-0014 §2, issue #48) keep that coverage complete: every option field, every named
    constant block, every numeric literal left in a function body. None of them can see a change
    that moves star ratings without introducing or moving a constant — swapping which tempo an
    operator reads, say. Issue #51 did exactly that: 58 of the 120 benchmark charts moved while
    the version stayed put, so an injection carrying the old token would have been read as
    current and kept its stale rating indefinitely. That is the failure ADR-0014 exists to
    prevent, and this digest is what closes it.

    Scope is `RATING_PATH_MODULES` — the same set the literal registry scans, so "a module whose
    edits can move a star rating" has exactly one definition.
    """
    module_dir = Path(__file__).resolve().parent
    return _source_digest([module_dir / name for name in RATING_PATH_MODULES])


@dataclass(frozen=True)
class DifficultyOptions:
    strain_options: Optional[StrainOptions] = None
    radar_options: Optional[RadarOptions] = None
    rating_options: Optional[RatingOptions] = None
    feature_options: Optional[FeatureOptions] = None

    @property
    def engine_fingerprint(self) -> str:
        """
        Methodology hash of every constant that can move a star rating, taken from the four
        objects that hold them: the star calibration and aggregation, the radar, strain and
        feature option defaults, and the named constants of the `physics`, `scaling` and
        `strain` calibration blocks. Changing any one of them changes the version stamped into
        osu!lazer metadata, which marks every previously injected beatmap for re-evaluation
        (ADR-0014) — so a formula change can never leave stale ratings behind.

        Coverage is not a hand-kept list. Each option object is folded in whole via `asdict`, so
        a new field is covered the moment it is declared, and each calibration block is read
        through `calibration.block_fingerprint_constants`, which takes the block's own
        `CALIBRATION_CONSTANTS` list. Two tests hold that up: every field of every option object
        must move this hash, and every numeric literal left in the rating path's function bodies
        must be a registered non-calibration literal (test_engine_literal_registry) — which also
        asserts each block's list is complete against its module's source. A constant that
        reaches a star rating without reaching this hash has nowhere left to hide.

        Constants are the whole story only for changes that introduce one. `rating_path` folds in
        the digest of the rating path's own source, which is what catches a change that moves star
        ratings without touching any constant (issue #51).
        """
        return compute_methodology_fingerprint(
            rating_options=asdict(self.rating_options or RatingOptions()),
            radar_options=asdict(self.radar_options or RadarOptions()),
            strain_options=asdict(self.strain_options or StrainOptions()),
            feature_options=asdict(self.feature_options or FeatureOptions()),
            physics=block_fingerprint_constants(physics),
            scaling=block_fingerprint_constants(scaling),
            strain_constants=block_fingerprint_constants(strain),
            rating_path=rating_path_source_digest(),
        )


@lru_cache(maxsize=None)
def current_engine_version() -> str:
    """
    Algorithm version of the engine's default configuration, derived entirely from its
    calibration constants: changing any constant changes this string (see
    `DifficultyOptions.engine_fingerprint`).

    Format: 'v'-prefixed 8-hex-char methodology hash, e.g. 'v1a2b3c4d'. Stamped into injected
    difficulty names so stale injections are identifiable after a formula change (ADR-0014).
    """
    return f"v{DifficultyOptions().engine_fingerprint}"


@dataclass(frozen=True)
class IntrinsicDifficultyResult:
    star_rating: float
    raw_star_rating: float
    radar: TechniqueRadar
    strain_profile: StrainTimeseriesProfile
    metadata: Dict[str, Any]
    #: The raw technique drivers the radar was mapped from, in the operators' own units. Carried
    #: because they are what the per-technique ladder gates are stated on (`guard`): the star
    #: rating is a monotone per-chart rescaling of these, so a collapsing ladder shows up here
    #: first, and a gate that had to re-derive them could gate a different vector than the
    #: rating read.
    drivers: Optional[RawTechniqueDrivers] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "star_rating": self.star_rating,
            "raw_star_rating": self.raw_star_rating,
            "radar": self.radar.to_dict(),
            "strain_profile": self.strain_profile.to_dict(),
            "metadata": self.metadata,
        }
        if self.drivers is not None:
            d["drivers"] = self.drivers.to_dict()
        return d


def evaluate_intrinsic_difficulty(
    content_or_path: Union[str, Path, Beatmap7K],
    options: Optional[DifficultyOptions] = None,
    features: Optional[BeatmapFeatures] = None,
) -> IntrinsicDifficultyResult:
    """
    Evaluates the intrinsic difficulty of an osu!mania 7K beatmap.

    Accepts file path (str or Path), raw .osu string content, or a Beatmap7K instance.
    Returns the complete IntrinsicDifficultyResult contract.

    `features` lets a caller that has already extracted the feature tensor (the batch
    pipeline's feature cache) hand it in, so the rating is computed from exactly the features
    the caller holds instead of a second extraction that could disagree with them.
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

    rating_options = options.rating_options or RatingOptions()

    if features is None:
        features = extract_beatmap_features(beatmap, options=options.feature_options)
    strain_profile = compute_dual_hand_strain(beatmap, options=options.strain_options)
    # Radar scores and the synthesized star rating are expressed in the same star scale:
    # both read their calibration from the one rating options object. The raw driver vector is
    # computed once here and handed to the radar, and carried on the result: the ladder gates
    # are stated on the drivers, so the vector they gate has to be the vector the rating used.
    radar_options = options.radar_options or RadarOptions()
    drivers = compute_raw_technique_drivers(beatmap, features=features, options=radar_options)
    radar = compute_technique_radar(
        beatmap,
        features=features,
        strain_profile=strain_profile,
        options=options.radar_options,
        calibration=rating_options.calibration,
        drivers=drivers,
    )
    synthesis = synthesize_star_rating(
        radar,
        p90_strain=strain_profile.p90_strain,
        options=rating_options,
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
        drivers=drivers,
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
