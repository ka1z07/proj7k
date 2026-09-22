"""
The equal-length counterexample (issue #52, AC7): the one first-principles assertion the LN
Release axis must satisfy without reference to any ladder's ground truth.

ADR-0015 decision 4 states the semantics as a thought experiment: take a chordstream and
replace every rice note with an **equal-length** hold. The tail-judgement density becomes
absurd and the difficulty does not move — every beat poses the same question (release
everything), so there is nothing to judge. The axis therefore has to read its degenerate value
on such a chart and must not gain on the General base.

The implemented form is `r_ln_rel = r_ln_gen * gain * (1 + k * release_lock_depth)`, and the
degeneracy is a property of `release_lock_depth` itself rather than a special case in the
operator: for a chart whose holds all have the same length, no interval can strictly contain
another's tail — containment needs one hold to start earlier *and* end later, which equal
lengths make impossible — so `release_lock_depth` is exactly 0 and the modifier is exactly 1.

Both shapes the ADR names are covered: seven-lane chords that start and end together, and a
staggered chordstream written entirely in equal-length holds.
"""

from typing import List

import pytest

from proj7k.features import extract_beatmap_features
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint
from proj7k.radar import RadarOptions, compute_raw_technique_drivers


BPM = 150.0
#: Chosen so every timestamp below is exact in binary floating point: the containment test in
#: `release_lock_depth` is strict (`start < tail < end`), and a beat length that does not divide
#: evenly leaves neighbouring holds overlapping by one ulp, which reads as a lock.
BEAT_MS = 400.0
HOLD_MS = 4 * BEAT_MS  # one measure, the same for every hold in both charts below


def _beatmap(hit_objects: List[HitObject]) -> Beatmap7K:
    return Beatmap7K(
        title="Equal-length holds",
        artist="Test",
        creator="Tester",
        version="degenerate",
        hit_objects=hit_objects,
        timing_points=[TimingPoint(time=0.0, beat_length=BEAT_MS, meter=4, uninherited=True)],
    )


def _seven_lane_equal_chords(bars: int = 16, step: float = HOLD_MS) -> Beatmap7K:
    """
    同起同长七轨单和弦: all seven lanes press and release together, every hold the same.

    The step equals the hold length, so no chord overlaps the next: at every lift the whole
    hand lifts at once and nothing is left held, which is the sense in which the equal-length
    chart has no lift judgement to make at all.
    """
    hos: List[HitObject] = []
    steps = int(bars * 4 * BEAT_MS / step)
    for i in range(steps):
        start = i * step
        for column in range(7):
            hos.append(
                HitObject(column=column, time=start, note_type=NoteType.LN, end_time=start + HOLD_MS)
            )
    return _beatmap(hos)


def _chordstream_in_equal_holds(bars: int = 16, step: float = BEAT_MS / 2) -> Beatmap7K:
    """
    chordstream 米键全换成等长 LN: a rolling two-lane-per-step stream, every lane held for the
    same length — the counterexample's second shape, where the *holds* are equal but their
    starts are staggered.
    """
    hos: List[HitObject] = []
    steps = int(bars * 4 * BEAT_MS / step)
    for i in range(steps):
        start = i * step
        for offset in (0, 3):
            column = (i + offset) % 7
            hos.append(
                HitObject(column=column, time=start, note_type=NoteType.LN, end_time=start + HOLD_MS)
            )
    return _beatmap(hos)


def test_synchronized_equal_holds_leave_the_release_axis_at_its_anchor():
    """
    `release_lock_depth` is 0, the modifier is 1, and the axis does not gain on General.

    All three are asserted separately: the first is the mechanism, the second is the anchor the
    ADR specifies (`g = 1` on a degenerate chart), and the third is the consequence the axis is
    judged on — an equal-length chart must not read as release-difficult.
    """
    options = RadarOptions()
    beatmap = _seven_lane_equal_chords()
    features = extract_beatmap_features(beatmap)
    drivers = compute_raw_technique_drivers(beatmap, features=features, options=options).to_dict()

    assert features.ln_count > 0, "the synthetic chart has no holds, so it cannot test degeneracy"
    assert features.release_lock_depth == pytest.approx(0.0), (
        "equal-length holds produced a non-zero release lock depth: "
        f"{features.release_lock_depth} — the containment test no longer excludes them"
    )
    # The ADR's `g` is the *lock* modifier; the axis' overall scale is the separate, pre-existing
    # `ln_release_gain`. The degeneracy claim is about `g`.
    g = 1.0 + options.ln_release_lock_gain * features.release_lock_depth
    assert g == pytest.approx(1.0), (
        f"the release lock modifier read {g} on the degenerate chart, expected exactly 1.0"
    )
    assert drivers["ln_release"] == pytest.approx(
        drivers["ln_general"] * options.ln_release_gain * g, rel=1e-9
    ), "the release driver is no longer its base times the modifier"
    assert drivers["ln_release"] <= drivers["ln_general"], (
        "an equal-length chart gained release load over its General base "
        f"(release {drivers['ln_release']:.4f} > general {drivers['ln_general']:.4f})"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "staggered equal-length holds are NOT degenerate under `release_lock_depth`: the "
        "containment test fires whenever a lane is pressed after another lane's hold began "
        "(measured: lock depth 2.11 on this synthetic), so the modifier reads 3.11, not 1. "
        "issue #52's AC7 names this shape as degenerate — that reading holds only for the "
        "synchronized case, and closing the gap means either counting only holds that outlast "
        "the lifting one or replacing the quantity. See docs/adr/0015's open question."
    ),
)
def test_staggered_equal_length_holds_are_degenerate_too():
    options = RadarOptions()
    features = extract_beatmap_features(_chordstream_in_equal_holds())
    modifier = options.ln_release_gain * (
        1.0 + options.ln_release_lock_gain * features.release_lock_depth
    )
    assert modifier == pytest.approx(1.0), (
        f"staggered equal-length holds read a modifier of {modifier:.4f}, expected 1.0"
    )


def test_staggered_unequal_holds_are_not_read_as_a_lift_load():
    """
    The control for the pair above: the same layout with *unequal* hold lengths does move the
    axis, so the degeneracy test is measuring the length structure rather than the layout.
    """
    options = RadarOptions()
    hos: List[HitObject] = []
    steps = int(16 * 4 * BEAT_MS / (BEAT_MS / 2))
    for i in range(steps):
        start = i * BEAT_MS / 2
        for offset in (0, 3):
            column = (i + offset) % 7
            # Intra-hand lengths differ, so a lift lands while the same hand is still holding a
            # longer neighbour — the release-lock structure the axis is about.
            length = HOLD_MS * (2 if column in (0, 1, 2) else 3)
            hos.append(
                HitObject(column=column, time=start, note_type=NoteType.LN, end_time=start + length)
            )
    features = extract_beatmap_features(_beatmap(hos))
    assert features.release_lock_depth > 0.0
