"""
CI force for the four radar-orthogonality thresholds (issue #47's AC1, welded in by #52).

`tools/radar_orthogonality_report.py` has declared these four thresholds since #47, and until
this module existed nothing asserted them: the tool printed a verdict nobody read, so a
regression could sit in the tree indefinitely. Issue #52 confirmed that directly — the file the
tool's docstring pointed at (`tests/test_radar_orthogonality.py`) did not exist.

Every threshold is imported from the tool rather than restated, so the tool cannot print one
verdict and CI assert another. Measured over the 120-chart benchmark:

    median separation            0.698   (threshold <= 0.5)   <- not yet met
    charts above 0.8             44      (threshold <= 10)    <- not yet met
    LN Release own-axis hits     6/15    (threshold >= 8)     <- not yet met
    Regular Speed own-axis hits  14/15   (threshold >= 12)

These are asserted rather than waived: a threshold with an escape hatch is the situation this
module exists to end. Which of them a change is expected to move is recorded in
`docs/adr/0015` — separation is a *shape* property of the drivers (stage 2), and LN Release's
own-axis hits are capped by construction on this corpus, where the release ladder's low tiers
lock no deeper than the General ladder's middle (see `RadarOptions.ln_release_gain`).
"""

import statistics

import pytest


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


def test_median_separation_is_within_the_committed_threshold(
    benchmark_drivers, benchmark_manifest, orthogonality_report
):
    ratios = [
        orthogonality_report._separation(d)
        for d in _drivers_of_all(benchmark_drivers, benchmark_manifest)
    ]
    median = statistics.median(ratios)
    assert median <= orthogonality_report.SEPARATION_MEDIAN_MAX, (
        f"median second-highest/highest driver {median:.4f} > "
        f"{orthogonality_report.SEPARATION_MEDIAN_MAX}: the axes are still coupled"
    )


def test_charts_above_the_high_separation_threshold_are_few(
    benchmark_drivers, benchmark_manifest, orthogonality_report
):
    ratios = [
        orthogonality_report._separation(d)
        for d in _drivers_of_all(benchmark_drivers, benchmark_manifest)
    ]
    high = sum(1 for r in ratios if r > orthogonality_report.SEPARATION_HIGH_THRESHOLD)
    assert high <= orthogonality_report.SEPARATION_HIGH_MAX, (
        f"{high} charts read above {orthogonality_report.SEPARATION_HIGH_THRESHOLD} "
        f"(limit {orthogonality_report.SEPARATION_HIGH_MAX})"
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


def test_the_tools_own_verdict_agrees_with_these_assertions(
    benchmark_drivers, benchmark_manifest, orthogonality_report
):
    """
    The tool's `verdict()` over the same drivers, so the printed report and CI cannot drift.

    The point of reading the tool's thresholds instead of restating them is that a developer
    reading `tools/radar_orthogonality_report.py`'s output sees exactly what CI enforces; this
    test is what makes that claim checkable rather than aspirational.
    """
    rows = [
        {"group": group, "tier": tier, "drivers": benchmark_drivers(group, tier)}
        for group in benchmark_manifest
        for tier in benchmark_manifest[group]
    ]
    summary = orthogonality_report.summarize(rows)
    passed, failures = orthogonality_report.verdict(summary)
    assert passed, "radar orthogonality verdict failed: " + "; ".join(failures)
