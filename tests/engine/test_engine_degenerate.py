"""A chart with nothing to solve (a couple of notes, a single hold) reads as zero difficulty, cleanly."""

import math
import warnings

import pytest

from proj7k.engine import evaluate_notes

TINY_CHARTS = {
    "two rice": [(0, 0.0, None), (1, 0.5, None)],
    "one rice": [(0, 0.0, None)],
    "one hold": [(0, 0.0, 0.5)],
    "six simultaneous holds": [(c, 0.0, 2.0) for c in range(6)],
}


@pytest.mark.parametrize("name", sorted(TINY_CHARTS))
def test_a_chart_with_nothing_to_solve_reads_zero_with_finite_numbers_and_no_warning(name):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        profile = evaluate_notes(TINY_CHARTS[name])

    assert profile.total_stars == 0.0
    numbers = [profile.dominance_margin]
    for reading in profile.skills.values():
        numbers += [reading.D, reading.stars, reading.coverage, reading.dominance]
    assert all(math.isfinite(x) for x in numbers)
