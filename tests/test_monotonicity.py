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


def test_tier_order_references_canonical_dan_ladder():
    from proj7k.dan import CANONICAL_DAN_TIERS

    # One shared definition: the lowest tier must not be silently dropped.
    assert TIER_ORDER == list(CANONICAL_DAN_TIERS)
    assert TIER_ORDER[0] == "0th"
    assert len(TIER_ORDER) == 15


def test_evaluate_tier_sequence_monotonic():
    # Full canonical ladder, monotonically increasing: 0th..Stellium
    tier_vals = [(tier, float(i + 1) * 2.0) for i, tier in enumerate(TIER_ORDER)]
    report = evaluate_tier_sequence(tier_vals, technique="Regular Jack", metric="avg_nps")

    assert report.technique == "Regular Jack"
    assert report.metric == "avg_nps"
    assert report.is_monotonic is True
    assert report.kendall_tau == 1.0
    assert report.spearman_rho == 1.0
    assert len(report.steps) == len(TIER_ORDER) - 1
    assert len(report.violations) == 0

    # Verify first step: 0th -> 1st (the lowest tier is covered)
    step0 = report.steps[0]
    assert step0.from_tier == "0th"
    assert step0.to_tier == "1st"
    assert step0.delta == 2.0
    assert step0.pct_change == 100.0


def test_evaluate_tier_sequence_with_inversion():
    # Introduce an inversion at 5th -> 6th: 6th lands one point below 5th
    expected = {tier: float(i + 1) * 2.0 for i, tier in enumerate(TIER_ORDER)}
    tier_vals = []
    for tier in TIER_ORDER:
        val = expected[tier]
        if tier == "6th":
            val = expected["5th"] - 1.0
        tier_vals.append((tier, val))

    report = evaluate_tier_sequence(tier_vals, technique="LN Inverse", metric="mean_locked_fingers")

    assert report.is_monotonic is False
    assert report.kendall_tau < 1.0
    assert len(report.violations) == 1

    violation = report.violations[0]
    assert violation.metric == "mean_locked_fingers"
    assert violation.from_tier == "5th"
    assert violation.to_tier == "6th"
    assert violation.from_value == expected["5th"]
    assert violation.to_value == expected["5th"] - 1.0
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


def test_evaluate_batch_monotonicity_inverse_bpm_scaling():
    from proj7k.features import BeatmapFeatures
    from proj7k.monotonicity import evaluate_batch_monotonicity

    # Mock batch items for LN Inverse with pseudo-inversion caused by low-speed charts:
    # 7th Dan: 142 BPM, 5.60 locked fingers (Cognitive illusion, low physical load)
    # 10th Dan: 164 BPM, 4.80 locked fingers
    # Gamma: 178 BPM, 4.60 locked fingers
    # Zenith: 200 BPM, 5.10 locked fingers
    # Stellium: 240 BPM, 5.20 locked fingers
    #
    # Without scaling: 7th (5.60) -> 10th (4.80) -> Gamma (4.60) has 2 inversions!
    class MockItem:
        def __init__(self, tier: str, bpm: float, locked: float):
            self.technique = "LN Inverse"
            self.tier = tier
            self.bpm = bpm
            self.status = "SUCCESS"
            self.id = 100
            self.song = f"Song {tier}"
            self.features = BeatmapFeatures(
                total_notes=1000,
                rice_count=10,
                ln_count=990,
                hold_pct=99.0,
                avg_nps=10.0,
                peak_4m_nps=15.0,
                duration_seconds=100.0,
                mean_locked_fingers=locked,
            )

    items = [
        MockItem("7th", 142.0, 5.60),
        MockItem("10th", 164.0, 4.80),
        MockItem("Gamma", 178.0, 4.60),
        MockItem("Zenith", 200.0, 5.10),
        MockItem("Stellium", 240.0, 5.20),
    ]

    # 1. Evaluate with scaling disabled -> shows inversion
    unscaled_report = evaluate_batch_monotonicity(
        items,
        metrics=["mean_locked_fingers"],
        apply_scaling=False,
    )
    unscaled_eval = unscaled_report["LN Inverse"]["mean_locked_fingers"]
    assert unscaled_eval["is_monotonic"] is False
    assert unscaled_eval["kendall_tau"] < 1.0
    assert len(unscaled_eval["violations"]) >= 1

    # 2. Evaluate with scaling enabled -> gating operator pre-applied!
    scaled_report = evaluate_batch_monotonicity(
        items,
        metrics=["mean_locked_fingers"],
        apply_scaling=True,
    )
    scaled_eval = scaled_report["LN Inverse"]["mean_locked_fingers"]
    assert scaled_eval["is_monotonic"] is True
    assert scaled_eval["kendall_tau"] == 1.0
    assert len(scaled_eval["violations"]) == 0

    # 3. Assert calibration chapter is clearly present with before/after comparison
    calibration = scaled_eval.get("scaling_calibration")
    assert calibration is not None
    assert calibration["before"]["is_monotonic"] is False
    assert calibration["before"]["kendall_tau"] < 1.0
    assert calibration["after"]["is_monotonic"] is True
    assert calibration["after"]["kendall_tau"] == 1.0

    # 4. Assert clock window distribution is clearly recorded
    dist = calibration["window_distribution"]
    assert len(dist) == 5
    # Check 7th Dan (low speed truncation)
    rec_7th = [r for r in dist if r["tier"] == "7th"][0]
    assert rec_7th["bpm"] == 142.0
    assert rec_7th["delta_t_ms"] == pytest.approx(105.6338, abs=1e-3)
    assert rec_7th["regime"] == "LOW_SPEED_TRUNCATION"
    assert rec_7th["calibrated_value"] <= 5.0
    assert rec_7th["calibrated_value"] < rec_7th["raw_value"]

    # Check Stellium (extreme high speed penalty)
    rec_stellium = [r for r in dist if r["tier"] == "Stellium"][0]
    assert rec_stellium["bpm"] == 240.0
    assert rec_stellium["delta_t_ms"] == 62.5
    assert rec_stellium["regime"] == "EXPONENTIAL_PENALTY"
    assert rec_stellium["scaling_factor"] > 3.0

