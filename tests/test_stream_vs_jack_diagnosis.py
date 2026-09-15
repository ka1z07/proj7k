import json
import os
from pathlib import Path
import pytest

from proj7k.assets import scan_local_asset_library
from proj7k.difficulty import evaluate_intrinsic_difficulty

LIBRARY_DIR = Path(os.path.expanduser("~/Library/Application Support/osu/files"))
MANIFEST_PATH = Path("docs/research/structured_index.json")


def test_regular_jack_benchmark_tracks_classified_as_jack():
    """
    Feedback loop for Stream vs Jack confusion bug:
    Asserts that canonical Regular Jack benchmark charts (e.g. 10th Dan SAMBAJACK,
    Gamma Dan Identity: Jack) are recognized as dominant 'jack' rather than 'stream',
    and that their jack radar score exceeds their stream radar score.
    """
    if not LIBRARY_DIR.exists() or not MANIFEST_PATH.exists():
        pytest.skip("Local osu! library or structured index not found")

    index = scan_local_asset_library(LIBRARY_DIR)
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Test 10th Dan Regular Jack: SAMBA LAND [SAMBAJACK]
    samba_entry = manifest["Regular Jack"]["10th"]
    samba_path = index.find_path(samba_entry["id"], samba_entry["song"])
    assert samba_path is not None, "SAMBAJACK beatmap file not found"

    res_10th = evaluate_intrinsic_difficulty(str(samba_path))
    r_10th = res_10th.radar

    # Test Gamma Dan Regular Jack: Identity Part 4 [Identity: Jack]
    identity_entry = manifest["Regular Jack"]["Gamma"]
    identity_path = index.find_path(identity_entry["id"], identity_entry["song"])
    assert identity_path is not None, "Identity: Jack beatmap file not found"

    res_gamma = evaluate_intrinsic_difficulty(str(identity_path))
    r_gamma = res_gamma.radar

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
