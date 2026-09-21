from pathlib import Path
import pytest

from proj7k.batch import BenchmarkItem, process_benchmark_item, run_benchmark_pipeline
from proj7k.difficulty import evaluate_intrinsic_difficulty


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_7K = (REPO_ROOT / "docs" / "sample_7k.osu").read_text(encoding="utf-8")


def _manifest() -> list:
    return [
        BenchmarkItem(technique="Regular Stream", tier="0th", id=101, content=SAMPLE_7K),
        BenchmarkItem(technique="Regular Stream", tier="1st", id=102, osu_path=str(REPO_ROOT / "docs" / "sample_7k.osu")),
    ]


def test_batch_results_carry_the_engine_star_rating():
    """
    The gate validates the engine's actual artifact, so a batch result must carry the very
    star rating `evaluate_intrinsic_difficulty` produces for that chart — not a re-derivation
    that could drift from the engine's own composition of radar, strain and calibration.
    """
    report = run_benchmark_pipeline(_manifest(), enable_cache=False)

    assert report.summary.success == 2
    for result in report.results:
        expected = evaluate_intrinsic_difficulty(SAMPLE_7K)
        assert result.star_rating == expected.star_rating
        assert result.uncompressed_star_rating == expected.raw_star_rating
        assert result.dominant_technique == expected.metadata["dominant_technique"]


def test_batch_result_serializes_star_rating():
    result = process_benchmark_item(BenchmarkItem(technique="Jack", tier="0th", id=1, content=SAMPLE_7K))

    payload = result.to_dict()

    assert payload["star_rating"] == result.star_rating
    assert payload["uncompressed_star_rating"] == result.uncompressed_star_rating
    assert payload["dominant_technique"] == result.dominant_technique


def test_batch_rating_survives_a_warm_feature_cache(tmp_path: Path):
    """
    A Layer-2 feature-cache hit must not change the rating.

    The second run uses a *fresh* cache instance, so the features come back through the JSON
    on disk rather than from the first instance's in-memory dictionary — the path a rated
    batch actually takes in a later process. The rating is computed from those restored
    features, so anything the round trip loses moves the rating.
    """
    from proj7k.cache import TwoLayerCache

    cache_dir = tmp_path / "cache"
    item = BenchmarkItem(technique="Jack", tier="0th", id=1, content=SAMPLE_7K)

    cold = process_benchmark_item(item, cache=TwoLayerCache(cache_dir=cache_dir, enabled=True))
    warm_cache = TwoLayerCache(cache_dir=cache_dir, enabled=True)
    warm = process_benchmark_item(item, cache=warm_cache)

    assert warm_cache.stats["feature_hits"] == 1
    assert warm.features.to_dict() == cold.features.to_dict()
    assert warm.star_rating == cold.star_rating


def test_failed_ingestion_carries_no_rating():
    result = process_benchmark_item(
        BenchmarkItem(technique="Jack", tier="0th", id=1, osu_path="/path/does/not/exist.osu")
    )

    assert result.status == "FAILED_INGESTION"
    assert result.star_rating is None
