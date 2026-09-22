"""
Issue #52's driver-layer acceptance as CI force: every driver orders its own ladder, and does
not order someone else's better.

The per-technique driver gates were registered in `guard.CALIBRATED_METRIC_GATES` by issue #52
but nothing asserted them, so the ticket's starting evidence could be read as "5 of 8 fail" or
"3 of 8 fail" depending on which gate a reader assumed — the same table, two answers. This
module evaluates each of the eight ladders on its own driver against the gate registered for
it, and reads those gates from `guard` rather than restating them. Around that it carries the
three acceptance criteria that are stated on the *drivers* rather than on the star rating:

- **AC1's exemption for `ln_release`** — judged by its modifier's relative order, not by
  dominating its own ladder (`test_ln_release_holds_its_place_among_the_ln_axes`).
- **AC2, the cross-technique intrusions** — ratcheted, with the argument for why zero is out of
  reach recorded next to the ratchet (`test_cross_technique_intrusions_have_not_increased`).
- **The bullseye**, on the one axis the ticket owner ruled it applies to.

Only two axes may be below their gate, and both are issue #50's by ADR-0015's ruling: Ω_irreg
does not yet separate a chordstream's texture from a tech chart's, which is what holds `tech`
down, and the `ln_inverse` axis is part of the same pass. The assertion is therefore two-sided
on purpose — an axis that starts clearing its gate must be moved out of `KNOWN_BELOW_GATE` in
the same commit that fixes it, so the boundary cannot quietly outlive the defect.
"""

import statistics

import pytest

from proj7k.guard import CALIBRATED_METRIC_GATES
from proj7k.monotonicity import (
    DRIVER_METRIC_PREFIX,
    TIER_ORDER,
    compute_spearman_rho,
    evaluate_tier_sequence,
)

#: The benchmark manifest group each axis' ladder is filed under.
LADDER_FOR_AXIS = {
    "jack": "Regular Jack",
    "tech": "Regular Tech",
    "speed": "Regular Speed",
    "stream": "Regular Stream",
    "ln_general": "LN General",
    "ln_tech": "LN Tech",
    "ln_inverse": "LN Inverse",
    "ln_release": "LN Release",
}

#: Axes still below their registered gate, with the tau/rho measured at the time of writing.
#: Both are issue #50's (ADR-0015 assigns Ω_irreg's resolution there). Every other axis must
#: clear its gate; an axis that clears it must be deleted from this map in the same change.
KNOWN_BELOW_GATE = {
    "tech": (0.790, 0.921),
    "ln_inverse": (0.790, 0.900),
}

#: The ticket's bullseye (`tau >= 0.93 / rho >= 0.98`), recorded rather than asserted: ADR-0015
#: notes that the official ladder's own ratings order at tau 0.886 on Regular Tech, so the
#: bullseye is a target; and the ticket owner ruled that it applies to the LN side only, because
#: a rice ladder's tier order *is* its density order (`peak_4m_nps` alone reaches rho 0.975-0.989
#: against all four), so demanding it of a rice driver demands a density shadow.
BULLSEYE = (0.93, 0.98)

#: Cross-technique intrusions, ratcheted rather than targeted at AC2's zero: see the test below
#: and ADR-0015's stage-2 record for why zero is out of reach. Stage 2 moved this from 15 to 13.
ACCEPTED_INTRUSIONS = 13


def _report(drivers, manifest, ladder, axis, metric=None):
    tiers = sorted(manifest[ladder], key=TIER_ORDER.index)
    return evaluate_tier_sequence(
        [(t, drivers(ladder, t)[axis]) for t in tiers],
        technique=ladder,
        metric=metric or DRIVER_METRIC_PREFIX + axis,
    )


def _clears(report, gate):
    return (
        report.kendall_tau >= gate.min_kendall_tau
        and report.spearman_rho >= gate.min_spearman_rho
        and len(report.violations) <= gate.max_violations
    )


@pytest.mark.parametrize("axis", sorted(LADDER_FOR_AXIS))
def test_each_driver_orders_its_own_ladder_or_is_a_recorded_boundary(
    axis, benchmark_drivers, benchmark_manifest
):
    gate = CALIBRATED_METRIC_GATES[DRIVER_METRIC_PREFIX + axis]
    report = _report(benchmark_drivers, benchmark_manifest, LADDER_FOR_AXIS[axis], axis)
    measured = (round(report.kendall_tau, 3), round(report.spearman_rho, 3))

    if axis in KNOWN_BELOW_GATE:
        assert not _clears(report, gate), (
            f"{axis} now clears its ladder gate at tau/rho {measured} — delete it from "
            f"KNOWN_BELOW_GATE: the defect is fixed and the boundary must not outlive it"
        )
        recorded = KNOWN_BELOW_GATE[axis]
        assert measured[0] <= recorded[0] + 0.01 and measured[1] <= recorded[1] + 0.01, (
            f"{axis} moved past its recorded boundary {recorded} (now {measured}) without "
            f"clearing the gate; re-record it so the handoff to #50 stays truthful"
        )
        return

    assert _clears(report, gate), (
        f"{axis} ladder {report.kendall_tau:.3f}/{report.spearman_rho:.3f} is below its "
        f"registered gate {gate.min_kendall_tau}/{gate.min_spearman_rho} "
        f"({len(report.violations)} inversions, max {gate.max_violations})"
    )


def test_the_recorded_boundary_is_exactly_what_is_below_the_gates(
    benchmark_drivers, benchmark_manifest
):
    """Nothing else has quietly slipped under its gate."""
    below = {}
    for axis, ladder in LADDER_FOR_AXIS.items():
        gate = CALIBRATED_METRIC_GATES[DRIVER_METRIC_PREFIX + axis]
        report = _report(benchmark_drivers, benchmark_manifest, ladder, axis)
        if not _clears(report, gate):
            below[axis] = (round(report.kendall_tau, 3), round(report.spearman_rho, 3))
    assert set(below) == set(KNOWN_BELOW_GATE), (
        f"axes below their gate are {sorted(below)} but the recorded boundary is "
        f"{sorted(KNOWN_BELOW_GATE)}; measured {below}"
    )


def test_the_ln_release_axis_meets_the_bullseye(benchmark_drivers, benchmark_manifest):
    """
    The one axis the bullseye does apply to, and the one place it is a real bar rather than a
    density request: release is General's base times a modifier, so its ordering comes from the
    flux base it inherits, and its own job is the modifier's shape.
    """
    report = _report(benchmark_drivers, benchmark_manifest, "LN Release", "ln_release")
    assert report.kendall_tau >= BULLSEYE[0] and report.spearman_rho >= BULLSEYE[1], (
        f"LN Release ladder {report.kendall_tau:.3f}/{report.spearman_rho:.3f} is short of the "
        f"bullseye {BULLSEYE}"
    )


def test_ln_release_holds_its_place_among_the_ln_axes(
    benchmark_features, benchmark_manifest
):
    """
    AC1's exemption, welded: `ln_release` is judged by its modifier's *relative* order rather
    than by dominating its own ladder, because it is a modifier on the General base and the two
    LN ladders interleave in lock depth. The AC states the criterion as "`g` above LN General
    and LN Tech, below LN Inverse", where `g = 1 + k * lockd` is the release modifier — so the
    four ladders' medians are what has to be ordered, not the axis' own hit count.
    """
    medians = {}
    for group in ("LN General", "LN Tech", "LN Inverse", "LN Release"):
        tiers = sorted(benchmark_manifest[group], key=TIER_ORDER.index)
        medians[group] = statistics.median(
            benchmark_features(group, t).release_lock_depth for t in tiers
        )
    order = sorted(medians, key=lambda g: medians[g])
    assert order == ["LN Tech", "LN General", "LN Release", "LN Inverse"], (
        "the release modifier's depth must order the four LN ladders Tech < General < Release < "
        f"Inverse (AC1's exemption); measured " + ", ".join(f"{g} {medians[g]:.4f}" for g in order)
    )


def test_cross_technique_intrusions_have_not_increased(benchmark_drivers, benchmark_manifest):
    """
    AC2 as a ratchet. The AC asks for intrusions to *fall*, and this is what they fell to.

    A "intrusion" is a driver that orders some other technique's ladder at least as well as its
    own. Zero is structurally out of reach and ADR-0015's stage-2 record carries the argument:
    all eight ladders are ordered by the same quantity (`peak_4m_nps` orders every one of them at
    rho 0.968-0.989), so any driver that reads density — which AC1 requires, since a driver that
    does not read it cannot order its own ladder — necessarily orders the others too. Driving all
    eight with pure peak density gives 31 intrusions of 56, against 13 today; the shape term is
    what keeps the count down. What this test enforces is that the count does not grow back.
    """
    def driver_over(axis_of_driver, group):
        tiers = sorted(benchmark_manifest[group], key=TIER_ORDER.index)
        return [benchmark_drivers(group, t)[axis_of_driver] for t in tiers]

    own = {
        axis: compute_spearman_rho(driver_over(axis, ladder))
        for axis, ladder in LADDER_FOR_AXIS.items()
    }

    intrusions = []
    for axis, own_ladder in LADDER_FOR_AXIS.items():
        for other in LADDER_FOR_AXIS.values():
            if other == own_ladder:
                continue
            rho = compute_spearman_rho(driver_over(axis, other))
            if rho >= own[axis]:
                intrusions.append((axis, other, round(rho - own[axis], 4)))

    assert len(intrusions) <= ACCEPTED_INTRUSIONS, (
        f"{len(intrusions)} cross-technique intrusions, up from the accepted "
        f"{ACCEPTED_INTRUSIONS}: {sorted(intrusions, key=lambda x: -x[2])}"
    )
