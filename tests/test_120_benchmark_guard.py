"""
End-to-end acceptance of the difficulty engine against the frozen benchmark ladder.

The manifest (`docs/research/structured_index.json`) names the 15 canonical Dan tiers' chart
sets per technique; the corpus fixture (`tests/fixtures/benchmark_corpus.json.gz`) carries
their raw `.osu` content, frozen into the repository so this validation runs anywhere instead
of only on a machine with osu! installed. Together they are the ladder the engine is
accountable to: the tiers must stay ordered by star rating, and the anchor tiers' medians must
stay inside their acceptance bands.
"""

import dataclasses
import statistics
from typing import Dict, List

import pytest

from proj7k.batch import BenchmarkBatchReport, run_benchmark_pipeline
from proj7k.dan import CANONICAL_DAN_SR_BANDS, CANONICAL_DAN_TIERS
from proj7k.guard import MonotonicityGuardConfig, evaluate_monotonicity_guard
from proj7k.monotonicity import evaluate_batch_monotonicity


#: Every technique in the manifest holds a full 15-tier ladder of charts.
EXPECTED_TECHNIQUES = (
    "Regular Jack",
    "Regular Tech",
    "Regular Speed",
    "Regular Stream",
    "LN General",
    "LN Tech",
    "LN Inverse",
    "LN Release",
)
EXPECTED_CHART_COUNT = len(EXPECTED_TECHNIQUES) * len(CANONICAL_DAN_TIERS)

#: The engine version's star-rating fingerprint — the digest of all 120 ratings.
#:
#: The monotonicity and anchor gates below catch a formula change that moves the ladder's shape
#: or its scale; this catches *every* formula change, including one small enough to leave both
#: intact, because it pins the numbers themselves. A deliberate calibration change is therefore
#: a two-part commit: update the constants, then re-baseline this digest (and the acceptance
#: bars below) after confirming the new ladder is the one you meant.
EXPECTED_STAR_RATING_CHECKSUM = "sha256:c829be124657dd7df07a1ec9b9b40e6acb1687fa942c7ac4a42cd7b189330023"

#: Ladder-level acceptance bar from the Phase 2 specification: the 120 chart ladder must clear
#: these as a whole, on top of no single technique collapsing (which the guard's per-technique
#: gates cover). Measured on the frozen corpus: mean rho 0.983 / mean tau 0.940 / 16 inversions.
LADDER_MIN_MEAN_SPEARMAN_RHO = 0.98
LADDER_MIN_MEAN_KENDALL_TAU = 0.94
LADDER_MAX_TOTAL_INVERSIONS = 20


@pytest.fixture(scope="module")
def benchmark_report(
    benchmark_manifest: Dict[str, Dict[str, dict]],
    benchmark_corpus: Dict[int, str],
) -> BenchmarkBatchReport:
    """The engine's output over the whole frozen ladder, evaluated once for this module."""
    return run_benchmark_pipeline(benchmark_manifest, corpus=benchmark_corpus, cache_dir=None)


def _star_ratings_by_tier(report: BenchmarkBatchReport) -> Dict[str, List[float]]:
    by_tier: Dict[str, List[float]] = {tier: [] for tier in CANONICAL_DAN_TIERS}
    for result in report.results:
        if result.status == "SUCCESS" and result.tier in by_tier and result.star_rating is not None:
            by_tier[result.tier].append(result.star_rating)
    return by_tier


def test_frozen_ladder_ingests_completely(benchmark_report: BenchmarkBatchReport):
    assert benchmark_report.summary.total == EXPECTED_CHART_COUNT
    assert benchmark_report.summary.failed == 0

    counts: Dict[str, int] = {}
    for result in benchmark_report.results:
        assert result.status == "SUCCESS", result.error
        assert result.star_rating is not None
        counts[result.technique] = counts.get(result.technique, 0) + 1

    # Every technique contributes one chart per tier — an ingestion that silently dropped a
    # chart would otherwise show up only as a weaker ladder.
    assert set(counts) == set(EXPECTED_TECHNIQUES)
    assert set(counts.values()) == {len(CANONICAL_DAN_TIERS)}
    assert "star_rating" in benchmark_report.monotonicity["Regular Jack"]


def test_star_rating_ladder_is_monotone_for_every_technique(benchmark_report: BenchmarkBatchReport):
    """The engine's own output — not the raw density features behind it — orders the ladder."""
    result = evaluate_monotonicity_guard(benchmark_report)

    assert result.passed, result.error_message
    assert set(result.metrics_summary) == set(EXPECTED_TECHNIQUES)
    for technique, metrics in result.metrics_summary.items():
        assert set(metrics) == {"star_rating"}, technique
        assert metrics["star_rating"]["kendall_tau"] >= 0.88, technique
        assert metrics["star_rating"]["spearman_rho"] >= 0.95, technique


def test_ladder_ratings_match_the_pinned_engine_fingerprint(benchmark_report: BenchmarkBatchReport):
    """
    Pins the engine's actual output for the frozen corpus. The gates above tolerate a rating
    that drifts a little as long as the ladder's shape and scale hold; this one tolerates
    nothing, so a calibration change cannot land without the digest being re-baselined on
    purpose.
    """
    assert benchmark_report.star_rating_checksum == EXPECTED_STAR_RATING_CHECKSUM


def test_ladder_clears_the_phase2_aggregate_acceptance_bar(benchmark_report: BenchmarkBatchReport):
    """
    The standing ladder-level acceptance criteria, checked as a whole rather than per technique:
    a corpus where every technique scrapes past its own gate must still fail here if the ladder
    as a whole has drifted.
    """
    summary = evaluate_monotonicity_guard(benchmark_report).metrics_summary
    star = {technique: metrics["star_rating"] for technique, metrics in summary.items()}

    mean_rho = sum(m["spearman_rho"] for m in star.values()) / len(star)
    mean_tau = sum(m["kendall_tau"] for m in star.values()) / len(star)
    total_inversions = sum(m["violations_count"] for m in star.values())

    assert mean_rho >= LADDER_MIN_MEAN_SPEARMAN_RHO, f"mean Spearman rho {mean_rho:.4f}"
    assert mean_tau >= LADDER_MIN_MEAN_KENDALL_TAU, f"mean Kendall tau {mean_tau:.4f}"
    assert total_inversions <= LADDER_MAX_TOTAL_INVERSIONS, f"{total_inversions} inversions"


def test_anchor_tier_medians_land_in_their_bands(benchmark_report: BenchmarkBatchReport):
    by_tier = _star_ratings_by_tier(benchmark_report)

    for tier, (low, high) in CANONICAL_DAN_SR_BANDS.items():
        samples = by_tier[tier]
        assert len(samples) == len(EXPECTED_TECHNIQUES), tier
        median = statistics.median(samples)
        assert low <= median <= high, f"{tier} median {median:.3f}★ outside [{low}, {high}]★"


def test_star_ratings_stay_under_the_soft_cap_ceiling(benchmark_report: BenchmarkBatchReport):
    for result in benchmark_report.results:
        assert 0.0 < result.star_rating <= 12.5


def test_diagnostic_metrics_stay_available_on_explicit_request(benchmark_report: BenchmarkBatchReport):
    """
    The gate defaults to the star rating, but the corpus still evaluates every metric, so a
    failing ladder can be diagnosed by asking for the raw quantities behind it.
    """
    requested = evaluate_monotonicity_guard(
        benchmark_report, config=MonotonicityGuardConfig(metrics=["avg_nps"])
    )

    assert set(requested.metrics_summary["Regular Jack"]) == {"avg_nps"}
    assert requested.passed is True, requested.error_message
    assert "star_rating" not in requested.metrics_summary["Regular Jack"]


def test_guard_blocks_a_rating_regression_on_the_real_ladder(benchmark_report: BenchmarkBatchReport):
    """
    A formula change that leaves the features untouched must be caught: this is what gating on
    the star rating buys over gating on `avg_nps` / `peak_4m_nps`, which such a change does not
    move at all.
    """
    regressed_results = [
        dataclasses.replace(result, star_rating=12.5 - result.star_rating)
        if result.technique == "LN Inverse"
        else result
        for result in benchmark_report.results
    ]
    regressed = BenchmarkBatchReport(
        summary=benchmark_report.summary,
        results=regressed_results,
        monotonicity=evaluate_batch_monotonicity(regressed_results),
        feature_checksum=benchmark_report.feature_checksum,
    )

    result = evaluate_monotonicity_guard(regressed)

    assert result.passed is False
    assert "LN Inverse" in result.error_message
    assert "star_rating" in result.error_message
