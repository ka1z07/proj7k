"""
The LN discriminant as a one-sided invariant (issue #52, AC5 / ADR-0015 decision 5).

Issue #52's starting evidence found one quantity in the engine that separates the two LN
idioms without fitting anything: the Rule E gate's own condition, `mean_locked_fingers >=
RadarOptions.inv_lock_gate` with `hold_ratio >= inv_hold_gate`. Over the benchmark corpus it
fires on 12 of the 15 LN Inverse charts and on **none** of the 15 LN Release charts, and
fitting the threshold would buy nothing (the best fit, 4.264, is worth zero additional
accuracy) — which is what makes it assertable rather than descriptive.

The invariant is stated **one-sided on purpose**. "LN Release never trips the gate" is a claim
the corpus supports and that a re-calibration must not quietly break. "LN Inverse always trips
it" is not: three of its own charts (0th, 1st, 2nd — `mean_locked_fingers` 2.65, 3.20, 2.78)
sit below the gate, and ADR-0015 records them as a known recall gap rather than a threshold to
lower. Writing the second claim would either fail today or, worse, invite `inv_lock_gate` to be
tuned down until it passed — which would cost the precision this test exists to protect.

The margin is thin and is recorded here rather than asserted: the deepest non-Inverse lock in
the corpus is LN Release Stellium at 3.887 against the gate's 4.0, i.e. 0.113. A replacement
chart or a re-calibration can cross it silently, which is why the ADR lists it as a risk (and
why this file is the thing that would notice).
"""

import pytest


#: The three LN Inverse benchmark tiers below the gate — the invariant's known recall gap.
LOW_LOCK_INVERSE_TIERS = ("0th", "1st", "2nd")


def _trips_rule_e(features, options) -> bool:
    """The Rule E condition as `radar.compute_raw_technique_drivers` applies it."""
    return (
        features.mean_locked_fingers >= options.inv_lock_gate
        and features.hold_pct / 100.0 >= options.inv_hold_gate
    )


def test_ln_release_never_trips_the_inverse_gate(benchmark_features, benchmark_manifest):
    from proj7k.radar import RadarOptions

    options = RadarOptions()
    tripped = [
        tier
        for tier in benchmark_manifest["LN Release"]
        if _trips_rule_e(benchmark_features("LN Release", tier), options)
    ]
    assert tripped == [], (
        f"LN Release tiers tripped Rule E: {tripped} — the one-sided invariant the LN "
        f"discriminant is stated as (gate: mean_locked_fingers >= {options.inv_lock_gate}, "
        f"hold_ratio >= {options.inv_hold_gate})"
    )


def test_inverse_ladder_trips_the_gate_except_for_the_recorded_low_lock_tiers(
    benchmark_features, benchmark_manifest
):
    """
    LN Inverse's own side of the invariant, pinned to the recorded gap.

    Asserted as an exact partition rather than "most charts trip it": if a future calibration
    moves a chart across the gate, this test says which one, and the recorded list in
    `docs/adr/0015` is what has to be updated with it.
    """
    from proj7k.radar import RadarOptions

    options = RadarOptions()
    tripping = {
        tier
        for tier in benchmark_manifest["LN Inverse"]
        if _trips_rule_e(benchmark_features("LN Inverse", tier), options)
    }
    not_tripping = set(benchmark_manifest["LN Inverse"]) - tripping
    assert not_tripping == set(LOW_LOCK_INVERSE_TIERS), (
        f"the LN Inverse charts below the gate are {sorted(not_tripping)}, expected "
        f"{sorted(LOW_LOCK_INVERSE_TIERS)} — recorded in ADR-0015 as the discriminant's recall gap"
    )


def test_the_gate_is_not_tuned_to_fit_the_corpus(benchmark_features, benchmark_manifest):
    """
    The gate is an engine constant, not a fitted threshold (ADR-0015 decision 5).

    A fitted threshold would be indistinguishable from this one on today's corpus and would
    drift with it; the fitted value (4.264) buys nothing, so any move of `inv_lock_gate` toward
    the corpus' own maximum has to be a deliberate calibration change with an ADR behind it.
    """
    from proj7k.radar import RadarOptions

    options = RadarOptions()
    deepest_release = max(
        benchmark_features("LN Release", tier).mean_locked_fingers
        for tier in benchmark_manifest["LN Release"]
    )
    assert options.inv_lock_gate > deepest_release == pytest.approx(3.887, abs=5e-4), (
        "the margin between the gate and the deepest LN Release lock changed: "
        f"gate {options.inv_lock_gate}, deepest LN Release {deepest_release:.4f}"
    )
