"""
CI force for the four radar-orthogonality thresholds (issue #47's AC1, welded in by #52).

`tools/radar_orthogonality_report.py` has declared these four thresholds since #47, and until
this module existed nothing asserted them: the tool printed a verdict nobody read, so a
regression could sit in the tree indefinitely. Issue #52 confirmed that directly — the file the
tool's docstring pointed at (`tests/test_radar_orthogonality.py`) did not exist.

Every threshold is imported from the tool rather than restated, so the tool cannot print one
verdict and CI assert another. Measured over the 120-chart benchmark:

    median separation            0.7638  (threshold <= 0.5)   <- accepted shortfall, see below
    charts above 0.8             55      (threshold <= 10)    <- accepted shortfall, see below
    LN Release own-axis hits     7/15    (threshold >= 7)
    Regular Speed own-axis hits  14/15   (threshold >= 12)

The two separation thresholds are **ratcheted rather than asserted at their committed value**,
because the committed pair is not reachable at all in this corpus and the ratchet is what
actually moves. Issue #52 stage 1 measured the LN axes' dynamic range collapsing
(0.698 -> 0.8095) as the price of the release modifier's degenerate form and the inverse axis'
share gate; stage 2 gave the four rice axes a peak-density carrier and brought the median back
down to 0.7638. So what CI enforces is that it does not get *worse* — the accepted figures are
upper bounds, and a further regression is a red light. `count of charts above 0.8` is compared
against an exact recorded number because the count moves in whole charts and a tolerance would
let it drift by.

**Why 0.5 cannot be reached.** `ln_release` is `ln_general * gain * (1 + k * lockd)` with
`gain` 0.645 and `lockd >= 0`, so on any chart the release/general pair's separation is
`gain * (1 + lockd)` when release trails and its reciprocal when release leads. Measured over
the 60 LN charts that is 0.6663 at the tightest and 0.621 at the widest release lead
(`lockd` reaches 1.497 on the Inverse ladder), and **37 of the 60** LN charts have this pair as
their top two — well over a quarter of the corpus pinned at or above 0.62 no matter what else
changes. Reaching a median of 0.5 would then need 61 of the remaining 83 charts below 0.5
(32 of 120 are, today, and none of them is one of the pinned 37). Reaching the committed
threshold means changing the release axis' form, not its calibration — ADR-0015's stage-1
record holds that trade. The committed thresholds stay in the tool, and in the docstring above,
as the target.
"""

import statistics

import pytest


#: The separation figures issue #52 measured last. They are upper bounds, not targets: the
#: committed thresholds are the tool's `SEPARATION_MEDIAN_MAX` / `SEPARATION_HIGH_MAX`, and the
#: test fails if either bound loosens. Stage 2 (the rice axes' peak-density carrier) moved them
#: from the 0.8076 / 61 that stage 1 was accepted at to 0.7638 / 55.
ACCEPTED_SEPARATION_MEDIAN = 0.7638
ACCEPTED_CHARTS_ABOVE_HIGH = 55


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
