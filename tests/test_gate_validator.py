import math
from types import SimpleNamespace
from typing import List

import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.engine.skills import SKILLS
from proj7k.downscaler.validator import DualGateValidator, ValidationResult, compute_skill_cosine_similarity
from proj7k.downscaler.mapper import DanTarget


def _make_dummy_beatmap(hit_objects: List[HitObject]) -> Beatmap7K:
    return Beatmap7K(
        title="Validator Test",
        artist="Test",
        creator="Test",
        version="Test",
        hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)],
    )


def _profile(**stars: float) -> SimpleNamespace:
    """A stand-in carrying only what the cosine reads: the eight skills' stars, in the engine's order."""
    return SimpleNamespace(skills={name: SimpleNamespace(stars=stars.get(name, 0.0)) for name in SKILLS})


def test_compute_skill_cosine_similarity():
    p1 = _profile(rc_jack=5.0, rc_tech=1.0, rc_speed=1.0, rc_stamina=0.5)
    # Scaled version (pure magnitude change, same direction)
    p2 = _profile(rc_jack=3.5, rc_tech=0.7, rc_speed=0.7, rc_stamina=0.35)
    assert pytest.approx(1.0, abs=1e-5) == compute_skill_cosine_similarity(p1, p2)

    # Orthogonal profile (jack vs ln_inverse)
    p3 = _profile(ln_inverse=5.0)
    assert pytest.approx(0.0, abs=1e-5) == compute_skill_cosine_similarity(p1, p3)


def test_validator_passes_when_conserved():
    # Build Chordjack pattern (columns 0, 2 with fast repetition)
    hos_orig: List[HitObject] = []
    for i in range(40):
        t = i * 120.0
        hos_orig.append(HitObject(column=0, time=t, note_type=NoteType.RICE))
        hos_orig.append(HitObject(column=2, time=t, note_type=NoteType.RICE))
    bm_orig = _make_dummy_beatmap(hos_orig)

    # Thinned chordjack (keep col 0 every step, col 2 every other step)
    hos_downscaled: List[HitObject] = []
    for i in range(40):
        t = i * 120.0
        hos_downscaled.append(HitObject(column=0, time=t, note_type=NoteType.RICE))
        if i % 2 == 0:
            hos_downscaled.append(HitObject(column=2, time=t, note_type=NoteType.RICE))
    bm_downscaled = _make_dummy_beatmap(hos_downscaled)

    validator = DualGateValidator(min_cosine_similarity=0.80)
    target = DanTarget(
        target_dan="7th",
        target_sr=6.1,
        target_D=27.0,
        dominant_skill="jack",
        features={"hold_pct": 0.0, "avg_nps": 20.0},
    )
    res = validator.validate(bm_orig, bm_downscaled, target=target)

    assert isinstance(res, ValidationResult)
    assert res.cosine_similarity >= 0.80
    assert res.dominant_conserved is True
    assert res.dominant_technique_orig == res.dominant_technique_downscaled
    assert res.gate1_passed is True
    assert res.gate2_passed is True
    assert res.passed is True


def test_validator_fails_when_dominant_technique_shifts():
    # Original is LN dominant
    hos_orig = [
        HitObject(column=c, time=0.0, note_type=NoteType.LN, end_time=2000.0)
        for c in range(6)
    ]
    bm_orig = _make_dummy_beatmap(hos_orig)

    # Downscaled has zero LN, completely switched to single rice notes
    hos_downscaled = [
        HitObject(column=i % 4, time=i * 250.0, note_type=NoteType.RICE)
        for i in range(20)
    ]
    bm_downscaled = _make_dummy_beatmap(hos_downscaled)

    validator = DualGateValidator(min_cosine_similarity=0.80)
    res = validator.validate(bm_orig, bm_downscaled)

    assert res.dominant_conserved is False
    assert res.passed is False
    assert res.gate1_passed is False
