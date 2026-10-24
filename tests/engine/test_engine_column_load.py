"""
Same-finger load (ADR-0044): at one note rate, notes held on fewer columns are not easier.

The v0.2 prototype read a stream spread over seven columns harder than the same stream on two (one per hand):
its neighbour-finger coupling grows with the chart's note rate while the same-finger rate `j` only counted once.
`k_same` weights `j`; these hold the engine to the order a player feels.
"""

import pytest

from proj7k.engine import evaluate_notes
from proj7k.engine.params import V02


def _stream(nps, columns, seconds=40.0):
    step = 1.0 / nps
    return [(columns[i % len(columns)], i * step, None) for i in range(int(nps * seconds))]


SEVEN = [0, 4, 1, 5, 2, 6, 3]
TWO = [1, 5]


@pytest.mark.parametrize("nps", [16, 20])
def test_two_columns_at_the_same_rate_read_harder_than_seven(nps):
    assert evaluate_notes(_stream(nps, TWO)).total_D > evaluate_notes(_stream(nps, SEVEN)).total_D


@pytest.mark.parametrize("nps", [16, 20])
def test_the_prototype_read_them_the_other_way(nps):
    # the defect this weight answers, kept so a later change cannot quietly bring it back as "the v0.2 order"
    assert evaluate_notes(_stream(nps, TWO), V02).total_D < evaluate_notes(_stream(nps, SEVEN), V02).total_D


def test_a_finger_taking_every_other_note_reads_harder_than_the_roll():
    step = 60.0 / 220.0 / 4.0
    roll = [(k % 7, k * step, None) for k in range(240)]
    anchored = [((1 if k % 2 == 0 else 2 + (k // 2) % 5), k * step, None) for k in range(240)]
    assert evaluate_notes(anchored).total_stars > evaluate_notes(roll).total_stars + 0.5


def test_the_weight_does_not_touch_releases():
    # an LN's release reads its own length, not a finger repeating; only presses are weighted
    notes = [(c, i * 0.25, i * 0.25 + 0.4) for i, c in enumerate([0, 1, 2, 4, 5, 6] * 20)]
    from dataclasses import replace

    from proj7k.engine.demand import hand_demand
    from proj7k.engine.events import preprocess

    ev = preprocess(notes, V02)
    a = hand_demand(ev, 0, V02)
    b = hand_demand(ev, 0, replace(V02, k_same=3.0))
    assert (a.v[ev.rel] == b.v[ev.rel]).all()
    assert (b.v[~ev.rel] >= a.v[~ev.rel]).all()
