import json
from pathlib import Path
import pytest

from proj7k.batch import (
    BenchmarkItem,
    main as batch_cli_main,
    process_benchmark_item,
    run_benchmark_pipeline,
)
from proj7k.engine import evaluate_osu


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
    profile `evaluate_osu` produces for that chart — its total stars, the eight skills' stars and
    the dominant skill — not a re-derivation that could drift from the engine's own composition.
    """
    report = run_benchmark_pipeline(_manifest(), enable_cache=False)
    expected = evaluate_osu(SAMPLE_7K)

    assert report.summary.success == 2
    for result in report.results:
        assert result.star_rating == expected.total_stars
        assert result.dominant_skill == expected.dominant_skill
        assert result.skills == {name: reading.stars for name, reading in expected.skills.items()}


def test_batch_result_serializes_star_rating():
    result = process_benchmark_item(BenchmarkItem(technique="Jack", tier="0th", id=1, content=SAMPLE_7K))

    payload = result.to_dict()

    assert payload["star_rating"] == result.star_rating
    assert payload["dominant_skill"] == result.dominant_skill
    assert payload["skills"] == result.skills
    assert set(payload["skills"]) == {
        "rc_jack", "rc_tech", "rc_speed", "rc_stamina", "ln_general", "ln_tech", "ln_inverse", "ln_release",
    }


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


def test_batch_can_skip_rating_evaluation():
    """
    Feature-only runs opt out of the rating stage without losing the feature tensor.

    The rating cost is solving the chart, not the features, and a caller re-freezing or
    validating features (the checksum path, the distillation export) does not need it.
    """
    report = run_benchmark_pipeline(_manifest(), evaluate_rating=False)

    assert report.summary.success == 2
    for result in report.results:
        assert result.features is not None
        assert result.star_rating is None
        assert result.dominant_skill is None
        assert result.skills is None


def test_cli_no_rating_flag_skips_the_rating_stage(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps([{"technique": "Jack", "tier": "0th", "id": 1, "content": SAMPLE_7K}]),
        encoding="utf-8",
    )
    out_file = tmp_path / "report.json"

    assert batch_cli_main(["--manifest", str(manifest_path), "--no-rating", "-o", str(out_file)]) == 0

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["results"][0]["star_rating"] is None


def test_failed_ingestion_carries_no_rating():
    result = process_benchmark_item(
        BenchmarkItem(technique="Jack", tier="0th", id=1, osu_path="/path/does/not/exist.osu")
    )

    assert result.status == "FAILED_INGESTION"
    assert result.star_rating is None
