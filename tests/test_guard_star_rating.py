from typing import List, Optional
import pytest

from proj7k.batch import BatchSummary, BenchmarkBatchReport, BenchmarkItemResult
from proj7k.dan import CANONICAL_DAN_SR_BANDS, CANONICAL_DAN_TIERS
from proj7k.guard import (
    CALIBRATED_METRIC_GATES,
    DEFAULT_METRIC_GATE,
    MonotonicityGuardConfig,
    evaluate_monotonicity_guard,
)
from proj7k.monotonicity import evaluate_batch_monotonicity


def _result(technique: str, tier: str, star_rating: Optional[float], id: int = 0) -> BenchmarkItemResult:
    return BenchmarkItemResult(
        technique=technique,
        tier=tier,
        status="SUCCESS",
        id=id,
        star_rating=star_rating,
        features=None,
    )


def _report(results: List[BenchmarkItemResult]) -> BenchmarkBatchReport:
    return BenchmarkBatchReport(
        summary=BatchSummary(total=len(results), success=len(results), failed=0),
        results=results,
        monotonicity=evaluate_batch_monotonicity(results),
    )


def _ladder(technique: str, ratings: List[float]) -> List[BenchmarkItemResult]:
    return [
        _result(technique, tier, rating, id=index)
        for index, (tier, rating) in enumerate(zip(CANONICAL_DAN_TIERS, ratings))
    ]


def _ascending() -> List[float]:
    return [3.2 + 0.5 * index for index in range(len(CANONICAL_DAN_TIERS))]


def _anchor_population() -> List[BenchmarkItemResult]:
    """
    One technique's roster of charts per banded tier (every one of them sitting at its tier's
    band centre) — enough charts per tier for a median, and ascending across the tiers.
    """
    results: List[BenchmarkItemResult] = []
    for tier, (low, high) in CANONICAL_DAN_SR_BANDS.items():
        for offset in range(4):
            results.append(
                _result("Regular Jack", tier, (low + high) / 2.0 + offset * 0.01, id=len(results))
            )
    return results


def test_default_gate_validates_the_star_rating_ladder():
    """
    The gate's default metric is the engine's own artifact. Gating on raw density features
    left every rating-formula change invisible to the guard — the numbers it checked were
    not the numbers the engine produces.
    """
    report = _report(_ladder("Regular Jack", _ascending()))

    res = evaluate_monotonicity_guard(report)

    assert res.passed, res.error_message
    assert set(res.metrics_summary["Regular Jack"]) == {"star_rating"}
    assert res.metrics_summary["Regular Jack"]["star_rating"]["kendall_tau"] == 1.0


def test_guard_blocks_a_star_rating_inversion():
    report = _report([
        _result("Regular Jack", "0th", 4.20, id=1),
        _result("Regular Jack", "1st", 3.90, id=2),
    ])

    res = evaluate_monotonicity_guard(report)

    assert res.passed is False
    assert "star_rating" in res.error_message
    assert "Regular Jack" in res.error_message


def test_default_star_rating_gate_is_tighter_than_the_raw_feature_gate():
    """A rating ladder that has visibly collapsed must not slip through on a lenient default."""
    assert CALIBRATED_METRIC_GATES["star_rating"].min_kendall_tau > DEFAULT_METRIC_GATE.min_kendall_tau
    assert CALIBRATED_METRIC_GATES["star_rating"].min_spearman_rho > DEFAULT_METRIC_GATE.min_spearman_rho


def test_explicit_thresholds_override_the_calibrated_gate():
    report = _report([
        _result("Regular Jack", "0th", 4.20, id=1),
        _result("Regular Jack", "1st", 3.90, id=2),
    ])

    res = evaluate_monotonicity_guard(
        report,
        config=MonotonicityGuardConfig(min_kendall_tau=-1.0, min_spearman_rho=-1.0, max_violations=5),
    )

    assert res.passed is True, res.error_message


def test_guard_asserts_anchor_median_bands():
    report = _report(_anchor_population())

    res = evaluate_monotonicity_guard(report)

    assert res.passed, res.error_message
    for tier, (low, high) in CANONICAL_DAN_SR_BANDS.items():
        summary = res.anchor_summary[tier]
        assert summary["samples"] == 4
        assert low <= summary["median"] <= high
        assert summary["passed"] is True


def test_guard_blocks_a_tier_whose_median_leaves_its_band():
    """The ladder's central tendency is the acceptance spec: a whole tier drifting out of its
    band means the scale moved, even when the tiers remain perfectly ordered."""
    results = _anchor_population()
    # Drill the 5th Dan tier down to a 3rd Dan reading.
    results = [
        _result(r.technique, r.tier, 4.6, r.id) if r.tier == "5th" else r
        for r in results
    ]

    res = evaluate_monotonicity_guard(_report(results))

    assert res.passed is False
    assert res.anchor_summary["5th"]["passed"] is False
    assert "5th" in res.error_message


def test_anchor_bands_skip_a_tier_without_a_population():
    """
    A band is a claim about a tier's central tendency, which needs a population behind it.
    Fragment manifests (one synthetic chart per tier) are gated on monotonicity alone, and
    the skipped tiers stay visible in the result rather than vanishing silently.
    """
    report = _report(_ladder("Regular Jack", [11.0 + index for index in range(len(CANONICAL_DAN_TIERS))]))

    res = evaluate_monotonicity_guard(report)

    assert res.passed, res.error_message
    assert res.anchor_summary["0th"]["samples"] == 1
    assert res.anchor_summary["0th"]["median"] == 11.0
    assert res.anchor_summary["0th"]["passed"] is None


def test_anchor_bands_can_be_disabled():
    results = _anchor_population()
    results = [
        _result(r.technique, r.tier, 12.0, r.id) if r.tier == "0th" else r
        for r in results
    ]

    res = evaluate_monotonicity_guard(
        _report(results),
        config=MonotonicityGuardConfig(anchor_bands={}),
    )

    assert res.passed is False  # monotonicity still catches the 0th tier jumping to the top
    assert res.anchor_summary == {}


def test_anchor_bands_are_overridable_per_tier():
    report = _report(_anchor_population())

    res = evaluate_monotonicity_guard(
        report,
        config=MonotonicityGuardConfig(anchor_bands={"0th": (9.0, 9.5)}),
    )

    assert res.passed is False
    assert "0th" in res.error_message


def test_guard_ignores_failed_items_when_measuring_anchor_medians():
    results = _anchor_population()
    results.append(
        BenchmarkItemResult(
            technique="Regular Jack",
            tier="0th",
            status="FAILED_INGESTION",
            star_rating=None,
            error="boom",
        )
    )

    res = evaluate_monotonicity_guard(_report(results))

    assert res.anchor_summary["0th"]["samples"] == 4
