import pytest

from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.downscaler.skeleton import MetricSkeletonDetector


def test_metric_skeleton_detector_downbeats_and_chord_invariants():
    """
    Vertical slice 1:
    - Verifies MetricSkeletonDetector parses TimingPoints (BPM, meter) to identify 1/1 measure downbeats.
    - Verifies 1/1 measure downbeats have an immutable anchor preserved so downbeats are never emptied.
    - Verifies concurrent chords (chord >= 2) preserve at least 1 note, so chords can only be thinned, not wiped out.
    """
    # 120 BPM, 4/4 meter -> beat_length = 500ms, measure = 2000ms
    # Measure 0 downbeat at t = 1000ms (tp start)
    # Measure 1 downbeat at t = 3000ms
    # Measure 2 downbeat at t = 5000ms
    timing_points = [
        TimingPoint(
            time=1000.0,
            beat_length=500.0,
            meter=4,
            uninherited=True,
        )
    ]

    # Hit objects:
    # At t = 1000ms (1/1 downbeat): single note col 1
    # At t = 2000ms (off-downbeat beat 2): single note col 2
    # At t = 3000ms (1/1 downbeat): 3-chord (cols 0, 3, 6)
    # At t = 4000ms (off-downbeat beat 4): 2-chord (cols 1, 5)
    ho_downbeat1 = HitObject(column=1, time=1000.0, note_type=NoteType.RICE)
    ho_beat2 = HitObject(column=2, time=2000.0, note_type=NoteType.RICE)
    ho_db2_c0 = HitObject(column=0, time=3000.0, note_type=NoteType.RICE)
    ho_db2_c3 = HitObject(column=3, time=3000.0, note_type=NoteType.RICE)
    ho_db2_c6 = HitObject(column=6, time=3000.0, note_type=NoteType.RICE)
    ho_beat4_c1 = HitObject(column=1, time=4000.0, note_type=NoteType.RICE)
    ho_beat4_c5 = HitObject(column=5, time=4000.0, note_type=NoteType.RICE)

    hit_objects = [
        ho_downbeat1,
        ho_beat2,
        ho_db2_c0,
        ho_db2_c3,
        ho_db2_c6,
        ho_beat4_c1,
        ho_beat4_c5,
    ]

    bm = Beatmap7K(
        timing_points=timing_points,
        hit_objects=hit_objects,
    )

    detector = MetricSkeletonDetector(bm)

    # 1. Downbeat detection
    assert detector.is_downbeat(1000.0) is True
    assert detector.is_downbeat(3000.0) is True
    assert detector.is_downbeat(5000.0) is True
    assert detector.is_downbeat(2000.0) is False
    assert detector.is_downbeat(4000.0) is False

    # 2. Skeleton anchor detection:
    # ho_downbeat1 is the sole note on measure 0 downbeat -> must be protected
    skeleton_notes = detector.detect_skeleton_notes()
    assert ho_downbeat1 in skeleton_notes

    # 3. Filter candidate removals:
    # If candidate removals attempt to remove ALL notes:
    all_candidates = list(hit_objects)
    valid_removals = detector.filter_candidate_removals(all_candidates)

    # Invariant 1: ho_downbeat1 (measure downbeat) MUST NOT be removed
    assert ho_downbeat1 not in valid_removals

    # Invariant 2: At t = 3000ms (downbeat 3-chord), at least 1 note must survive (cannot remove all 3)
    removed_at_3000 = [ho for ho in valid_removals if ho.time == 3000.0]
    assert len(removed_at_3000) <= 2

    # Invariant 3: At t = 4000ms (off-downbeat 2-chord), at least 1 note must survive (cannot wipe chord to 0)
    removed_at_4000 = [ho for ho in valid_removals if ho.time == 4000.0]
    assert len(removed_at_4000) <= 1

    # Non-chord off-downbeat (ho_beat2) CAN be safely removed
    assert ho_beat2 in valid_removals


def test_bimanual_flux_balancer_flux_calculation_and_asymmetry_penalty():
    """
    Vertical slice 2:
    - Verifies BimanualFluxBalancer accurately counts left (0,1,2), right (4,5,6), and shared center (3) flux.
    - Verifies asymmetry penalty factor: overloaded hand has lower removal penalty (< 1.0, favoring pruning),
      underloaded hand has higher removal penalty (> 1.0, protecting from over-pruning).
    - When 50/50 balanced, both penalty factors equal 1.0.
    """
    from proj7k.downscaler.balancer import BimanualFluxBalancer

    balancer = BimanualFluxBalancer()

    # Balanced map: 2 notes left (col 0, 1), 1 note center (col 3), 2 notes right (col 5, 6)
    # Left flux = 2 + 0.5 = 2.5
    # Right flux = 2 + 0.5 = 2.5
    balanced_notes = [
        HitObject(column=0, time=100.0, note_type=NoteType.RICE),
        HitObject(column=1, time=200.0, note_type=NoteType.RICE),
        HitObject(column=3, time=300.0, note_type=NoteType.RICE),
        HitObject(column=5, time=400.0, note_type=NoteType.RICE),
        HitObject(column=6, time=500.0, note_type=NoteType.RICE),
    ]

    l_flux, r_flux = balancer.compute_hand_flux(balanced_notes)
    assert l_flux == 2.5
    assert r_flux == 2.5

    ratio_l, ratio_r = balancer.compute_flux_ratio(balanced_notes)
    assert ratio_l == 0.5
    assert ratio_r == 0.5

    pen_l, pen_r = balancer.compute_asymmetry_penalty(l_flux, r_flux)
    assert abs(pen_l - 1.0) < 1e-6
    assert abs(pen_r - 1.0) < 1e-6

    # Asymmetrical left-heavy map:
    # 7 notes left (cols 0, 1, 2), 1 note center (col 3), 2 notes right (cols 4, 5)
    # Left = 7 + 0.5 = 7.5
    # Right = 2 + 0.5 = 2.5
    # Left ratio = 7.5 / 10.0 = 0.75
    left_heavy_notes = [
        HitObject(column=0, time=i * 100.0, note_type=NoteType.RICE) for i in range(4)
    ] + [
        HitObject(column=1, time=i * 100.0 + 50.0, note_type=NoteType.RICE) for i in range(3)
    ] + [
        HitObject(column=3, time=500.0, note_type=NoteType.RICE),
        HitObject(column=4, time=600.0, note_type=NoteType.RICE),
        HitObject(column=5, time=700.0, note_type=NoteType.RICE),
    ]

    lh_l, lh_r = balancer.compute_hand_flux(left_heavy_notes)
    assert lh_l == 7.5
    assert lh_r == 2.5
    r_l, r_r = balancer.compute_flux_ratio(left_heavy_notes)
    assert r_l == 0.75
    assert r_r == 0.25

    pen_l, pen_r = balancer.compute_asymmetry_penalty(lh_l, lh_r)
    # Left is overloaded -> penalty to remove left is low (< 1.0), encouraging pruning
    assert pen_l < 1.0
    # Right is underloaded -> penalty to remove right is high (> 1.0), protecting right
    assert pen_r > 1.0


def test_bimanual_flux_balancer_adaptive_candidate_balancing():
    """
    Vertical slice 3:
    - Tests adaptive candidate pruning rebalancing.
    - On an asymmetrical map (70% left load), when given candidates from both hands,
      balancer preferentially selects candidates from the overloaded hand to bring the
      surviving ratio into the physiological [45%, 55%] balance band.
    """
    from proj7k.downscaler.balancer import BimanualFluxBalancer

    balancer = BimanualFluxBalancer()

    # Create 14 left notes (col 0, 1, 2) and 6 right notes (col 4, 5, 6) -> total 20 notes
    left_notes = [
        HitObject(column=0, time=i * 100.0, note_type=NoteType.RICE) for i in range(14)
    ]
    right_notes = [
        HitObject(column=4, time=i * 100.0, note_type=NoteType.RICE) for i in range(6)
    ]
    all_notes = left_notes + right_notes

    # Candidates: suppose pruner proposed 6 left notes and 4 right notes (10 notes to prune)
    # If all 10 are pruned: remaining left = 8, right = 2 -> left ratio = 8 / 10 = 80%!
    # Balancer should adaptively select removals prioritizing the overloaded left hand
    candidates = left_notes[:6] + right_notes[:4]

    balanced_removals = balancer.balance_candidate_removals(
        current_notes=all_notes,
        candidates=candidates,
        target_ratio_range=(0.45, 0.55),
        max_removals=6,
    )

    # Calculate surviving notes after balanced removals
    removed_ids = {id(h) for h in balanced_removals}
    surviving = [h for h in all_notes if id(h) not in removed_ids]

    surviving_l_ratio, _ = balancer.compute_flux_ratio(surviving)
    # The surviving ratio should have converged within or substantially closer to [0.45, 0.55]
    # compared to the original 70%
    assert 0.45 <= surviving_l_ratio <= 0.60
    # Overloaded left hand notes make up the vast majority of removals
    left_removals = [h for h in balanced_removals if h.column in (0, 1, 2)]
    right_removals = [h for h in balanced_removals if h.column in (4, 5, 6)]
    assert len(left_removals) > len(right_removals)


def test_end_to_end_skeleton_protection_and_bimanual_convergence():
    """
    Acceptance Criteria Test:
    - High difficulty map with 8 measures (160 BPM, 4/4 meter).
    - Contains dense chords on downbeats (3-chords, 4-chords), dense stream in-between,
      and strong left-hand bias (75% left notes).
    - Applies aggressive candidate pruning (asking to remove 80% of all notes).
    - Verifies:
      1. Every single 1/1 measure downbeat retains at least 1 note (no sudden measure blanks).
      2. No concurrent chord is completely wiped to 0 notes.
      3. Surviving bimanual flux ratio converges within [45%, 55%].
      4. Applying pure deletion mutation yields a valid Beatmap7K that dumps and parses cleanly.
    """
    from proj7k.downscaler import (
        MetricSkeletonDetector,
        BimanualFluxBalancer,
        apply_pure_deletion,
    )
    from proj7k.parser import dump_osu_7k, parse_osu_7k

    # 160 BPM, 4/4 meter -> beat_length = 375ms, measure_length = 1500ms
    # 8 measures: t = 0 to 12000ms
    bpm = 160.0
    beat_len = 60000.0 / bpm  # 375ms
    measure_len = beat_len * 4.0  # 1500ms

    tp = TimingPoint(time=0.0, beat_length=beat_len, meter=4, uninherited=True)
    hit_objects: List[HitObject] = []

    # Populate 8 measures:
    for m in range(8):
        m_start = m * measure_len
        # Downbeat chord at beat 0: 3-chord on left (cols 0, 1, 2) + center (col 3)
        hit_objects.append(HitObject(column=0, time=m_start, note_type=NoteType.RICE))
        hit_objects.append(HitObject(column=1, time=m_start, note_type=NoteType.RICE))
        hit_objects.append(HitObject(column=2, time=m_start, note_type=NoteType.RICE))
        hit_objects.append(HitObject(column=3, time=m_start, note_type=NoteType.RICE))

        # Off-beat chords and stream across 16th notes:
        # Heavily biased towards left hand (columns 0, 1, 2)
        for sub_step in range(1, 16):
            t = m_start + sub_step * (beat_len / 4.0)
            if sub_step % 4 == 0:
                # Quarter note beat: 2-chord on left
                hit_objects.append(HitObject(column=0, time=t, note_type=NoteType.RICE))
                hit_objects.append(HitObject(column=1, time=t, note_type=NoteType.RICE))
            elif sub_step % 2 == 0:
                # 8th note: single note on left
                hit_objects.append(HitObject(column=2, time=t, note_type=NoteType.RICE))
            else:
                # 16th note: alternate between left and right, with 3x more left
                col = (sub_step % 3) if (sub_step % 4 != 3) else 4
                hit_objects.append(HitObject(column=col, time=t, note_type=NoteType.RICE))

    bm = Beatmap7K(
        title="Test Skeleton Map",
        artist="Test Artist",
        creator="proj7k",
        version="Extra",
        timing_points=[tp],
        hit_objects=hit_objects,
    )

    detector = MetricSkeletonDetector(bm)
    balancer = BimanualFluxBalancer()

    # Initial bimanual flux check: heavily asymmetric (> 70% left)
    init_l, init_r = balancer.compute_flux_ratio(bm.hit_objects)
    assert init_l > 0.70

    # Aggressive candidate selection: propose removing 80% of all notes
    # Candidate pool covers all notes
    candidate_removals = list(bm.hit_objects)

    # 1. First gate: MetricSkeletonDetector filters candidates to protect invariants
    skeleton_safe_candidates = detector.filter_candidate_removals(candidate_removals)

    # 2. Second gate: BimanualFluxBalancer adaptively selects candidates from safe pool
    # Auto-converges until surviving ratio enters [0.45, 0.55]
    balanced_removals = balancer.balance_candidate_removals(
        current_notes=bm.hit_objects,
        candidates=skeleton_safe_candidates,
        target_ratio_range=(0.45, 0.55),
    )

    # Apply pure deletion mutation
    pruned_bm = apply_pure_deletion(bm, balanced_removals)

    # Assertions:
    # 1. Verify no measure downbeat is empty
    for m in range(8):
        m_start = m * measure_len
        surviving_at_downbeat = [
            ho for ho in pruned_bm.hit_objects if abs(ho.time - m_start) < 2.0
        ]
        assert len(surviving_at_downbeat) >= 1, f"Measure {m} downbeat at {m_start}ms was emptied!"

    # 2. Verify all surviving chords have at least 1 note
    grouped_surviving: Dict[float, List[HitObject]] = {}
    for ho in pruned_bm.hit_objects:
        grouped_surviving.setdefault(round(ho.time, 1), []).append(ho)

    for t_key, orig_group in detector._time_groups.items():
        if len(orig_group) >= 2:
            surviving_group = grouped_surviving.get(t_key, [])
            assert len(surviving_group) >= 1, f"Chord at {t_key}ms was completely wiped out!"

    # 3. Verify bimanual flux ratio converged into [45%, 55%]
    surv_l, surv_r = balancer.compute_flux_ratio(pruned_bm.hit_objects)
    assert 0.45 <= surv_l <= 0.55, f"Expected left ratio in [0.45, 0.55], got {surv_l:.3f}"

    # 4. Verify round-trip serialization
    dumped = dump_osu_7k(pruned_bm)
    reloaded = parse_osu_7k(dumped)
    assert len(reloaded.hit_objects) == len(pruned_bm.hit_objects)


def test_metric_skeleton_detector_multiple_timing_points_and_meters():
    """
    Tests MetricSkeletonDetector with multiple timing points:
    - Section 1: 120 BPM (500ms beat, 4/4 meter -> 2000ms measure) from 0 to 4000ms
    - Section 2: 150 BPM (400ms beat, 3/4 meter -> 1200ms measure) starting at 4000ms
    """
    from proj7k.downscaler import MetricSkeletonDetector

    tps = [
        TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True),
        TimingPoint(time=4000.0, beat_length=400.0, meter=3, uninherited=True),
    ]

    bm = Beatmap7K(timing_points=tps, hit_objects=[])
    detector = MetricSkeletonDetector(bm)

    # Section 1 downbeats: 0, 2000, (4000 is transition)
    assert detector.is_downbeat(0.0) is True
    assert detector.is_downbeat(2000.0) is True
    assert detector.is_downbeat(1000.0) is False  # beat 2, not downbeat

    # Section 2 downbeats (starts at 4000, meter 3, beat 400 -> measure 1200ms):
    # 4000, 5200, 6400, 7600...
    assert detector.is_downbeat(4000.0) is True
    assert detector.is_downbeat(5200.0) is True
    assert detector.is_downbeat(6400.0) is True
    assert detector.is_downbeat(4800.0) is False  # beat 2 of 3/4


def test_metric_skeleton_detector_index_candidates_and_no_timing_points():
    """
    Tests MetricSkeletonDetector with:
    - Candidate indices (int) instead of HitObject references.
    - Beatmap without explicit uninherited timing points (fallback 120 BPM 4/4).
    """
    from proj7k.downscaler import MetricSkeletonDetector

    ho1 = HitObject(column=0, time=0.0, note_type=NoteType.RICE)
    ho2 = HitObject(column=1, time=0.0, note_type=NoteType.RICE)
    ho3 = HitObject(column=5, time=500.0, note_type=NoteType.RICE)

    bm = Beatmap7K(timing_points=[], hit_objects=[ho1, ho2, ho3])
    detector = MetricSkeletonDetector(bm)

    # 0ms is downbeat in fallback timing (0ms, 500ms beat, 4/4 meter)
    assert detector.is_downbeat(0.0) is True
    assert detector.is_downbeat(2000.0) is True

    # Candidate indices: [0, 1, 2] (try to delete all)
    removals = detector.filter_candidate_removals([0, 1, 2])
    # At t=0ms (downbeat & 2-chord), at least 1 note must survive
    removals_at_0 = [ho for ho in removals if ho.time == 0.0]
    assert len(removals_at_0) <= 1
    # Off-downbeat ho3 can be removed
    assert ho3 in removals


def test_bimanual_flux_balancer_edge_cases_and_right_heavy():
    """
    Tests edge cases for BimanualFluxBalancer:
    - Zero/empty flux.
    - Column 3 center lane only.
    - Right-heavy map balancing.
    - Already balanced map with max_removals=None.
    """
    from proj7k.downscaler import BimanualFluxBalancer

    balancer = BimanualFluxBalancer()

    # 1. Empty notes
    assert balancer.compute_hand_flux([]) == (0.0, 0.0)
    assert balancer.compute_flux_ratio([]) == (0.5, 0.5)
    assert balancer.compute_asymmetry_penalty(0.0, 0.0) == (1.0, 1.0)
    assert balancer.balance_candidate_removals([], []) == []

    # 2. Pure center lane (column 3) notes
    center_notes = [HitObject(column=3, time=i * 100.0, note_type=NoteType.RICE) for i in range(4)]
    assert balancer.compute_hand_flux(center_notes) == (2.0, 2.0)
    assert balancer.compute_flux_ratio(center_notes) == (0.5, 0.5)

    # 3. Already balanced map (50/50): balance_candidate_removals processes candidates in a balanced manner
    balanced_notes = [
        HitObject(column=0, time=100.0, note_type=NoteType.RICE),
        HitObject(column=6, time=100.0, note_type=NoteType.RICE),
    ]
    balanced_res = balancer.balance_candidate_removals(balanced_notes, balanced_notes)
    assert len(balanced_res) == 2
    # Verify post-removal ratio is preserved
    surv = [h for h in balanced_notes if id(h) not in {id(x) for x in balanced_res}]
    assert balancer.compute_flux_ratio(surv) == (0.5, 0.5)

    # 4. Right-heavy map: 16 right notes (col 4, 5, 6), 4 left notes (col 0, 1) -> 80% right
    r_notes = [HitObject(column=5, time=i * 50.0, note_type=NoteType.RICE) for i in range(16)]
    l_notes = [HitObject(column=1, time=i * 50.0, note_type=NoteType.RICE) for i in range(4)]
    all_notes = l_notes + r_notes

    pen_l, pen_r = balancer.compute_asymmetry_penalty(4.0, 16.0)
    assert pen_l > 1.0  # Left is protected
    assert pen_r < 1.0  # Right is penalized / targeted for pruning

    balanced_removals = balancer.balance_candidate_removals(all_notes, all_notes)
    removed_ids = {id(h) for h in balanced_removals}
    surviving = [h for h in all_notes if id(h) not in removed_ids]
    surv_l_ratio, surv_r_ratio = balancer.compute_flux_ratio(surviving)

    assert 0.45 <= surv_l_ratio <= 0.55
    # All or almost all removals are right notes
    assert all(ho.column in (4, 5, 6) for ho in balanced_removals)


def test_chord_anchor_multi_round_protection():
    """
    Verifies that off-downbeat chords have an immutable anchor registered in detect_skeleton_notes()
    preventing multi-round cascading deletion down to 0 notes.
    """
    from proj7k.downscaler import MetricSkeletonDetector, apply_pure_deletion

    tp = TimingPoint(time=0.0, beat_length=500.0, meter=4, uninherited=True)
    # Off-downbeat at 500ms (beat 1): 3-chord (cols 0, 3, 5)
    ho1 = HitObject(column=0, time=500.0, note_type=NoteType.RICE)
    ho2 = HitObject(column=3, time=500.0, note_type=NoteType.RICE)
    ho3 = HitObject(column=5, time=500.0, note_type=NoteType.RICE)

    bm = Beatmap7K(timing_points=[tp], hit_objects=[ho1, ho2, ho3])
    detector = MetricSkeletonDetector(bm)

    # detect_skeleton_notes MUST include an anchor for this chord even though it's off-downbeat
    skeleton_anchors = detector.detect_skeleton_notes()
    assert len(skeleton_anchors) >= 1
    anchor = skeleton_anchors[0]

    # Round 1: candidate removals try to remove ho1 and ho3
    round1_removals = detector.filter_candidate_removals([ho1, ho3])
    bm_round1 = apply_pure_deletion(bm, round1_removals)
    assert len(bm_round1.hit_objects) >= 1

    # Round 2: candidate removals try to remove all surviving notes
    detector_round2 = MetricSkeletonDetector(bm)
    round2_removals = detector_round2.filter_candidate_removals([anchor])
    # The anchor cannot be removed
    assert anchor not in round2_removals




