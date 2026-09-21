from typing import List

import pytest

from proj7k.calibration import (
    DEFAULT_CALIBRATION,
    StrainStarCalibration,
    compute_methodology_fingerprint,
)
from proj7k.difficulty import (
    DifficultyOptions,
    current_engine_version,
    evaluate_intrinsic_difficulty,
)
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.radar import RadarOptions
from proj7k.rating import RatingOptions
from proj7k.strain import StrainOptions, compute_raw_strain_star_rating


def _make_sample_beatmap(hit_objects: List[HitObject]) -> Beatmap7K:
    return Beatmap7K(
        title="Test Calibration Beatmap",
        artist="Test Artist",
        creator="Tester",
        version="Test Version",
        hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=400.0, meter=4, uninherited=True)],
    )


def _dense_chart() -> Beatmap7K:
    """Dense 7-lane chart with a clear jack bias and residual signal in the other dims."""
    hos: List[HitObject] = []
    for i in range(200):
        t = i * 120.0
        hos.append(HitObject(column=0, time=t, note_type=NoteType.RICE))
        hos.append(HitObject(column=1, time=t + 60.0, note_type=NoteType.RICE))
        if i % 2 == 0:
            hos.append(HitObject(column=3, time=t, note_type=NoteType.RICE))
    return _make_sample_beatmap(hos)


def test_rating_options_defaults_come_from_canonical_calibration():
    opts = RatingOptions()
    assert opts.strain_a == DEFAULT_CALIBRATION.strain_a
    assert opts.strain_b == DEFAULT_CALIBRATION.strain_b
    assert opts.strain_exp == DEFAULT_CALIBRATION.strain_exp
    assert opts.driver_backpressure_exp == DEFAULT_CALIBRATION.driver_backpressure_exp
    assert opts.calibration == DEFAULT_CALIBRATION


def test_anchor_law_has_one_definition():
    # The strain-side helper and the calibration value object must agree bit for bit.
    for s_base in (0.0, -5.0, 1.0, 48.8776, 162.9026, 500.0):
        assert compute_raw_strain_star_rating(s_base) == DEFAULT_CALIBRATION.star_rating_from_strain(s_base)

    custom = StrainStarCalibration(strain_a=0.4, strain_b=0.2, strain_exp=0.5)
    assert compute_raw_strain_star_rating(100.0, a=0.4, b=0.2, exp=0.5) == custom.star_rating_from_strain(100.0)


def test_radar_scale_follows_rating_options_calibration():
    """
    Radar scores are expressed in the star scale handed to the rating synthesizer, so a
    non-default RatingOptions must move the radar vector too (previously it kept its own
    hardcoded copy of the anchor law and silently disagreed).
    """
    bm = _dense_chart()
    baseline = evaluate_intrinsic_difficulty(bm)
    assert baseline.radar.dominant_score > 0.0

    # The dominant dimension carries the full anchor scale: its driver ratio is 1.0.
    sr_base = DEFAULT_CALIBRATION.star_rating_from_strain(baseline.strain_profile.p90_strain)
    assert baseline.radar.dominant_score == pytest.approx(min(12.0, sr_base), abs=1e-12)

    rescaled_options = DifficultyOptions(
        rating_options=RatingOptions(strain_a=DEFAULT_CALIBRATION.strain_a * 2.0)
    )
    rescaled = evaluate_intrinsic_difficulty(bm, options=rescaled_options)

    expected_sr_base = rescaled_options.rating_options.calibration.star_rating_from_strain(
        rescaled.strain_profile.p90_strain
    )
    assert rescaled.radar.dominant_score == pytest.approx(min(12.0, expected_sr_base), abs=1e-12)
    assert rescaled.star_rating != baseline.star_rating
    # Every dimension of the vector is rescaled, not just the dominant one.
    assert abs(rescaled.radar.tech - baseline.radar.tech) > 1.0
    assert abs(rescaled.radar.stream - baseline.radar.stream) > 1.0


def test_backpressure_exponent_is_injectable():
    # A larger back-pressure exponent compresses the non-dominant dimensions harder,
    # while the dominant dimension (ratio 1.0) keeps the full anchor scale.
    bm = _dense_chart()
    baseline = evaluate_intrinsic_difficulty(bm)
    sharper_options = DifficultyOptions(
        rating_options=RatingOptions(driver_backpressure_exp=1.0)
    )
    sharper = evaluate_intrinsic_difficulty(bm, options=sharper_options)

    assert sharper.radar.dominant_score == pytest.approx(baseline.radar.dominant_score, abs=1e-12)
    assert sharper.radar.tech < baseline.radar.tech
    assert sharper.radar.speed <= baseline.radar.speed


def test_methodology_fingerprint_tracks_calibration_constants():
    baseline = RatingOptions().methodology_fingerprint
    assert len(baseline) == 8
    assert baseline == RatingOptions().methodology_fingerprint

    for kwargs in (
        {"strain_a": DEFAULT_CALIBRATION.strain_a * 1.01},
        {"strain_b": DEFAULT_CALIBRATION.strain_b + 0.001},
        {"strain_exp": 0.66},
        {"driver_backpressure_exp": 1.0},
        {"p_norm": 3.0},
        {"damping_coeff": 0.09},
        {"soft_cap_threshold": 9.0},
        {"soft_cap_scale": 2.0},
    ):
        assert RatingOptions(**kwargs).methodology_fingerprint != baseline, kwargs


def test_current_engine_version_is_calibration_derived():
    version = current_engine_version()
    assert version.startswith("v")
    assert len(version) == 9
    assert version == current_engine_version()  # stable across calls

    # Raw helper keeps the digest stable regardless of key insertion order.
    assert compute_methodology_fingerprint(a=1, b=2) == compute_methodology_fingerprint(b=2, a=1)
    assert compute_methodology_fingerprint(a=1, b=2) != compute_methodology_fingerprint(a=2, b=2)


def test_engine_fingerprint_covers_every_star_rating_constant():
    """
    The injected version must change whenever anything that moves a star rating changes,
    otherwise a calibration rewrite would leave stale ratings in the game database.
    """
    baseline = DifficultyOptions().engine_fingerprint

    moved = {
        "star anchor": DifficultyOptions(rating_options=RatingOptions(strain_a=0.5)),
        "back-pressure": DifficultyOptions(rating_options=RatingOptions(driver_backpressure_exp=1.0)),
        "aggregation": DifficultyOptions(rating_options=RatingOptions(p_norm=3.0)),
        "radar options": DifficultyOptions(radar_options=RadarOptions(jack_threshold_ms=230.0)),
        "strain options": DifficultyOptions(strain_options=StrainOptions(tau_time_constant_s=1.5)),
    }
    for label, options in moved.items():
        assert options.engine_fingerprint != baseline, label

    # Shared physical constants are part of the fingerprint too.
    import proj7k.difficulty as difficulty_module
    import proj7k.physics as physics

    assert difficulty_module.CHORDJACK_STEP_INTERVAL_MS == physics.CHORDJACK_STEP_INTERVAL_MS
    assert difficulty_module.JACK_INTERVAL_PENALTY_MS == physics.JACK_INTERVAL_PENALTY_MS
    assert difficulty_module.ANTIPHASE_ONSET_WINDOW_S == physics.ANTIPHASE_ONSET_WINDOW_S
    assert difficulty_module.BRACKET_PHASE_INVERSION_WINDOW_MS == physics.BRACKET_PHASE_INVERSION_WINDOW_MS
    assert difficulty_module.SPEED_BURST_INTERVAL_MS == physics.SPEED_BURST_INTERVAL_MS

    original = difficulty_module.CHORDJACK_STEP_INTERVAL_MS
    try:
        difficulty_module.CHORDJACK_STEP_INTERVAL_MS = original + 1.0
        assert DifficultyOptions().engine_fingerprint != baseline
    finally:
        difficulty_module.CHORDJACK_STEP_INTERVAL_MS = original
