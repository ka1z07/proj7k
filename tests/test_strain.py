from typing import List
import time
import math
import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.strain import (
    StrainTimeseriesProfile,
    StrainOptions,
    compute_dual_hand_strain,
    _calculate_percentile,
)


def _make_sample_beatmap(hit_objects: List[HitObject]) -> Beatmap7K:
    return Beatmap7K(
        title="Test Strain Beatmap",
        artist="Test Artist",
        creator="Tester",
        version="Test Version",
        hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)],
    )


def test_calculate_percentile_edge_cases():
    assert _calculate_percentile([], 90.0) == 0.0
    assert _calculate_percentile([42.0], 90.0) == 42.0
    assert _calculate_percentile([10.0, 20.0], 50.0) == 15.0


def test_empty_beatmap_strain():
    bm = _make_sample_beatmap([])
    profile = compute_dual_hand_strain(bm)

    assert isinstance(profile, StrainTimeseriesProfile)
    assert profile.p90_strain == 0.0
    assert profile.p95_strain == 0.0
    assert profile.peak_strain == 0.0
    assert len(profile.times) >= 1
    assert profile.combined_strain == [0.0] * len(profile.times)

    d = profile.to_dict()
    assert d["p90_strain"] == 0.0
    assert d["p95_strain"] == 0.0
    assert d["peak_strain"] == 0.0
    assert "left_hand_strain" in d
    assert "right_hand_strain" in d
    assert "combined_strain" in d


def test_one_hand_asymmetry():
    # Left hand (col 0, 1) has dense notes; right hand (col 4, 5, 6) has none
    hos: List[HitObject] = []
    for i in range(40):
        hos.append(HitObject(column=0, time=i * 100.0, note_type=NoteType.RICE))
        hos.append(HitObject(column=1, time=i * 100.0 + 50.0, note_type=NoteType.RICE))

    bm = _make_sample_beatmap(hos)
    profile = compute_dual_hand_strain(bm)

    max_left = max(profile.left_hand_strain)
    max_right = max(profile.right_hand_strain)

    assert max_left > 5.0
    assert max_right == 0.0
    # Combined strain is L2 norm: sqrt(s_l^2 + 0^2) == s_l
    for s_l, s_comb in zip(profile.left_hand_strain, profile.combined_strain):
        assert math.isclose(s_comb, s_l, rel_tol=1e-5, abs_tol=1e-5)


def test_center_column_sharing():
    # Column 3 should be shared 50% between left and right hand
    hos = [HitObject(column=3, time=i * 125.0, note_type=NoteType.RICE) for i in range(30)]
    bm = _make_sample_beatmap(hos)
    profile = compute_dual_hand_strain(bm)

    assert len(profile.left_hand_strain) == len(profile.right_hand_strain)
    for s_l, s_r in zip(profile.left_hand_strain, profile.right_hand_strain):
        assert math.isclose(s_l, s_r, rel_tol=1e-5, abs_tol=1e-5)

    assert profile.peak_strain > 0.0
    for s_l, s_comb in zip(profile.left_hand_strain, profile.combined_strain):
        assert math.isclose(s_comb, math.sqrt(2) * s_l, rel_tol=1e-5, abs_tol=1e-5)


def test_center_column_jack_penalty():
    # Center column rapid jack (100ms < 160ms) should increase strain symmetrically
    hos = [HitObject(column=3, time=i * 100.0, note_type=NoteType.RICE) for i in range(20)]
    bm = _make_sample_beatmap(hos)
    profile = compute_dual_hand_strain(bm)

    for s_l, s_r in zip(profile.left_hand_strain, profile.right_hand_strain):
        assert math.isclose(s_l, s_r, rel_tol=1e-5, abs_tol=1e-5)
    assert profile.peak_strain > 0.0


def test_rest_measure_decay():
    # Dense burst for 1 second, followed by 4 seconds of silence
    hos = [HitObject(column=0, time=i * 50.0, note_type=NoteType.RICE) for i in range(20)]
    # Place a single anchor note at 5000ms to extend duration
    hos.append(HitObject(column=0, time=5000.0, note_type=NoteType.RICE))
    bm = _make_sample_beatmap(hos)

    tau = 1.2
    options = StrainOptions(tau_time_constant_s=tau, step_s=0.25)
    profile = compute_dual_hand_strain(bm, options=options)

    # After t=1.0s (burst end), let's track pure exponential decay
    # Pick t1 = 2.0s and t2 = 3.25s during silence (multiples of step_s = 0.25)
    idx1 = profile.times.index(2.0)
    idx2 = profile.times.index(3.25)
    s1 = profile.left_hand_strain[idx1]
    s2 = profile.left_hand_strain[idx2]

    # Theoretical decay ratio: exp(-(3.25 - 2.0) / 1.2) = exp(-1.25 / 1.2)
    expected_ratio = math.exp(-1.25 / tau)
    actual_ratio = s2 / s1
    assert math.isclose(actual_ratio, expected_ratio, rel_tol=0.01)


def test_gap1_unpressed_middle_finger():
    # Case A: col 0 and 2 hit together with col 1 empty (Genuine [gap:1] 抠空中指)
    hos_gap1 = [
        HitObject(column=0, time=i * 300.0, note_type=NoteType.RICE) for i in range(10)
    ] + [
        HitObject(column=2, time=i * 300.0, note_type=NoteType.RICE) for i in range(10)
    ]
    bm_gap1 = _make_sample_beatmap(hos_gap1)
    prof_gap1 = compute_dual_hand_strain(bm_gap1)

    # Case B: col 0, 1, 2 all hit together (3-finger chord, middle finger NOT empty)
    hos_chord3 = [
        HitObject(column=0, time=i * 300.0, note_type=NoteType.RICE) for i in range(10)
    ] + [
        HitObject(column=1, time=i * 300.0, note_type=NoteType.RICE) for i in range(10)
    ] + [
        HitObject(column=2, time=i * 300.0, note_type=NoteType.RICE) for i in range(10)
    ]
    bm_chord3 = _make_sample_beatmap(hos_chord3)
    prof_chord3 = compute_dual_hand_strain(bm_chord3)

    # Gap1 option toggled off to isolate baseline physical load
    opt_no_gap = StrainOptions(gap1_weight=0.0)
    prof_gap1_baseline = compute_dual_hand_strain(bm_gap1, options=opt_no_gap)

    # Case A should have gap1 penalty applied (> baseline)
    assert prof_gap1.peak_strain > prof_gap1_baseline.peak_strain


def test_jack_and_gap1_strain_enhancement():
    # Baseline: smooth stream with spacing 200ms (> 160ms jack threshold, no gap1)
    base_hos: List[HitObject] = []
    for i in range(20):
        col = i % 3
        base_hos.append(HitObject(column=col, time=i * 200.0, note_type=NoteType.RICE))
    bm_base = _make_sample_beatmap(base_hos)
    prof_base = compute_dual_hand_strain(bm_base)

    # Enhanced: fast jack on column 0 (100ms < 160ms) and simultaneous [gap:1] on cols 0 & 2
    jack_gap_hos: List[HitObject] = []
    for i in range(10):
        t = i * 200.0
        # Jack on col 0: 0ms and 100ms
        jack_gap_hos.append(HitObject(column=0, time=t, note_type=NoteType.RICE))
        jack_gap_hos.append(HitObject(column=0, time=t + 100.0, note_type=NoteType.RICE))
        # Simultaneous gap1 on col 2 (col 1 remains empty)
        jack_gap_hos.append(HitObject(column=2, time=t, note_type=NoteType.RICE))

    bm_jack_gap = _make_sample_beatmap(jack_gap_hos)
    prof_jack_gap = compute_dual_hand_strain(bm_jack_gap)

    # The jack and gap1 pattern should generate significantly higher peak and P90 strain
    assert prof_jack_gap.peak_strain > prof_base.peak_strain * 1.5
    assert prof_jack_gap.p90_strain > prof_base.p90_strain * 1.3

    # Symmetric verification on right hand (cols 4, 5, 6)
    rh_jack_gap_hos: List[HitObject] = []
    for i in range(10):
        t = i * 200.0
        rh_jack_gap_hos.append(HitObject(column=4, time=t, note_type=NoteType.RICE))
        rh_jack_gap_hos.append(HitObject(column=4, time=t + 100.0, note_type=NoteType.RICE))
        rh_jack_gap_hos.append(HitObject(column=6, time=t, note_type=NoteType.RICE))
    bm_rh = _make_sample_beatmap(rh_jack_gap_hos)
    prof_rh = compute_dual_hand_strain(bm_rh)
    assert prof_rh.peak_strain > prof_base.peak_strain * 1.5
    assert max(prof_rh.right_hand_strain) > 0.0
    assert max(prof_rh.left_hand_strain) == 0.0


def test_quantiles_ordering_and_profile_contract():
    hos = [HitObject(column=i % 7, time=i * 70.0, note_type=NoteType.RICE) for i in range(60)]
    bm = _make_sample_beatmap(hos)
    prof = compute_dual_hand_strain(bm)

    assert prof.p90_strain <= prof.p95_strain <= prof.peak_strain
    assert prof.step_seconds == 0.25
    assert len(prof.times) == len(prof.left_hand_strain) == len(prof.right_hand_strain) == len(prof.combined_strain)

    data = prof.to_dict()
    assert isinstance(data["times"], list)
    assert isinstance(data["p90_strain"], float)
    assert isinstance(data["p95_strain"], float)
    assert isinstance(data["peak_strain"], float)


def test_locked_finger_counts_match_per_sample_scan():
    """
    The precomputed difference-array step function must reproduce the naive per-sample
    interval scan exactly, including nested/overlapping holds, holds whose endpoints land
    exactly on a sample, and holds reaching past either end of the grid.
    """
    from proj7k.strain import _build_locked_finger_counts, LEFT_HAND_LANES, RIGHT_HAND_LANES

    times_s = [round(k * 0.25, 5) for k in range(13)]  # 0.00 .. 3.00
    col_lns = {
        0: [(0.0, 1.0), (0.5, 1.25)],          # overlapping
        1: [(1.25, 1.25), (2.0, 5.0)],         # instant hit + running past the grid end
        2: [],                                  # never held
        3: [(-1.0, 0.0)],                       # ends exactly on the first sample
        4: [(0.3, 0.4)],                        # entirely between two samples
        5: [(3.5, 4.0)],                        # entirely past the grid
        6: [(0.25, 2.75), (0.5, 1.0)],          # nested
    }

    for columns in (LEFT_HAND_LANES, RIGHT_HAND_LANES, tuple(range(7))):
        fast = _build_locked_finger_counts(times_s, col_lns, columns)
        naive = [
            sum(1 for c in columns if any(st <= t <= et for st, et in col_lns[c]))
            for t in times_s
        ]
        assert fast == naive, columns

    assert _build_locked_finger_counts([], col_lns, LEFT_HAND_LANES) == []


def test_deterministic_and_performance():
    # 1000 notes pattern
    hos = [
        HitObject(column=(i * 3) % 7, time=i * 40.0, note_type=NoteType.RICE)
        for i in range(1000)
    ]
    bm = _make_sample_beatmap(hos)

    t0 = time.perf_counter()
    prof1 = compute_dual_hand_strain(bm)
    duration = time.perf_counter() - t0

    # Single beatmap strain calculation should easily execute under 20ms (< 0.020s)
    assert duration < 0.020, f"Strain calculation took too long: {duration:.4f}s"

    prof2 = compute_dual_hand_strain(bm)
    assert prof1.p90_strain == prof2.p90_strain
    assert prof1.peak_strain == prof2.peak_strain
    assert prof1.combined_strain == prof2.combined_strain
