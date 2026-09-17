import math
import pytest
from typing import List

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.radar import TechniqueRadar
from proj7k.downscaler.validator import DualGateValidator, ValidationResult, compute_radar_cosine_similarity
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


def test_compute_radar_cosine_similarity():
    r1 = TechniqueRadar(
        jack=5.0, tech=1.0, speed=1.0, stream=0.5,
        ln_general=0.0, ln_tech=0.0, ln_inverse=0.0, ln_release=0.0,
        dominant_technique="jack", dominant_score=5.0,
    )
    # Scaled version (pure magnitude change, same direction)
    r2 = TechniqueRadar(
        jack=3.5, tech=0.7, speed=0.7, stream=0.35,
        ln_general=0.0, ln_tech=0.0, ln_inverse=0.0, ln_release=0.0,
        dominant_technique="jack", dominant_score=3.5,
    )
    sim = compute_radar_cosine_similarity(r1, r2)
    assert pytest.approx(1.0, abs=1e-5) == sim

    # Orthogonal radar (jack vs ln_inverse)
    r3 = TechniqueRadar(
        jack=0.0, tech=0.0, speed=0.0, stream=0.0,
        ln_general=0.0, ln_tech=0.0, ln_inverse=5.0, ln_release=0.0,
        dominant_technique="ln_inverse", dominant_score=5.0,
    )
    sim_ortho = compute_radar_cosine_similarity(r1, r3)
    assert pytest.approx(0.0, abs=1e-5) == sim_ortho


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
        target_strain=115.8,
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
