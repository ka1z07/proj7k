from pathlib import Path
import pytest
from proj7k.features import BeatmapFeatures
from proj7k.batch import BenchmarkItemResult, run_benchmark_pipeline, BenchmarkItem
from proj7k.checksum import compute_feature_checksum


def _dummy_features(nps: float, hold: float) -> BeatmapFeatures:
    return BeatmapFeatures(
        total_notes=100,
        rice_count=50,
        ln_count=50,
        hold_pct=hold,
        avg_nps=nps,
        peak_4m_nps=nps * 1.5,
        duration_seconds=60.0,
        peak_1b_nps=nps * 2.0,
        gap1_count=10,
        gap1_density=0.166667,
        adj_count=5,
        adj_density=0.083333,
        mean_locked_fingers=1.5,
        lockout_profile={0: 0.2, 1: 0.5, 2: 0.3},
        antiphase_count=4,
        antiphase_rate=0.066667,
        delta_t_action=125.0,
        inverse_score=5.555555,
    )


def test_compute_feature_checksum_determinism():
    res1 = BenchmarkItemResult(
        technique="Regular Jack",
        tier="1st",
        status="SUCCESS",
        id=101,
        song="Song 1",
        bpm=120.0,
        features=_dummy_features(5.0, 50.0),
    )
    res2 = BenchmarkItemResult(
        technique="Regular Stream",
        tier="2nd",
        status="SUCCESS",
        id=102,
        song="Song 2",
        bpm=140.0,
        features=_dummy_features(6.0, 30.0),
    )

    # Order in list should not affect checksum
    checksum_a = compute_feature_checksum([res1, res2])
    checksum_b = compute_feature_checksum([res2, res1])

    assert checksum_a.startswith("sha256:")
    assert checksum_a == checksum_b


def test_compute_feature_checksum_sensitivity():
    res1 = BenchmarkItemResult(
        technique="Regular Jack",
        tier="1st",
        status="SUCCESS",
        id=101,
        song="Song 1",
        features=_dummy_features(5.0, 50.0),
    )
    res_altered = BenchmarkItemResult(
        technique="Regular Jack",
        tier="1st",
        status="SUCCESS",
        id=101,
        song="Song 1",
        features=_dummy_features(5.0001, 50.0),  # Slight float difference
    )

    c1 = compute_feature_checksum([res1])
    c2 = compute_feature_checksum([res_altered])

    assert c1 != c2


def test_pipeline_includes_feature_checksum():
    manifest = [
        BenchmarkItem(
            technique="Regular Jack",
            tier="1st",
            id=101,
            content="""osu file format v14
[General]
Mode: 3
[TimingPoints]
0,500,4,2,0,50,1,0
[HitObjects]
36,192,0,1,0,0:0:0:0:
""",
        )
    ]

    report = run_benchmark_pipeline(manifest)
    assert report.feature_checksum is not None
    assert report.feature_checksum.startswith("sha256:")
    assert report.to_dict()["feature_checksum"] == report.feature_checksum
