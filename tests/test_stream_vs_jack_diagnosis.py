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


def test_regular_jack_benchmark_tracks_classified_as_jack(benchmark_env):
    """
    Feedback loop for Stream vs Jack confusion bug (ADR-0007):
    Asserts that canonical Regular Jack benchmark charts (e.g. 10th Dan SAMBAJACK,
    Gamma Dan Identity: Jack) are recognized as dominant 'jack' rather than 'stream',
    and that their jack radar score exceeds their stream radar score.
    """
    index, manifest = benchmark_env

    r_10th = _get_radar(index, manifest, "Regular Jack", "10th")
    r_gamma = _get_radar(index, manifest, "Regular Jack", "Gamma")

    # Assert 10th Dan SAMBAJACK is recognized as Jack dominant
    assert r_10th.dominant_technique == "jack", (
        f"10th Dan SAMBAJACK misclassified as {r_10th.dominant_technique} "
        f"(jack={r_10th.jack:.2f}★, stream={r_10th.stream:.2f}★)"
    )
    assert r_10th.jack > r_10th.stream, (
        f"10th Dan SAMBAJACK jack score ({r_10th.jack:.2f}★) <= stream score ({r_10th.stream:.2f}★)"
    )

    # Assert Gamma Dan Identity: Jack is recognized as Jack dominant
    assert r_gamma.dominant_technique == "jack", (
        f"Gamma Dan Identity: Jack misclassified as {r_gamma.dominant_technique} "
        f"(jack={r_gamma.jack:.2f}★, stream={r_gamma.stream:.2f}★)"
    )
    assert r_gamma.jack > r_gamma.stream, (
        f"Gamma Dan Identity: Jack jack score ({r_gamma.jack:.2f}★) <= stream score ({r_gamma.stream:.2f}★)"
    )


def test_regular_stream_benchmark_tracks_classified_as_stream(benchmark_env):
    """
    Feedback loop verifying Stream benchmarks retain Stream dominance under ADR-0007.
    """
    index, manifest = benchmark_env

    r_10th = _get_radar(index, manifest, "Regular Stream", "10th")
    r_gamma = _get_radar(index, manifest, "Regular Stream", "Gamma")

    assert r_10th.dominant_technique == "stream", (
        f"10th Dan Fox4-Raize misclassified as {r_10th.dominant_technique} "
        f"(jack={r_10th.jack:.2f}★, stream={r_10th.stream:.2f}★)"
    )
    assert r_10th.stream > r_10th.jack, (
        f"10th Dan Fox4-Raize stream score ({r_10th.stream:.2f}★) <= jack score ({r_10th.jack:.2f}★)"
    )

    assert r_gamma.dominant_technique == "stream", (
        f"Gamma Dan Paraclete misclassified as {r_gamma.dominant_technique} "
        f"(jack={r_gamma.jack:.2f}★, stream={r_gamma.stream:.2f}★)"
    )
    assert r_gamma.stream > r_gamma.jack, (
        f"Gamma Dan Paraclete stream score ({r_gamma.stream:.2f}★) <= jack score ({r_gamma.jack:.2f}★)"
    )


def test_regular_jack_1st_dai_dir_detected_with_valid_jack_score(benchmark_env):
    """
    Feedback loop for Ticket 1 (SPEC-P2.2-01):
    Asserts that 1st Dan dai - dir [Hard] (which contains 41 instances of 127ms two-note jacks)
    receives a valid Jack radar score > 3.0★ (expected 3.2★ ~ 4.2★), not 0.00★.
    """
    index, manifest = benchmark_env
    r_1st = _get_radar(index, manifest, "Regular Jack", "1st")
    assert r_1st.jack > 3.0, (
        f"1st Dan dai - dir jack score too low: {r_1st.jack:.2f}★ <= 3.0★ (was 0.00★)"
    )
    assert 3.0 <= r_1st.jack <= 4.5, (
        f"1st Dan dai - dir jack score out of expected range: {r_1st.jack:.2f}★"
    )

