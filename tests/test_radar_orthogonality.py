"""
CI force for the four radar-orthogonality thresholds (issue #47's AC1, welded in by #52).

`tools/radar_orthogonality_report.py` has declared these four thresholds since #47, and until
this module existed nothing asserted them: the tool printed a verdict nobody read, so a
regression could sit in the tree indefinitely. Issue #52 confirmed that directly — the file the
tool's docstring pointed at (`tests/test_radar_orthogonality.py`) did not exist.

Every threshold is imported from the tool rather than restated, so the tool cannot print one
verdict and CI assert another. Measured over the 120-chart benchmark:

    median separation            0.8076  (threshold <= 0.5)   <- accepted shortfall, see below
    charts above 0.8             61      (threshold <= 10)    <- accepted shortfall, see below
    LN Release own-axis hits     7/15    (threshold >= 7)
    Regular Speed own-axis hits  14/15   (threshold >= 12)

The two separation thresholds are **ratcheted rather than asserted at their committed value**:
issue #52 measured the LN axes' dynamic range collapsing (0.698 -> 0.8095) as the price of the
release modifier's degenerate form and the inverse axis' share gate, and the ticket owner
accepted that state as the stage-2 starting point. So what CI enforces is that it does not get
*worse* — the accepted figures are upper bounds, and a further regression is a red light. The
committed thresholds stay in the tool (and in the docstring above) as the target; `count of
charts above 0.8` is compared against an exact recorded number because the count moves in whole
charts and a tolerance would let one drift by.
"""

import statistics

import pytest


#: The separation figures issue #52 measured and the ticket owner accepted as the stage-2
#: starting point. They are upper bounds, not targets: the committed thresholds are the tool's
#: `SEPARATION_MEDIAN_MAX` / `SEPARATION_HIGH_MAX`, and the test fails if either bound loosens.
ACCEPTED_SEPARATION_MEDIAN = 0.8076
ACCEPTED_CHARTS_ABOVE_HIGH = 61


def _drivers_of_all(benchmark_drivers, benchmark_manifest):
    return [
        benchmark_drivers(group, tier)
        for group in benchmark_manifest
        for tier in benchmark_manifest[group]
    ]


def _own_axis_hits(benchmark_drivers, benchmark_manifest, report):
    """Own-axis hits per manifest group, under the tool's argmax definition."""
    hits = {}
    for axis, group in report.GROUP_FOR_TECHNIQUE.items():
        hits[group] = sum(
            1
            for tier in benchmark_manifest[group]
            if report._argmax(benchmark_drivers(group, tier)) == axis
        )
    return hits


def test_median_separation_has_not_loosened_past_the_accepted_figure(
    benchmark_drivers, benchmark_manifest, orthogonality_report
):
    ratios = [
        orthogonality_report._separation(d)
        for d in _drivers_of_all(benchmark_drivers, benchmark_manifest)
    ]
    median = round(statistics.median(ratios), 4)
    assert median <= ACCEPTED_SEPARATION_MEDIAN, (
        f"median second-highest/highest driver {median:.4f} is worse than the accepted "
        f"{ACCEPTED_SEPARATION_MEDIAN} (committed threshold "
        f"{orthogonality_report.SEPARATION_MEDIAN_MAX}): the axes are decoupling backwards"
    )


def test_charts_above_the_high_threshold_have_not_increased(
    benchmark_drivers, benchmark_manifest, orthogonality_report
):
    ratios = [
        orthogonality_report._separation(d)
        for d in _drivers_of_all(benchmark_drivers, benchmark_manifest)
    ]
    high = sum(1 for r in ratios if r > orthogonality_report.SEPARATION_HIGH_THRESHOLD)
    assert high <= ACCEPTED_CHARTS_ABOVE_HIGH, (
        f"{high} charts read above {orthogonality_report.SEPARATION_HIGH_THRESHOLD}, up from the "
        f"accepted {ACCEPTED_CHARTS_ABOVE_HIGH} (limit {orthogonality_report.SEPARATION_HIGH_MAX})"
    )


def test_ln_release_holds_enough_of_its_own_ladder(
    benchmark_drivers, benchmark_manifest, orthogonality_report
):
    hits = _own_axis_hits(benchmark_drivers, benchmark_manifest, orthogonality_report)
    summary = ", ".join(f"{g} {h}/15" for g, h in sorted(hits.items()))
    assert hits["LN Release"] >= orthogonality_report.LN_RELEASE_MIN_HITS, (
        f"LN Release own-axis hits {hits['LN Release']}/15 < "
        f"{orthogonality_report.LN_RELEASE_MIN_HITS} — all ladders: {summary}"
    )


def test_speed_holds_enough_of_its_own_ladder(
    benchmark_drivers, benchmark_manifest, orthogonality_report
):
    hits = _own_axis_hits(benchmark_drivers, benchmark_manifest, orthogonality_report)
    summary = ", ".join(f"{g} {h}/15" for g, h in sorted(hits.items()))
    assert hits["Regular Speed"] >= orthogonality_report.SPEED_MIN_HITS, (
        f"Regular Speed own-axis hits {hits['Regular Speed']}/15 < "
        f"{orthogonality_report.SPEED_MIN_HITS} — all ladders: {summary}"
    )


def test_the_tools_own_verdict_names_the_same_shortfalls_ci_does(
    benchmark_drivers, benchmark_manifest, orthogonality_report
):
    """
    The tool's `verdict()` over the same drivers, so the printed report and CI cannot drift.

    The point of reading the tool's thresholds instead of restating them is that a developer
    reading `tools/radar_orthogonality_report.py`'s output sees exactly what CI enforces. What
    is pinned here is the *set* of thresholds the tool reports unmet: it is the two accepted
    separation figures and nothing else. The moment a change meets one of them — or breaks
    another — this test says so, which is when the ratchet above (and this list) gets updated.
    """
    rows = [
        {"group": group, "tier": tier, "drivers": benchmark_drivers(group, tier)}
        for group in benchmark_manifest
        for tier in benchmark_manifest[group]
    ]
    summary = orthogonality_report.summarize(rows)
    passed, failures = orthogonality_report.verdict(summary)
    assert not passed, (
        "the tool's verdict now passes: the accepted separation shortfalls were met, so the "
        "ratchet in this module (and the tool's thresholds) must be re-baselined"
    )
    assert len(failures) == 2 and all(
        "separation" in f or "charts above" in f for f in failures
    ), f"the tool reports different shortfalls than CI expects: {failures}"
