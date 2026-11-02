"""
The engine's speed terms (ADR-0052): the three things the owner asked the engine to read, on synthetic charts.

1. Fast minijacks mixed into a stream count: a quarter of the rows repeating one column of the row before adds
   clearly more at 210 BPM than at 150 BPM.
2. A short, dense, fast section in an otherwise easy chart counts: the hardest 8 s window (`burst_kappa`) carries
   the chart's reading, where the 4 s hand memory used to average a 2 s burst away.
3. The same pattern grows faster than linearly with BPM, the more so the more it rests on one finger; a single-note
   roll (one press per row, no finger repeats) stays about linear, as the owner ruled.

Where a claim is checked against the v0.2 engine (`V02`, the speed terms off) it is on the level `D`, since both
read through today's star scale.
"""

import dataclasses
import random

import numpy as np
import pytest

from proj7k.engine import evaluate_notes
from proj7k.engine.params import DEFAULT, V02


def _rows(rowfn, bpm, seconds, t0=1.0, div=4):
    step, out, k, t = 60.0 / bpm / div, [], 0, t0
    while t < t0 + seconds:
        out.extend((c, t, None) for c in rowfn(k))
        k += 1
        t += step
    return out, t


def _jumpstream(seed=0, minijack=0.0):
    """Two notes a row, no column of the row before, except (with probability `minijack`) one that repeats."""
    rng, prev = random.Random(seed), [set()]

    def row(_):
        s = set()
        if minijack and prev[0] and rng.random() < minijack:
            s.add(rng.choice(sorted(prev[0])))
        while len(s) < 2:
            c = rng.randrange(7)
            if c not in prev[0]:
                s.add(c)
        prev[0] = s
        return sorted(s)
    return row


def _chordjack(seed=0):
    rng, prev = random.Random(seed), [set()]

    def row(_):
        s = {c for c in prev[0] if rng.random() < 0.5}
        while len(s) < 3:
            s.add(rng.randrange(7))
        prev[0] = s
        return sorted(s)
    return row


PATTERNS = {
    "jumpstream": _jumpstream,
    "chordjack": _chordjack,
    "roll": lambda: (lambda k: [k % 7]),
    "jack": lambda: (lambda k: [1]),
}


def _stars(notes, params=DEFAULT):
    return float(evaluate_notes(notes, params).total_stars)


def _level(notes, params=DEFAULT):
    return float(evaluate_notes(notes, params).total_D)


def _exponent(pattern, params=DEFAULT, read=_stars):
    """d ln(reading) / d ln(BPM) from 150 to 225 BPM (1/4 rows, 30 s)."""
    lo, hi = (read(_rows(PATTERNS[pattern](), bpm, 30)[0], params) for bpm in (150, 225))
    return float(np.log(hi / lo) / np.log(1.5))


def test_fast_minijacks_mixed_into_a_stream_count():
    def gain(bpm, params=DEFAULT, read=_stars):
        plain = read(_rows(_jumpstream(), bpm, 60)[0], params)
        mixed = read(_rows(_jumpstream(minijack=0.25), bpm, 60)[0], params)
        return mixed / plain if read is _level else mixed - plain

    slow, fast = gain(150), gain(210)
    assert fast > 1.0                   # about +1.4 stars at 210 BPM (v0.2 engine: +0.4)
    assert fast > 2.0 * slow            # and it grows with the speed of the repeats
    assert gain(210, read=_level) - 1.0 > 2.0 * (gain(210, V02, _level) - 1.0)


@pytest.mark.parametrize("seconds", [2, 4, 8])
def test_a_short_fast_section_in_an_easy_chart_counts(seconds):
    easy, end = _rows(_jumpstream(), 120, 90, div=2)
    burst, _ = _rows(_jumpstream(5), 220, seconds, t0=end)
    alone = _stars(easy)
    with_burst = _stars(easy + burst)
    # Even a 2 s burst lifts a 2.3-star chart by more than a star and a half (v0.2 engine: +0.8).
    assert with_burst - alone > 1.5
    lift = _level(easy + burst) / _level(easy)
    assert lift > _level(easy + burst, V02) / _level(easy, V02) + 0.2


def test_the_burst_term_only_ever_raises_the_reading():
    notes, _ = _rows(_jumpstream(3), 180, 60)
    off = evaluate_notes(notes, dataclasses.replace(DEFAULT, burst_kappa=0.0))
    assert float(evaluate_notes(notes).total_D) >= float(off.total_D)


@pytest.mark.parametrize("pattern,low", [("jumpstream", 1.15), ("chordjack", 1.4), ("jack", 1.6)])
def test_the_same_pattern_grows_faster_than_linearly_with_bpm(pattern, low):
    assert _exponent(pattern) >= low
    assert _exponent(pattern, V02, _level) <= 1.1    # the v0.2 engine's level was about linear in BPM


def test_a_single_note_roll_stays_about_linear():
    assert 0.9 <= _exponent("roll") <= 1.1
