"""
Jack speed (ADR-0051): a sustained jack near a finger's speed limit costs more than its rate.

Before this, the engine was homogeneous of degree one in time: a chart played r times faster read exactly
r times the difficulty, so a 200 BPM 1/4 single-column jack (13.3 taps a second on one finger, about the
limit of a human hand) read 6.61 stars, an 8th-dan chart. The surcharge holds the engine to the steep rise a
player feels near the limit, without touching streams, minijacks, releases or the benchmark ladder's order.
"""

from dataclasses import replace

import pytest

from proj7k.dan import CANONICAL_DAN_SR
from proj7k.engine import evaluate_notes
from proj7k.engine.demand import hand_demand
from proj7k.engine.events import preprocess
from proj7k.engine.params import DEFAULT, V02

FLAT = replace(DEFAULT, jack_gain=0.0)


def _chart(rows, bpm, seconds=30.0):
    step = 60.0 / bpm / 4.0
    return [(c, 1.0 + k * step, None) for k in range(int(seconds / step)) for c in rows(k)]


def _jack(bpm):
    return _chart(lambda k: [1], bpm)


def _roll(bpm):
    return _chart(lambda k: [k % 7], bpm)


def test_a_sustained_200_bpm_quarter_jack_reads_above_the_top_dan():
    assert evaluate_notes(_jack(200)).total_stars > CANONICAL_DAN_SR["Stellium"]


def test_without_the_surcharge_it_read_as_an_8th_dan_chart():
    # the defect this answers, kept so a later change cannot quietly bring the linear reading back
    assert evaluate_notes(_jack(200), FLAT).total_stars < CANONICAL_DAN_SR["9th"]


def test_jack_difficulty_rises_faster_than_its_rate_near_the_limit():
    slow, fast = evaluate_notes(_jack(150)).total_D, evaluate_notes(_jack(200)).total_D
    assert fast / slow > 1.5 * (200 / 150)
    # and is monotone through it
    stars = [evaluate_notes(_jack(bpm)).total_stars for bpm in (120, 150, 170, 185, 200, 215, 230)]
    assert stars == sorted(stars)


@pytest.mark.parametrize("bpm", [150, 200, 230])
def test_streams_without_jacks_are_untouched(bpm):
    # every column comes back after seven rows, far below the limit: the surcharge is there but negligible
    ev = preprocess(_roll(bpm), DEFAULT)
    assert hand_demand(ev, 0, DEFAULT).v == pytest.approx(hand_demand(ev, 0, FLAT).v, rel=1e-6)


def test_minijacks_do_not_reach_the_surcharge():
    # three-note jacks at 20 Hz between slow bars: the window of three repeats always holds the long interval
    # before the minijack, and the surcharge sees the slowest of them, about 1 Hz
    notes = []
    for bar in range(30):
        t = bar * 1.0
        notes += [(2, t, None), (2, t + 0.05, None), (2, t + 0.10, None), (5, t + 0.5, None)]
    ev = preprocess(notes, DEFAULT)
    assert ev.j_run.max() < 2.0
    assert evaluate_notes(notes).total_D == pytest.approx(evaluate_notes(notes, FLAT).total_D, rel=1e-6)


def test_the_surcharge_does_not_touch_releases():
    notes = [(c, i * 0.075, i * 0.075 + 0.04 + 0.3) for i, c in enumerate([1] * 200)]
    ev = preprocess(notes, DEFAULT)
    a, b = hand_demand(ev, 0, FLAT), hand_demand(ev, 0, DEFAULT)
    assert (a.v[ev.rel] == b.v[ev.rel]).all()
    assert (b.v[~ev.rel] >= a.v[~ev.rel]).all()


def test_the_prototype_constants_have_no_surcharge():
    assert V02.jack_gain == 0.0


def test_one_stray_short_interval_does_not_charge_its_neighbours():
    # a 20 ms note dropped into a 10 Hz jack splits one interval; the slowest of the last three stays 10 Hz
    notes = [(1, k * 0.1, None) for k in range(100)] + [(1, 5.0 + 0.02, None)]
    ev = preprocess(notes, DEFAULT)
    assert ev.j_run.max() == pytest.approx(10.0)
