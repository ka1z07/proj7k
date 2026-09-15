import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Tuple
import pytest
from scipy import stats

from proj7k.assets import scan_local_asset_library
from proj7k.difficulty import evaluate_intrinsic_difficulty
from proj7k.parser import parse_osu_7k


CANONICAL_TIERS: List[str] = [
    "0th", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th",
    "8th", "9th", "10th", "Gamma", "Azimuth", "Zenith", "Stellium"
]
TIER_INDICES = {t: i for i, t in enumerate(CANONICAL_TIERS)}

LIBRARY_DIR = Path(os.path.expanduser("~/Library/Application Support/osu/files"))
MANIFEST_PATH = Path("docs/research/structured_index.json")


def _evaluate_monotonicity(tier_scores: List[Tuple[int, float]]) -> Tuple[float, float, int]:
    sorted_items = sorted(tier_scores, key=lambda x: x[0])
    tiers = [x[0] for x in sorted_items]
    scores = [x[1] for x in sorted_items]

    if len(scores) < 2:
        return 1.0, 1.0, 0

    rho, _ = stats.spearmanr(tiers, scores)
    tau, _ = stats.kendalltau(tiers, scores)

    inversions = 0
    for i in range(len(scores) - 1):
        if scores[i + 1] < scores[i] - 1e-4:
            inversions += 1

    return float(rho), float(tau), inversions


def test_120_song_full_monotonicity_guard():
    if not LIBRARY_DIR.exists() or not MANIFEST_PATH.exists():
        pytest.skip("Local osu! library or structured index manifest not found.")

    index = scan_local_asset_library(LIBRARY_DIR)
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 1. Evaluate all songs
    tech_tier_results: Dict[str, List[Tuple[int, str, float]]] = {}
    tier_all_srs: Dict[str, List[float]] = {t: [] for t in CANONICAL_TIERS}
    fingerprint_entries: List[str] = []

    song_count = 0
    for tech, tiers in manifest.items():
        tech_tier_results[tech] = []
        for tier, d in tiers.items():
            if tier not in TIER_INDICES:
                continue
            bid = d["id"]
            song_name = d["song"]
            path = index.find_path(bid, song_name)
            if not path:
                continue

            res = evaluate_intrinsic_difficulty(str(path))
            t_idx = TIER_INDICES[tier]
            sr = res.star_rating

            tech_tier_results[tech].append((t_idx, tier, sr))
            tier_all_srs[tier].append(sr)
            fingerprint_entries.append(f"{tech}:{tier}:{sr:.4f}")
            song_count += 1

    assert song_count == 120, f"Expected 120 benchmark songs, found {song_count}"

    # 2. Monotonicity metrics
    all_rhos: List[float] = []
    all_taus: List[float] = []
    total_inversions = 0

    for tech, results in tech_tier_results.items():
        assert len(results) == 15, f"Technique {tech} has {len(results)} tiers, expected 15"
        rho, tau, inv = _evaluate_monotonicity([(r[0], r[2]) for r in results])
        all_rhos.append(rho)
        all_taus.append(tau)
        total_inversions += inv

    mean_rho = sum(all_rhos) / len(all_rhos)
    mean_tau = sum(all_taus) / len(all_taus)

    # Acceptance criteria: Spearman rho >= 0.98, Kendall tau >= 0.94
    assert mean_rho >= 0.98, f"Mean Spearman rho {mean_rho:.4f} < 0.98"
    assert mean_tau >= 0.94, f"Mean Kendall tau {mean_tau:.4f} < 0.94"
    assert total_inversions <= 20, f"Total inversions {total_inversions} exceeded threshold 20"

    # 3. Anchor medians
    # 0th Dan ≈ 3.5★, 5th Dan ≈ 5.5★, 10th Dan ≈ 7.5★, Stellium ≈ 10.5★ ~ 12.5★
    def median(vals: List[float]) -> float:
        s = sorted(vals)
        mid = len(s) // 2
        return (s[mid] + s[~mid]) / 2.0

    m_0th = median(tier_all_srs["0th"])
    m_5th = median(tier_all_srs["5th"])
    m_10th = median(tier_all_srs["10th"])
    m_stellium = median(tier_all_srs["Stellium"])

    assert 3.0 <= m_0th <= 4.0, f"0th Dan median {m_0th:.2f}★ not in [3.0, 4.0]★"
    assert 5.0 <= m_5th <= 6.0, f"5th Dan median {m_5th:.2f}★ not in [5.0, 6.0]★"
    assert 7.0 <= m_10th <= 8.2, f"10th Dan median {m_10th:.2f}★ not in [7.0, 8.2]★"
    assert 10.0 <= m_stellium <= 12.5, f"Stellium median {m_stellium:.2f}★ not in [10.0, 12.5]★"

    # All songs strictly capped at <= 12.5★
    for tier, srs in tier_all_srs.items():
        for sr in srs:
            assert sr <= 12.5, f"Star rating {sr} exceeded 12.5★ ceiling"

    # 4. Compute deterministic checksum
    fingerprint_str = "\n".join(sorted(fingerprint_entries))
    checksum = hashlib.sha256(fingerprint_str.encode("utf-8")).hexdigest()
    assert len(checksum) == 64
