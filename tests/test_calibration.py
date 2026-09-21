import dataclasses
from typing import Any, List

import pytest

from proj7k import physics, scaling, strain
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
from proj7k.features import FeatureOptions
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
    # Every dimension of the vector is rescaled, not just the dominant one. Asserted as a ratio
    # rather than a star delta: what the calibration scales is each score's share of the anchor
    # law, so the absolute move depends on how big that dimension happened to be — a fixed
    # number of stars would be an assertion about the fixture's balance, not about the coupling.
    assert rescaled.radar.tech > baseline.radar.tech * 1.5
    assert rescaled.radar.stream > baseline.radar.stream * 1.5


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


#: The option object each `DifficultyOptions` field carries, and the attribute it is passed to.
OPTION_HOMES = {
    RatingOptions: "rating_options",
    RadarOptions: "radar_options",
    StrainOptions: "strain_options",
    FeatureOptions: "feature_options",
}

#: Every calibration block that reports its own constants, keyed by the fingerprint argument it
#: is folded in under.
CALIBRATION_BLOCKS = {"physics": physics, "scaling": scaling, "strain_constants": strain}


def _different(value: Any) -> Any:
    """
    A value that differs from `value`, whatever the field's type is. A type this cannot perturb
    is a type whose coverage nothing is checking, so it fails loudly rather than passing over it.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + "_perturbed"
    if value is None:
        return 1
    if isinstance(value, tuple):
        return value + (_different(value[0]),) if value else (1,)
    raise AssertionError(
        f"no perturbation is defined for {type(value).__name__}: add one so this field's "
        f"fingerprint coverage is actually exercised"
    )


# One case per field, derived from the dataclasses: a newly declared field is a new case, so a
# constant cannot be added to an option object and quietly skip the fingerprint.
@pytest.mark.parametrize(
    ("options_class", "field_name"),
    [
        pytest.param(cls, f.name, id=f"{cls.__name__}.{f.name}")
        for cls in OPTION_HOMES
        for f in dataclasses.fields(cls)
    ],
)
def test_every_option_field_moves_the_engine_fingerprint(options_class, field_name):
    """
    The injected version must change whenever anything that moves a star rating changes,
    otherwise a calibration rewrite would leave stale ratings in the game database. The option
    objects are folded in whole, so this is the assertion that the folding is actually total.
    """
    baseline = DifficultyOptions().engine_fingerprint
    defaults = options_class()
    perturbed = options_class(**{field_name: _different(getattr(defaults, field_name))})

    moved = DifficultyOptions(**{OPTION_HOMES[options_class]: perturbed})

    assert moved.engine_fingerprint != baseline, (
        f"{options_class.__name__}.{field_name} is read by the engine but not by the fingerprint"
    )


@pytest.mark.parametrize(
    ("block_name", "constant_name"),
    [
        pytest.param(name, constant, id=f"{name}.{constant}")
        for name, module in CALIBRATION_BLOCKS.items()
        for constant in module.CALIBRATION_CONSTANTS
    ],
)
def test_every_calibration_constant_moves_the_engine_version(
    block_name: str, constant_name: str, monkeypatch
):
    """
    The version stamped into injected metadata, not just the fingerprint behind it: a constant
    that moves a star rating without moving this string leaves stale ratings in osu!lazer
    (ADR-0014), which is the failure issue #48 exists to close.
    """
    baseline = current_engine_version()
    module = CALIBRATION_BLOCKS[block_name]

    monkeypatch.setattr(module, constant_name, _different(getattr(module, constant_name)))
    current_engine_version.cache_clear()

    assert current_engine_version() != baseline, f"{block_name}.{constant_name} is not covered"


def test_the_version_is_the_default_fingerprint():
    """
    `current_engine_version` is the cached projection of the default fingerprint. Pinning the
    identity keeps the two from drifting apart: the coverage assertions above are split between
    them, and would otherwise be measuring different things.
    """
    current_engine_version.cache_clear()
    assert current_engine_version() == f"v{DifficultyOptions().engine_fingerprint}"
