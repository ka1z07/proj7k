import math
from typing import List
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.strain import (
    StrainOptions,
    compute_dual_hand_strain,
    compute_judgment_overlap_buffer,
    compute_high_speed_scaling_factor,
    compute_micro_speed_burst,
)


def _make_sample_beatmap(
    hit_objects: List[HitObject],
    bpm: float = 150.0,
) -> Beatmap7K:
    beat_length = 60000.0 / bpm if bpm > 0 else 500.0
    return Beatmap7K(
        title="Test Modulation Beatmap",
        artist="Test Artist",
        creator="Tester",
        version="Test Version",
        hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=beat_length, meter=4, uninherited=True)],
    )


def test_judgment_overlap_buffer_and_scaling():
    # At 240 BPM, 16th note delta_t = 60000 / (240 * 4) = 62.5ms
    # With W_judg = 38ms, eta = min(0.75, 38 / 62.5) = 0.608
    eta = compute_judgment_overlap_buffer(bpm=240.0, w_judg_ms=38.0)
    assert math.isclose(eta, 38.0 / 62.5, rel_tol=1e-3)
    assert eta <= 0.75

    # Scaling factor at 240 BPM:
    # 1.0 + 0.65 * ((240 - 180) / 40) * (1.0 - 0.45 * eta)
    scaling_240 = compute_high_speed_scaling_factor(bpm=240.0, eta=eta)
    buffer_factor = 1.0 - 0.45 * eta
    expected_scaling = 1.0 + 0.65 * (60.0 / 40.0) * buffer_factor
    assert math.isclose(scaling_240, expected_scaling, rel_tol=1e-3)

    # Scaling factor at low speed <= 145 BPM:
    # (120 / 145)^1.8 * 0.75
    scaling_120 = compute_high_speed_scaling_factor(bpm=120.0, eta=0.0)
    expected_120 = ((120.0 / 145.0) ** 1.8) * 0.75
    assert math.isclose(scaling_120, expected_120, rel_tol=1e-3)

    # Transition zone: 145 < BPM < 180
    scaling_160 = compute_high_speed_scaling_factor(bpm=160.0, eta=0.0)
    expected_160 = 0.75 + 0.25 * ((160.0 - 145.0) / 35.0)
    assert math.isclose(scaling_160, expected_160, rel_tol=1e-3)


def test_micro_speed_burst_calculation():
    # Hits spaced at 50ms (interval < 110ms) -> triggers speed burst strain
    # ((110 - 50) / 50)^1.35 = 1.2^1.35 approx 1.278
    hits_fast = [0.0, 0.050, 0.100, 0.150]
    burst_fast = compute_micro_speed_burst(hits_fast)
    expected_single = math.pow((110.0 - 50.0) / 50.0, 1.35)
    assert math.isclose(burst_fast, 3 * expected_single, rel_tol=1e-3)

    # Hits spaced at 200ms (> 110ms) -> burst strain is 0.0
    hits_slow = [0.0, 0.200, 0.400]
    burst_slow = compute_micro_speed_burst(hits_slow)
    assert burst_slow == 0.0


def test_micro_speed_burst_boosts_regular_speed():
    # Rapid single-hand alternating stream at 300 BPM (tap spacing = 50ms)
    # Compare with identical note count at 120 BPM (tap spacing = 125ms)
    hos_fast: List[HitObject] = []
    for i in range(20):
        col = i % 3
        hos_fast.append(HitObject(column=col, time=i * 50.0, note_type=NoteType.RICE))
    bm_fast = _make_sample_beatmap(hos_fast, bpm=300.0)

    # With burst enabled vs disabled
    opt_burst_on = StrainOptions(speed_burst_weight=0.30)
    opt_burst_off = StrainOptions(speed_burst_weight=0.0)

    prof_on = compute_dual_hand_strain(bm_fast, options=opt_burst_on)
    prof_off = compute_dual_hand_strain(bm_fast, options=opt_burst_off)

    # Micro speed burst should significantly elevate the strain for 300 BPM tapping
    assert prof_on.peak_strain > prof_off.peak_strain * 1.25


def test_cognitive_impedance_saturation_in_ln_inverse():
    # 240 BPM high-density LN inverse: 2 locked fingers per hand with continuous tapping
    # Left hand: cols 0 & 1 held as LN for 2000ms, col 2 tapped rapidly every 62.5ms
    hos_ln: List[HitObject] = [
        HitObject(column=0, time=0.0, note_type=NoteType.LN, end_time=2000.0),
        HitObject(column=1, time=0.0, note_type=NoteType.LN, end_time=2000.0),
    ]
    for i in range(30):
        hos_ln.append(HitObject(column=2, time=i * 62.5, note_type=NoteType.RICE))

    bm_ln = _make_sample_beatmap(hos_ln, bpm=240.0)

    # Evaluate with default options (alpha=0.6, gamma=1.2, eta buffering active)
    profile = compute_dual_hand_strain(bm_ln)

    # Peak strain should be high but comfortably bounded (finite, non-exploding)
    assert 10.0 < profile.peak_strain < 250.0
    assert profile.p90_strain > 5.0


def test_antiphase_articulation_and_bpm_option():
    # Antiphase: press on col 1 at 500ms, while col 0 releases at 505ms (within 25ms)
    hos = [
        HitObject(column=0, time=0.0, note_type=NoteType.LN, end_time=505.0),
        HitObject(column=1, time=500.0, note_type=NoteType.RICE),
    ]
    bm = _make_sample_beatmap(hos)
    opt = StrainOptions(bpm=175.0)
    profile = compute_dual_hand_strain(bm, options=opt)
    assert profile.peak_strain > 0.0


def test_zero_physical_density_zero_impedance():
    # If no notes are struck in a window, momentary difficulty D_hand must be 0
    # Even if LN bodies were held, empty active notes -> D_hand = 0
    bm_empty = _make_sample_beatmap([])
    profile = compute_dual_hand_strain(bm_empty)

    assert profile.peak_strain == 0.0
    assert profile.p90_strain == 0.0
    assert all(s == 0.0 for s in profile.combined_strain)
