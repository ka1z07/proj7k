"""
Which columns each row of notes goes to.

Rows are placed one at a time, greedily, choosing among all column sets of the row's size (at most
35 for 7K) the one with the lowest cost:

- **recency** — a column hit a moment ago is expensive, falling with the square of the gap
  (75 ms costs ~6, 300 ms ~0.4). This is what keeps a fast passage from piling onto one finger;
  the engine's star reads sustained load and is nearly blind to a single overworked column, so the
  generator must not lean on it for that.
- **hand load** — each hand's recent work (a 0.5 s decaying count); the busier hand costs more. The
  middle column is the thumb's and counts half to each hand.
- **pitch** — the onset's brightness, ranked over the song, gives a preferred position from the
  left (low) to the right (high) column, so melodies move across the keyboard the way they move in pitch.
- **shape** — three or more notes all on one hand cost extra, and an exact repeat of the previous row
  costs extra (it would be a chord jack).
- a small seeded random term, so equal choices do not settle into a fixed pattern.

Deterministic for a given seed.
"""

from dataclasses import dataclass
from itertools import combinations
from typing import List, Sequence

import numpy as np

COLUMNS = 7
THUMB = 3
#: Left-hand share of each column (the thumb's column is split).
_LEFT = np.array([1.0, 1.0, 1.0, 0.5, 0.0, 0.0, 0.0])

RECENCY_MS = 180.0
HAND_TAU_S = 0.5
W_HAND = 0.35
W_PITCH = 0.45
W_ONE_HAND = 0.6
W_REPEAT = 1.0
NOISE = 0.25

_SUBSETS = {n: np.array([[c in s for c in range(COLUMNS)] for s in combinations(range(COLUMNS), n)], dtype=float)
            for n in range(1, COLUMNS + 1)}


@dataclass(frozen=True)
class Row:
    time_ms: float
    size: int
    brightness: float


def assign_columns(rows: Sequence[Row], seed: int = 0) -> List[List[int]]:
    rng = np.random.default_rng(seed)
    order = np.argsort([r.brightness for r in rows], kind="stable")
    rank = np.empty(len(rows))
    rank[order] = np.linspace(0.0, 1.0, len(rows)) if len(rows) > 1 else 0.5

    last = np.full(COLUMNS, -1e9)
    load = np.zeros(2)            # left, right
    load_t = 0.0
    prev = np.zeros(COLUMNS)
    out: List[List[int]] = []
    for i, row in enumerate(rows):
        n = int(min(max(row.size, 1), COLUMNS))
        t = row.time_ms
        load = load * np.exp(-(t - load_t) / 1000.0 / HAND_TAU_S)
        load_t = t

        gap = np.maximum(t - last, 1.0)
        recency = (RECENCY_MS / gap) ** 2
        hand = W_HAND * (_LEFT * load[0] + (1.0 - _LEFT) * load[1])
        target = rank[i] * (COLUMNS - 1) + rng.normal(0.0, 0.8)
        pitch = W_PITCH * np.abs(np.arange(COLUMNS) - target) / (COLUMNS - 1)
        per_col = recency + hand + pitch

        subsets = _SUBSETS[n]
        cost = subsets @ per_col
        if n >= 3:
            left_share = subsets @ _LEFT
            one_hand = (left_share >= n - 0.5) | (left_share <= 0.5)
            cost = cost + W_ONE_HAND * one_hand
        if n == int(prev.sum()):
            cost = cost + W_REPEAT * np.all(subsets == prev, axis=1)
        cost = cost + rng.uniform(0.0, NOISE, size=len(cost))
        chosen = subsets[int(np.argmin(cost))]

        cols = [c for c in range(COLUMNS) if chosen[c]]
        out.append(cols)
        last[chosen > 0] = t
        load[0] += float(chosen @ _LEFT)
        load[1] += float(chosen @ (1.0 - _LEFT))
        prev = chosen
    return out
