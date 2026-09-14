import pytest
from proj7k.monotonicity import (
    compute_kendall_tau,
    compute_spearman_rho,
    evaluate_tier_sequence,
    TIER_ORDER,
)


def test_kendall_tau_strictly_increasing():
    y = [1.0, 2.5, 4.0, 7.2, 10.0]
    tau = compute_kendall_tau(y)
    assert tau == 1.0


def test_kendall_tau_strictly_decreasing():
    y = [10.0, 8.0, 6.0, 4.0, 2.0]
    tau = compute_kendall_tau(y)
    assert tau == -1.0


def test_kendall_tau_partial_inversion():
    # [1, 2, 4, 3]: 5 concordant, 1 discordant out of 6 pairs -> (5 - 1)/6 = 2/3
    y = [1.0, 2.0, 4.0, 3.0]
    tau = compute_kendall_tau(y)
    assert round(tau, 4) == round(2.0 / 3.0, 4)


def test_spearman_rho_strictly_increasing():
    y = [10.0, 20.0, 30.0, 40.0]
    rho = compute_spearman_rho(y)
    assert rho == 1.0


def test_spearman_rho_strictly_decreasing():
    y = [100.0, 90.0, 80.0, 70.0]
    rho = compute_spearman_rho(y)
    assert rho == -1.0


def test_spearman_rho_partial_inversion():
    # [1, 2, 4, 3]: d = [0, 0, -1, 1] -> sum(d^2) = 2 -> 1 - (6*2)/(4*15) = 1 - 0.2 = 0.8
    y = [1.0, 2.0, 4.0, 3.0]
    rho = compute_spearman_rho(y)
    assert round(rho, 4) == 0.8


def test_rank_correlation_with_ties():
    # Ties in y: [1, 2, 2, 3]
    y = [1.0, 2.0, 2.0, 3.0]
    tau = compute_kendall_tau(y)
    rho = compute_spearman_rho(y)
    assert 0.8 < tau <= 1.0
    assert 0.8 < rho <= 1.0


def test_evaluate_tier_sequence_monotonic():
    # 14-tier monotonically increasing values: 1st..Stellium
    tier_vals = [(tier, float(i + 1) * 2.0) for i, tier in enumerate(TIER_ORDER)]
    report = evaluate_tier_sequence(tier_vals, technique="Regular Jack", metric="avg_nps")

    assert report.technique == "Regular Jack"
    assert report.metric == "avg_nps"
    assert report.is_monotonic is True
    assert report.kendall_tau == 1.0
    assert report.spearman_rho == 1.0
    assert len(report.steps) == 13
    assert len(report.violations) == 0

    # Verify first step: 1st -> 2nd
    step0 = report.steps[0]
    assert step0.from_tier == "1st"
    assert step0.to_tier == "2nd"
    assert step0.delta == 2.0
    assert step0.pct_change == 100.0


def test_evaluate_tier_sequence_with_inversion():
    # Introduce an inversion at 5th -> 6th
    tier_vals = []
    for i, tier in enumerate(TIER_ORDER):
        val = float(i + 1) * 2.0
        if tier == "6th":
            val = 9.0  # 5th was 10.0 -> drop of 1.0!
        tier_vals.append((tier, val))

    report = evaluate_tier_sequence(tier_vals, technique="LN Inverse", metric="mean_locked_fingers")

    assert report.is_monotonic is False
    assert report.kendall_tau < 1.0
    assert len(report.violations) == 1

    violation = report.violations[0]
    assert violation.metric == "mean_locked_fingers"
    assert violation.from_tier == "5th"
    assert violation.to_tier == "6th"
    assert violation.from_value == 10.0
    assert violation.to_value == 9.0
    assert violation.drop_magnitude == 1.0
    assert "5th" in violation.advice and "6th" in violation.advice


def test_evaluate_tier_sequence_with_discontinuity():
    # 14 tiers: baseline step is 1.0, but 7th -> 8th jumps by 20.0!
    tier_vals = []
    current_val = 2.0
    for tier in TIER_ORDER:
        if tier == "8th":
            current_val += 20.0  # Massive jump!
        else:
            current_val += 1.0
        tier_vals.append((tier, current_val))

    report = evaluate_tier_sequence(tier_vals, technique="Regular Speed", metric="avg_nps")

    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert warning.metric == "avg_nps"
    assert warning.from_tier == "7th"
    assert warning.to_tier == "8th"
    assert warning.delta == 20.0
    assert warning.delta > warning.threshold
    assert "3-sigma" in warning.message or "threshold" in warning.message
