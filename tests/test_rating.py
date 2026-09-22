import math
import pytest
from proj7k.radar import TechniqueRadar
from proj7k.rating import (
    RatingOptions,
    StarRatingSynthesis,
    aggregate_p_norm,
    apply_tanh_soft_cap,
    compute_raw_strain_star_rating,
    synthesize_star_rating,
)


def test_compute_raw_strain_star_rating_anchors():
    # Anchors: 0th (m_0 ≈ 48.88) -> ~3.5★, 10th (m_10 ≈ 162.90) -> ~7.5★
    sr_0 = compute_raw_strain_star_rating(48.8776)
    assert 3.45 <= sr_0 <= 3.55

    sr_10 = compute_raw_strain_star_rating(162.9026)
    assert 7.45 <= sr_10 <= 7.55

    # Zero strain -> 0.0
    assert compute_raw_strain_star_rating(0.0) == 0.0
    assert compute_raw_strain_star_rating(-10.0) == 0.0


def test_aggregate_p_norm_pure_specialization_retention():
    """The player-side aggregate the profiler reads: no longer the chart's composition."""
    # Pure specialized chart: Dominant = 7.5★, noise in others <= 2.0★
    spec_scores = [7.5, 1.5, 2.0, 1.8, 1.0, 1.2, 1.0, 1.0]
    sr_norm = aggregate_p_norm(spec_scores, p=4.0, damping_coeff=0.08)

    # Must retain >= 95% of peak value (7.5 * 0.95 = 7.125)
    retention = sr_norm / 7.5
    assert retention >= 0.95
    assert 7.20 <= sr_norm <= 7.30

    # Even completely pure single-dimension [7.5, 0, 0, ...]
    pure_single = [7.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    sr_pure = aggregate_p_norm(pure_single, p=4.0, damping_coeff=0.08)
    assert sr_pure / 7.5 >= 0.95


def test_aggregate_p_norm_hybrid_synergy_bonus():
    """
    The profiler's player-side aggregate keeps its damping term (issue #50 moved only the
    *chart* composition to a plain maximum), so a player strong across several dimensions still
    reads above their single best one.
    """
    # Balanced hybrid chart: Dominant = 6.8★, several secondary dimensions ~6.0-6.5★
    hybrid_scores = [3.5, 6.8, 4.5, 6.5, 3.0, 5.0, 6.0, 2.5]
    sr_norm = aggregate_p_norm(hybrid_scores, p=4.0, damping_coeff=0.08)

    max_dim = 6.8
    synergy_bonus = sr_norm - max_dim
    # Synergy bonus must exceed +0.8★
    assert synergy_bonus > 0.80
    assert 8.00 <= sr_norm <= 8.15


def test_apply_tanh_soft_cap_continuity_and_ceiling():
    # Under threshold (<= 9.5), output strictly equals input
    assert apply_tanh_soft_cap(5.0) == 5.0
    assert apply_tanh_soft_cap(9.5) == 9.5

    # C^1 smoothness check near 9.5:
    eps = 1e-5
    left_slope = (apply_tanh_soft_cap(9.5) - apply_tanh_soft_cap(9.5 - eps)) / eps
    right_slope = (apply_tanh_soft_cap(9.5 + eps) - apply_tanh_soft_cap(9.5)) / eps
    assert abs(left_slope - 1.0) < 1e-4
    assert abs(right_slope - 1.0) < 1e-4

    # Above threshold, smoothly compresses and stays strictly bounded at or below 12.5★
    assert 9.5 < apply_tanh_soft_cap(10.5) < 10.5
    assert 9.5 < apply_tanh_soft_cap(15.0) < 12.5
    assert apply_tanh_soft_cap(100.0) <= 12.5
    assert apply_tanh_soft_cap(10000.0) <= 12.5

    # Monotonicity test: for any s1 < s2, f(s1) <= f(s2) (with strict inequality before float saturation)
    test_inputs = [0.0, 3.5, 7.5, 9.5, 10.0, 11.0, 13.0, 15.0, 18.0, 25.0]
    outputs = [apply_tanh_soft_cap(x) for x in test_inputs]
    for i in range(len(outputs) - 1):
        assert outputs[i] < outputs[i + 1]


def test_synthesize_star_rating_composes_as_the_largest_technique_star():
    """
    The chart rating is the largest of the eight absolute technique stars (issue #50).

    It used to be an extremum-dominant p-norm of *shares* of the strain rating, which is why
    this fixture — one strong axis and seven weak ones — came out at 7.2-7.3 rather than at its
    dominant score: the damping term was a bonus the weaker axes contributed. With the axes on
    one absolute scale there is no bonus to collect: the chart is as hard as its hardest
    technique, and `synergy_bonus` is what the composition added on top of the dominant axis'
    own score, which is now nothing when the dominant axis is the largest.
    """
    radar = TechniqueRadar(
        jack=7.5,
        tech=1.5,
        speed=2.0,
        stream=1.8,
        ln_general=1.0,
        ln_tech=1.2,
        ln_inverse=1.0,
        ln_release=1.0,
        dominant_technique="jack",
        dominant_score=7.5,
    )
    res = synthesize_star_rating(radar, p90_strain=162.9)
    assert isinstance(res, StarRatingSynthesis)
    assert res.uncompressed_rating == 7.5
    assert res.star_rating == 7.5
    assert res.dominant_technique == "jack"
    assert res.dominant_score == 7.5
    assert res.synergy_bonus == 0.0
    # The strain rating is still reported, and still on the same scale — it is simply not part
    # of the composition any more.
    assert 7.45 <= res.raw_strain_rating <= 7.55


def test_synthesize_star_rating_extreme_limit():
    # Extreme chart: 14★ across all dimensions
    radar = TechniqueRadar(
        jack=14.0,
        tech=14.0,
        speed=14.0,
        stream=14.0,
        ln_general=14.0,
        ln_tech=14.0,
        ln_inverse=14.0,
        ln_release=14.0,
        dominant_technique="stream",
        dominant_score=14.0,
    )
    res = synthesize_star_rating(radar, p90_strain=500.0)
    assert res.star_rating <= 12.5
    assert res.uncompressed_rating > res.star_rating


def test_synthesize_star_rating_empty_radar():
    radar = TechniqueRadar(
        jack=0.0,
        tech=0.0,
        speed=0.0,
        stream=0.0,
        ln_general=0.0,
        ln_tech=0.0,
        ln_inverse=0.0,
        ln_release=0.0,
        dominant_technique="None",
        dominant_score=0.0,
    )
    # If no physical strain either
    res = synthesize_star_rating(radar, p90_strain=0.0)
    assert res.star_rating == 0.0
    assert res.uncompressed_rating == 0.0

    # If physical strain exists, fallback to raw strain
    res_strain = synthesize_star_rating(radar, p90_strain=48.8776)
    assert 3.45 <= res_strain.star_rating <= 3.55


def test_deterministic_output():
    # Same inputs must produce exact bitwise deterministic floating point outputs
    radar = TechniqueRadar(
        jack=5.5,
        tech=6.2,
        speed=4.8,
        stream=6.0,
        ln_general=3.0,
        ln_tech=4.0,
        ln_inverse=2.0,
        ln_release=1.5,
        dominant_technique="tech",
        dominant_score=6.2,
    )
    res1 = synthesize_star_rating(radar, p90_strain=100.0)
    res2 = synthesize_star_rating(radar, p90_strain=100.0)
    assert res1.star_rating == res2.star_rating
    assert res1.uncompressed_rating == res2.uncompressed_rating
    assert res1.raw_strain_rating == res2.raw_strain_rating
    assert res1.synergy_bonus == res2.synergy_bonus

    d = res1.to_dict()
    assert d["star_rating"] == res1.star_rating
    assert d["dominant_technique"] == "tech"


def test_aggregate_p_norm_dict_and_empty():
    assert aggregate_p_norm([], p=4.0, damping_coeff=0.08) == 0.0
    d_scores = {"jack": 6.0, "stream": 5.8, "tech": 5.5}
    val = aggregate_p_norm(d_scores, p=4.0, damping_coeff=0.08)
    assert val > 6.0
