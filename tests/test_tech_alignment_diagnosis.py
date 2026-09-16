import json
import os
from pathlib import Path
from typing import Tuple
import pytest

from proj7k.assets import AssetLibraryIndex, scan_local_asset_library
from proj7k.difficulty import evaluate_intrinsic_difficulty
from proj7k.radar import TechniqueRadar

LIBRARY_DIR = Path(os.path.expanduser("~/Library/Application Support/osu/files"))
MANIFEST_PATH = Path("docs/research/structured_index.json")


@pytest.fixture(scope="module")
def benchmark_env() -> Tuple[AssetLibraryIndex, dict]:
    if not LIBRARY_DIR.exists() or not MANIFEST_PATH.exists():
        pytest.skip("Local osu! library or structured index not found")
    index = scan_local_asset_library(LIBRARY_DIR)
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return index, manifest


def _get_radar(index: AssetLibraryIndex, manifest: dict, tech: str, tier: str) -> TechniqueRadar:
    entry = manifest[tech][tier]
    path = index.find_path(entry["id"], entry["song"])
    assert path is not None, f"Beatmap file not found for {tech} {tier}: {entry['song']}"
    return evaluate_intrinsic_difficulty(str(path)).radar


def test_regular_tech_benchmark_tracks_classified_as_tech(benchmark_env):
    """
    Unit test for SPEC-P2.2-03 / ADR-0008:
    Verify that canonical Regular Tech benchmark charts are recognized as dominant 'tech',
    and their tech radar score exceeds stream and jack scores.
    """
    index, manifest = benchmark_env

    for tier in ["6th", "7th", "10th", "Stellium"]:
        radar = _get_radar(index, manifest, "Regular Tech", tier)
        assert radar.dominant_technique == "tech", (
            f"Regular Tech {tier} misclassified as {radar.dominant_technique} "
            f"(tech={radar.tech:.2f}★, stream={radar.stream:.2f}★, jack={radar.jack:.2f}★)"
        )
        assert radar.tech > radar.stream, (
            f"Regular Tech {tier} tech score ({radar.tech:.2f}★) <= stream score ({radar.stream:.2f}★)"
        )


def test_ln_tech_benchmark_tracks_classified_as_ln_tech(benchmark_env):
    """
    Unit test for SPEC-P2.2-03 / ADR-0008:
    Verify that canonical LN Tech benchmark charts are recognized as dominant 'ln_tech',
    and their ln_tech radar score exceeds ln_general and ln_release scores.
    """
    index, manifest = benchmark_env

    for tier in ["5th", "10th", "Gamma", "Stellium"]:
        radar = _get_radar(index, manifest, "LN Tech", tier)
        assert radar.dominant_technique == "ln_tech", (
            f"LN Tech {tier} misclassified as {radar.dominant_technique} "
            f"(ln_tech={radar.ln_tech:.2f}★, ln_gen={radar.ln_general:.2f}★, ln_rel={radar.ln_release:.2f}★)"
        )
        assert radar.ln_tech >= radar.ln_general, (
            f"LN Tech {tier} ln_tech score ({radar.ln_tech:.2f}★) < ln_gen score ({radar.ln_general:.2f}★)"
        )


def test_regular_stream_and_ln_general_not_hijacked_by_tech(benchmark_env):
    """
    Regression verification for SPEC-P2.2-03:
    Ensures regular stream charts and LN general charts do not suffer semantic drift into tech.
    """
    index, manifest = benchmark_env

    # 1. Regular Stream benchmarks must retain stream dominance
    for tier in ["5th", "10th", "Stellium"]:
        radar = _get_radar(index, manifest, "Regular Stream", tier)
        assert radar.dominant_technique == "stream", (
            f"Regular Stream {tier} misclassified as {radar.dominant_technique} "
            f"(stream={radar.stream:.2f}★, tech={radar.tech:.2f}★)"
        )
        assert radar.stream > radar.tech, (
            f"Regular Stream {tier} stream score ({radar.stream:.2f}★) <= tech score ({radar.tech:.2f}★)"
        )

    # 2. LN General benchmarks must retain ln_general dominance
    for tier in ["5th", "10th", "Stellium"]:
        radar = _get_radar(index, manifest, "LN General", tier)
        assert radar.dominant_technique == "ln_general", (
            f"LN General {tier} misclassified as {radar.dominant_technique} "
            f"(ln_gen={radar.ln_general:.2f}★, ln_tech={radar.ln_tech:.2f}★)"
        )
        assert radar.ln_general > radar.ln_tech, (
            f"LN General {tier} ln_gen score ({radar.ln_general:.2f}★) <= ln_tech score ({radar.ln_tech:.2f}★)"
        )
