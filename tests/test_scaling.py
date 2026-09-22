import pytest
from proj7k.scaling import (
    compute_action_window,
    compute_inverse_scaling_factor,
    apply_inverse_bpm_scaling,
    compute_inverse_score,
    LOW_SPEED_BPM_THRESHOLD,
    HIGH_SPEED_BPM_THRESHOLD,
    LOW_SPEED_CAP,
)


def test_compute_action_window():
    # delta_t = 60000 / (BPM * Divisor)
    # 120 BPM, Divisor=4 -> 60000 / 480 = 125.0 ms
    assert compute_action_window(120.0, divisor=4) == 125.0

    # 142 BPM (Case 2 brave heart), Divisor=4 -> 60000 / 568 = 105.6338 ms
    assert compute_action_window(142.0, divisor=4) == 105.6338

    # 200 BPM, Divisor=4 -> 60000 / 800 = 75.0 ms
    assert compute_action_window(200.0, divisor=4) == 75.0

    # 240 BPM, Divisor=4 -> 60000 / 960 = 62.5 ms
    assert compute_action_window(240.0, divisor=4) == 62.5

    # Divisor=2 (8th note)
    assert compute_action_window(120.0, divisor=2) == 250.0

    # Invalid / Zero BPM
    assert compute_action_window(0.0) == float("inf")
    assert compute_action_window(-10.0) == float("inf")


def test_inverse_scaling_factor_regimes():
    # 1. Low-speed regime (<= 145 BPM)
    factor_low, regime_low = compute_inverse_scaling_factor(140.0, mean_locked_fingers=5.6)
    assert regime_low == "LOW_SPEED_TRUNCATION"
    assert factor_low < 1.0

    # 2. Standard mid-speed regime (145 < BPM < 180)
    factor_mid, regime_mid = compute_inverse_scaling_factor(160.0, mean_locked_fingers=4.5)
    assert regime_mid == "STANDARD"
    assert factor_low < factor_mid <= 1.05

    # 3. High-speed regime (>= 180 BPM) with exponential penalty
    factor_high, regime_high = compute_inverse_scaling_factor(180.0, mean_locked_fingers=4.5)
    assert regime_high == "EXPONENTIAL_PENALTY"

    factor_extreme, regime_extreme = compute_inverse_scaling_factor(220.0, mean_locked_fingers=4.5)
    assert regime_extreme == "EXPONENTIAL_PENALTY"
    # Exponential penalty increases with BPM
    assert factor_extreme > factor_high * 1.5


def test_apply_inverse_bpm_scaling_low_speed_hard_cap():
    # Low speed (142 BPM, e.g. Case 2 7th Dan, raw locked fingers = 5.60)
    # The calibrated difficulty MUST be capped <= 5.0 (初中段位等效区间)
    calibrated_val, factor, regime = apply_inverse_bpm_scaling(5.60, bpm=142.0)
    assert regime == "LOW_SPEED_TRUNCATION"
    assert calibrated_val <= LOW_SPEED_CAP  # 5.0
    assert calibrated_val < 5.60

    # Even with an extreme raw value of 7.0 locked fingers at 120 BPM:
    calibrated_extreme, _, _ = apply_inverse_bpm_scaling(7.0, bpm=120.0)
    assert calibrated_extreme <= LOW_SPEED_CAP


def test_apply_inverse_bpm_scaling_high_speed_penalty_saturates():
    # High speed (200 BPM and 240 BPM, high locked fingers): micro tolerance is severely
    # compressed and the penalty regime applies. It compounds sub-linearly rather than
    # exponentially — the buffer CONTEXT.md describes (判定窗口重叠缓冲 η) absorbs the growth,
    # which the strain side's own high-speed law has modelled all along.
    calibrated_200, factor_200, regime_200 = apply_inverse_bpm_scaling(4.5, bpm=200.0)
    assert regime_200 == "EXPONENTIAL_PENALTY"
    assert factor_200 > 1.5
    assert calibrated_200 > 4.5 * 1.5

    calibrated_240, factor_240, regime_240 = apply_inverse_bpm_scaling(5.0, bpm=240.0)
    assert regime_240 == "EXPONENTIAL_PENALTY"
    assert factor_240 > factor_200  # still grows with tempo
    assert factor_240 < factor_200 * 1.8  # but not by a compounding step
    assert calibrated_240 > 10.0  # still the extreme tier's load (Stellium)

    # The saturation is what keeps an extreme notation tempo from carrying the axis: the
    # `LN General` Stellium benchmark chart sits at BPM* 357, where the unbounded exponential
    # reached 130x and drove InverseScore to 830 against a ladder spanning 0.2-10.
    _, factor_extreme, _ = apply_inverse_bpm_scaling(3.0, bpm=357.0)
    assert factor_extreme < 5.0
    assert factor_extreme < factor_240 * 1.5  # past the knee the multiplier barely moves


def test_inverse_score_order_reversal_resolution():
    # Typical inverse paradox:
    # 7th Dan: 142 BPM, 5.60 locked fingers (looks very hard on paper, but low BPM)
    # 10th Dan: 164 BPM, 4.80 locked fingers
    # Gamma: 178 BPM, 4.60 locked fingers
    # Zenith: 200 BPM, 5.10 locked fingers
    # Stellium: 240 BPM, 5.20 locked fingers
    #
    # Raw values: [5.60, 4.80, 4.60, 5.10, 5.20] -> Severe inversions!
    # Calibrated values MUST be strictly monotonic!

    bpms = [142.0, 164.0, 178.0, 200.0, 240.0]
    raw_locked = [5.60, 4.80, 4.60, 5.10, 5.20]

    calibrated = [
        apply_inverse_bpm_scaling(lock, bpm=bpm)[0]
        for lock, bpm in zip(raw_locked, bpms)
    ]

    for i in range(len(calibrated) - 1):
        assert calibrated[i] < calibrated[i + 1], (
            f"Step {i} failed monotonicity: {calibrated[i]} >= {calibrated[i+1]}"
        )


def test_compute_inverse_score_with_nps():
    # InverseScore = f(MeanLocked, delta_t_action, NPS)
    score_low = compute_inverse_score(mean_locked_fingers=5.6, bpm=142.0, nps=13.0)
    assert score_low <= LOW_SPEED_CAP * 1.5

    score_high = compute_inverse_score(mean_locked_fingers=5.0, bpm=240.0, nps=25.0)
    assert score_high > score_low * 3.0
