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


def test_ln_general_benchmark_tracks_classified_as_ln_general(benchmark_env):
    """
    Feedback loop for Ticket 2 (SPEC-P2.2-02):
    Asserts that canonical LN General benchmark charts (e.g. 5th, 10th, Stellium)
    are recognized as dominant 'ln_general' rather than falsely hijacked by 'ln_release'.
    """
    index, manifest = benchmark_env

    for tier in ["5th", "10th", "Stellium"]:
        radar = _get_radar(index, manifest, "LN General", tier)
        assert radar.dominant_technique == "ln_general", (
            f"LN General {tier} misclassified as {radar.dominant_technique} "
            f"(gen={radar.ln_general:.2f}★, rel={radar.ln_release:.2f}★)"
        )
        assert radar.ln_general > radar.ln_release, (
            f"LN General {tier} general score ({radar.ln_general:.2f}★) <= release score ({radar.ln_release:.2f}★)"
        )


def test_ln_inverse_high_tier_benchmark_tracks_classified_as_ln_inverse(benchmark_env):
    """
    Feedback loop for Ticket 2 (SPEC-P2.2-02):
    Asserts that canonical high-tier LN Inverse benchmark charts (8th ~ Stellium)
    naturally recover 'ln_inverse' dominance once Release inflation is resolved.
    """
    index, manifest = benchmark_env

    for tier in ["8th", "10th", "Stellium"]:
        radar = _get_radar(index, manifest, "LN Inverse", tier)
        assert radar.dominant_technique == "ln_inverse", (
            f"LN Inverse {tier} misclassified as {radar.dominant_technique} "
            f"(inv={radar.ln_inverse:.2f}★, rel={radar.ln_release:.2f}★, gen={radar.ln_general:.2f}★)"
        )
        assert radar.ln_inverse > radar.ln_release, (
            f"LN Inverse {tier} inverse score ({radar.ln_inverse:.2f}★) <= release score ({radar.ln_release:.2f}★)"
        )
