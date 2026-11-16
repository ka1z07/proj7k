"""
ADR-0062: an LN's LN-ness rises smoothly with its real length (0 at `l_min`, 1 at `l_full`), so playing a chart
at another rate, which moves every LN's real length, moves the stars continuously. The hard 100 ms cut it replaces
turned a batch of short LNs into rices the moment a faster rate pushed them under it, and the faster rate read easier
("Duplicity Shade [Metamorphosis]" 0.75x 11.51, 0.76x 11.08, 0.77x 10.55).
"""

from dataclasses import replace

import pytest

from proj7k.engine.evaluate import evaluate_notes
from proj7k.engine.events import ln_weight, preprocess
from proj7k.engine.params import DEFAULT

HARD_CUT = replace(DEFAULT, l_min=0.100, l_full=0.100)


def _short_ln_stream(rate: float):
    """A 7K stream at 180 BPM 1/4 where every other note is a 76 ms LN (in chart time), played at `rate`."""
    step = 60.0 / 180.0 / 4.0
    notes = []
    for k in range(400):
        t = k * step
        col = (k * 3) % 7
        notes.append((col, t / rate, (t + 0.076) / rate if k % 2 == 0 else None))
    return notes


RATES = [round(0.70 + 0.005 * i, 3) for i in range(31)]


def test_ln_weight_is_a_smoothstep_between_the_two_lengths():
    assert ln_weight(DEFAULT.l_min, DEFAULT) == 0.0
    assert ln_weight(DEFAULT.l_full, DEFAULT) == 1.0
    mid = 0.5 * (DEFAULT.l_min + DEFAULT.l_full)
    assert ln_weight(mid, DEFAULT) == pytest.approx(0.5)
    xs = [DEFAULT.l_min + (DEFAULT.l_full - DEFAULT.l_min) * i / 50 for i in range(51)]
    ws = [ln_weight(x, DEFAULT) for x in xs]
    assert ws == sorted(ws)


def test_the_hard_cut_is_the_limit_l_min_equals_l_full():
    assert ln_weight(0.0999, HARD_CUT) == 0.0
    assert ln_weight(0.100, HARD_CUT) == 1.0


def test_a_partial_lns_release_counts_in_proportion_to_its_ln_ness():
    ev = preprocess([(0, 0.0, 0.100), (1, 0.5, None)], DEFAULT)
    g = ln_weight(0.100, DEFAULT)
    assert 0.0 < g < 1.0
    assert ev.n == 3 and ev.wt.tolist() == [1.0, g, 1.0]
    assert ev.lnw.tolist() == [g, g, 0.0]


def test_the_hard_cut_reads_a_faster_rate_easier():
    """The defect: the hard cut drops where the 76 ms LNs cross 100 ms of real time (0.76x)."""
    stars = [evaluate_notes(_short_ln_stream(r), HARD_CUT).total_stars for r in RATES]
    assert min(b - a for a, b in zip(stars, stars[1:])) < -0.05


def test_stars_rise_with_the_rate_across_the_ln_ramp():
    stars = [evaluate_notes(_short_ln_stream(r)).total_stars for r in RATES]
    assert all(b > a for a, b in zip(stars, stars[1:])), [round(b - a, 4) for a, b in zip(stars, stars[1:])]
