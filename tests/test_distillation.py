import json
from pathlib import Path
import pytest
from proj7k.batch import BenchmarkItemResult
from proj7k.features import BeatmapFeatures
from proj7k.distillation import (
    extract_feature_vector,
    normalize_feature_vectors,
    compute_technique_centroids,
    compute_feature_importance,
    compute_separability_matrix,
    distill_benchmark_features,
    DISTILLATION_FEATURE_KEYS,
    DistillationResult,
)


def _make_item(technique: str, tier: str, features: BeatmapFeatures, id: int = 1) -> BenchmarkItemResult:
    return BenchmarkItemResult(
        technique=technique,
        tier=tier,
        status="SUCCESS",
        features=features,
        id=id,
        song=f"{technique} {tier}",
        bpm=150.0,
    )


def test_extract_feature_vector():
    feat = BeatmapFeatures(
        total_notes=100,
        rice_count=50,
        ln_count=50,
        hold_pct=50.0,
        avg_nps=10.0,
        peak_4m_nps=12.0,
        peak_1b_nps=15.0,
        duration_seconds=10.0,
        gap1_density=1.5,
        adj_density=2.0,
        mean_locked_fingers=3.5,
        antiphase_rate=4.0,
        delta_t_action=100.0,
        inverse_score=3.8,
    )
    vec = extract_feature_vector(feat)
    assert isinstance(vec, dict)
    for k in DISTILLATION_FEATURE_KEYS:
        assert k in vec
    assert vec["hold_pct"] == 50.0
    assert vec["mean_locked_fingers"] == 3.5
    assert vec["antiphase_rate"] == 4.0


def _build_test_dataset() -> List[BenchmarkItemResult]:
    # 4 distinct techniques with signature attributes:
    # 1. Regular Jack: high peak_1b_nps, high adj_density, zero hold
    # 2. Regular Stream: high gap1_density, high avg_nps, zero hold
    # 3. LN Inverse: high mean_locked_fingers, high hold_pct, high inverse_score
    # 4. LN Release: high antiphase_rate, high hold_pct, low locked fingers
    items = [
        # Regular Jack (2 tiers)
        _make_item("Regular Jack", "1st", BeatmapFeatures(
            total_notes=100, rice_count=100, ln_count=0, hold_pct=0.0,
            avg_nps=8.0, peak_4m_nps=10.0, peak_1b_nps=25.0, duration_seconds=10.0,
            gap1_density=0.2, adj_density=8.0, mean_locked_fingers=0.0, antiphase_rate=0.0,
            inverse_score=0.0,
        ), id=101),
        _make_item("Regular Jack", "2nd", BeatmapFeatures(
            total_notes=120, rice_count=120, ln_count=0, hold_pct=0.0,
            avg_nps=10.0, peak_4m_nps=12.0, peak_1b_nps=30.0, duration_seconds=10.0,
            gap1_density=0.2, adj_density=10.0, mean_locked_fingers=0.0, antiphase_rate=0.0,
            inverse_score=0.0,
        ), id=102),

        # Regular Stream (2 tiers)
        _make_item("Regular Stream", "1st", BeatmapFeatures(
            total_notes=150, rice_count=150, ln_count=0, hold_pct=0.0,
            avg_nps=15.0, peak_4m_nps=18.0, peak_1b_nps=15.0, duration_seconds=10.0,
            gap1_density=5.0, adj_density=1.0, mean_locked_fingers=0.0, antiphase_rate=0.0,
            inverse_score=0.0,
        ), id=201),
        _make_item("Regular Stream", "2nd", BeatmapFeatures(
            total_notes=180, rice_count=180, ln_count=0, hold_pct=0.0,
            avg_nps=18.0, peak_4m_nps=22.0, peak_1b_nps=18.0, duration_seconds=10.0,
            gap1_density=6.0, adj_density=1.0, mean_locked_fingers=0.0, antiphase_rate=0.0,
            inverse_score=0.0,
        ), id=202),

        # LN Inverse (2 tiers)
        _make_item("LN Inverse", "1st", BeatmapFeatures(
            total_notes=100, rice_count=5, ln_count=95, hold_pct=95.0,
            avg_nps=5.0, peak_4m_nps=6.0, peak_1b_nps=5.0, duration_seconds=10.0,
            gap1_density=0.1, adj_density=0.1, mean_locked_fingers=5.0, antiphase_rate=1.0,
            inverse_score=15.0,
        ), id=301),
        _make_item("LN Inverse", "2nd", BeatmapFeatures(
            total_notes=120, rice_count=5, ln_count=115, hold_pct=96.0,
            avg_nps=6.0, peak_4m_nps=7.0, peak_1b_nps=6.0, duration_seconds=10.0,
            gap1_density=0.1, adj_density=0.1, mean_locked_fingers=5.5, antiphase_rate=1.0,
            inverse_score=20.0,
        ), id=302),

        # LN Release (2 tiers)
        _make_item("LN Release", "1st", BeatmapFeatures(
            total_notes=100, rice_count=2, ln_count=98, hold_pct=98.0,
            avg_nps=8.0, peak_4m_nps=10.0, peak_1b_nps=8.0, duration_seconds=10.0,
            gap1_density=0.5, adj_density=0.5, mean_locked_fingers=1.5, antiphase_rate=20.0,
            inverse_score=1.5,
        ), id=401),
        _make_item("LN Release", "2nd", BeatmapFeatures(
            total_notes=120, rice_count=2, ln_count=118, hold_pct=98.5,
            avg_nps=10.0, peak_4m_nps=12.0, peak_1b_nps=10.0, duration_seconds=10.0,
            gap1_density=0.5, adj_density=0.5, mean_locked_fingers=1.8, antiphase_rate=25.0,
            inverse_score=1.8,
        ), id=402),
    ]
    return items


def test_compute_technique_centroids():
    items = _build_test_dataset()
    centroids_raw, centroids_norm = compute_technique_centroids(items)

    assert len(centroids_raw) == 4
    assert len(centroids_norm) == 4

    # Assert Jack centroid properties
    jack_raw = centroids_raw["Regular Jack"]
    assert jack_raw["peak_1b_nps"] == 27.5
    assert jack_raw["adj_density"] == 9.0
    assert jack_raw["mean_locked_fingers"] == 0.0

    # Assert Inverse centroid properties
    inv_raw = centroids_raw["LN Inverse"]
    assert inv_raw["mean_locked_fingers"] == 5.25
    assert inv_raw["hold_pct"] == 95.5
    assert inv_raw["inverse_score"] == 17.5


def test_compute_feature_importance_top3():
    items = _build_test_dataset()
    centroids_raw, centroids_norm = compute_technique_centroids(items)
    importance = compute_feature_importance(centroids_raw, centroids_norm)

    assert len(importance) == 4

    # 1. Regular Jack should have peak_1b_nps or adj_density in Top 3
    jack_top = [f["feature"] for f in importance["Regular Jack"]]
    assert len(jack_top) == 3
    assert "adj_density" in jack_top or "peak_1b_nps" in jack_top

    # 2. Regular Stream should have gap1_density or avg_nps in Top 3
    stream_top = [f["feature"] for f in importance["Regular Stream"]]
    assert len(stream_top) == 3
    assert "gap1_density" in stream_top or "avg_nps" in stream_top

    # 3. LN Inverse should have mean_locked_fingers or inverse_score in Top 3
    inv_top = [f["feature"] for f in importance["LN Inverse"]]
    assert len(inv_top) == 3
    assert "mean_locked_fingers" in inv_top or "inverse_score" in inv_top

    # 4. LN Release should have antiphase_rate in Top 3
    rel_top = [f["feature"] for f in importance["LN Release"]]
    assert len(rel_top) == 3
    assert "antiphase_rate" in rel_top


def test_compute_separability_matrix_properties():
    items = _build_test_dataset()
    centroids_raw, centroids_norm = compute_technique_centroids(items)
    sep = compute_separability_matrix(centroids_norm)

    techniques = sep.techniques
    assert len(techniques) == 4
    matrix = sep.matrix
    assert len(matrix) == 4
    assert len(matrix[0]) == 4

    # 1. Symmetry and non-negativity
    for i in range(len(techniques)):
        for j in range(len(techniques)):
            # Non-negativity
            assert matrix[i][j] >= 0.0
            # Symmetry
            assert matrix[i][j] == pytest.approx(matrix[j][i], abs=1e-4)
            # Diagonal is 0
            if i == j:
                assert matrix[i][j] == 0.0
            else:
                assert matrix[i][j] > 0.0

    # 2. Global orthogonality score and mutual information
    assert sep.global_orthogonality_score > 0.0
    assert 0.0 <= sep.global_mutual_information <= 1.0
    assert sep.global_orthogonality_score == pytest.approx(1.0 - sep.global_mutual_information, abs=1e-4)


def test_distill_benchmark_features_and_export(tmp_path: Path):
    items = _build_test_dataset()
    distillation = distill_benchmark_features(items)

    assert isinstance(distillation, DistillationResult)
    data = distillation.to_dict()

    assert "fingerprints" in data
    assert "separability_matrix" in data
    assert "ground_truth_dataset" in data

    # Check fingerprints
    for tech_name, fp in data["fingerprints"].items():
        assert "centroid_raw" in fp
        assert "centroid_normalized" in fp
        assert "radar_profile" in fp
        assert "top_features" in fp
        assert len(fp["top_features"]) == 3
        assert "purity_champion" in fp
        assert fp["purity_champion"]["purity_score"] > 0.0

    # Check JSON export
    out_file = tmp_path / "ground_truth_benchmark.json"
    distillation.export_ground_truth(str(out_file))
    assert out_file.exists()

    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded["version"] == "1.0.0"
    assert len(loaded["techniques"]) == 4
    assert "separability_matrix" in loaded
