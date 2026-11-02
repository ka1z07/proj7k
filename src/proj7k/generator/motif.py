"""
A motif's way of writing, read off its notes and put onto another stretch (ADR-0055).

Mapperatorinator can't be told a style (ADR-0047 phase 2), so imitation happens outside the model, in two steps:

1. `features` reads how a stretch is written, independent of the music under it: the chord sizes, how each row
   moves from the one before (a jack keeps a column, a stair steps one over, a trill goes back to the row before
   last, a jump lands elsewhere), which way it moves, which hand it lands on, the gaps between rows in beats and the
   long-note share. `similarity` compares two of those (1 = written alike). Candidates for a rewrite are ranked by
   how alike they are to the motif.
2. `restyle` keeps a stretch's rows where they are (its rhythm, which the model wrote to the music, and how many
   notes each row has) and gives them the motif's columns, the motif's rows walked in order. `strength` (0..1) is
   the share of rows rewritten this way; the rest keep the model's columns. A row keeps its own long notes'
   lengths. The result goes through `legal.sanitize`.
"""

import math
import random
from dataclasses import replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from proj7k.generator import legal
from proj7k.parser import HitObject, NoteType

KEYS = 7
#: Notes this close (ms) are one row.
ROW_MS = 2.0
#: The gaps between rows read on these beat fractions (1/1 .. 1/8, and triplets); anything longer is "long".
GAPS = (1.0, 1 / 2, 1 / 3, 1 / 4, 1 / 6, 1 / 8)
MOVES = ("jack", "stair", "trill", "jump")
#: How much each feature group counts in `similarity`.
WEIGHTS = {"chord": 1.0, "move": 1.5, "shift": 0.75, "hand": 0.5, "gap": 1.0, "ln": 0.5}

Row = Tuple[float, Tuple[int, ...], List[HitObject]]


def rows(objs: Sequence[HitObject]) -> List[Row]:
    """(time, sorted columns, its notes) per row, in time order."""
    out: List[Row] = []
    for o in sorted(objs, key=lambda o: (o.time, o.column)):
        if out and o.time - out[-1][0] <= ROW_MS:
            out[-1][2].append(o)
        else:
            out.append((float(o.time), (), [o]))
    return [(t, tuple(sorted({o.column for o in notes})), notes) for t, _, notes in out]


def _move(prev2: Optional[Tuple[int, ...]], prev: Tuple[int, ...], cur: Tuple[int, ...]) -> str:
    if set(prev) & set(cur):
        return "jack"
    if prev2 is not None and set(prev2) == set(cur):
        return "trill"
    if all(min(abs(c - p) for p in prev) == 1 for c in cur):
        return "stair"
    return "jump"


def _gap_bin(beats: float) -> int:
    for i, g in enumerate(GAPS):
        if abs(beats - g) <= 0.08 * g + 0.01:
            return i
    return len(GAPS)   # off the grid or longer than a beat


def _hist(values: Sequence[int], n: int) -> np.ndarray:
    h = np.bincount(np.asarray(values, dtype=int), minlength=n).astype(float)[:n]
    return h / h.sum() if h.sum() else h


def features(objs: Sequence[HitObject], beat_of: Callable[[float], float]) -> Dict[str, np.ndarray]:
    """How a stretch is written (see the module docstring), each group a distribution that sums to 1."""
    rs = rows(objs)
    chords = [len(c) - 1 for _, c, _ in rs]
    moves, shifts, gaps = [], [], []
    for k in range(1, len(rs)):
        prev2 = rs[k - 2][1] if k >= 2 else None
        moves.append(MOVES.index(_move(prev2, rs[k - 1][1], rs[k][1])))
        d = float(np.mean(rs[k][1])) - float(np.mean(rs[k - 1][1]))
        shifts.append(0 if d < -0.5 else 2 if d > 0.5 else 1)
        gaps.append(_gap_bin(beat_of(rs[k][0]) - beat_of(rs[k - 1][0])))
    hands = [0 if all(c < 3 for c in cols) else 2 if all(c > 3 for c in cols) else 1 for _, cols, _ in rs]
    ln = sum(o.note_type == NoteType.LN for o in objs) / max(1, len(objs))
    return {"chord": _hist(chords, KEYS), "move": _hist(moves, len(MOVES)), "shift": _hist(shifts, 3),
            "hand": _hist(hands, 3), "gap": _hist(gaps, len(GAPS) + 1), "ln": np.array([ln, 1.0 - ln])}


def similarity(a: Dict[str, np.ndarray], b: Dict[str, np.ndarray]) -> float:
    """1 minus the weighted mean total-variation distance between the two stretches' feature groups."""
    total = sum(WEIGHTS.values())
    d = sum(w * 0.5 * float(np.abs(a[k] - b[k]).sum()) for k, w in WEIGHTS.items())
    return 1.0 - d / total


def _fit(cols: Tuple[int, ...], size: int, prev: Tuple[int, ...]) -> Tuple[int, ...]:
    """The motif row `cols` made `size` notes wide: extra columns are the free ones (not in the row before, so no
    jack the motif didn't write) nearest the row's own; surplus columns go from the outside in."""
    cur = list(cols)
    if len(cur) > size:
        mid = float(np.mean(cur))
        cur = sorted(cur, key=lambda c: abs(c - mid))[:size]
    while len(cur) < size:
        free = [c for c in range(KEYS) if c not in cur]
        mid = float(np.mean(cur)) if cur else 3.0
        free.sort(key=lambda c: (c in prev, abs(c - mid), c))
        cur.append(free[0])
    return tuple(sorted(cur))


def restyle(objs: Sequence[HitObject], motif: Sequence[HitObject], beat_of: Callable[[float], float] = None,
            strength: float = 1.0, seed: int = 0, timing=(), chords: bool = False) -> List[HitObject]:
    """`objs` with the motif's columns on its own rows (see the module docstring). The motif's rows are walked in
    order, one per row of `objs`, round and round (every other pass mirrored, as a mapper varies a repeat); each
    is made as wide as the row it lands on. `strength` is the share of rows that take the motif's columns (the
    others keep the model's; the walk goes on either way)."""
    src = [c for _, c, _ in rows(motif)]
    rs = rows(objs)
    if not src or not rs or strength <= 0:
        return list(objs)
    rng = random.Random(seed)
    out: List[HitObject] = []
    prev: Tuple[int, ...] = ()
    for k, (t, cols, notes) in enumerate(rs):
        if rng.random() < strength:
            lap, j = divmod(k, len(src))
            base = tuple(KEYS - 1 - c for c in src[j]) if lap % 2 else src[j]
            new = base if chords else _fit(base, len(cols), prev)
        else:
            new = cols
        lengths = sorted((o.end_time - o.time for o in notes if o.note_type == NoteType.LN and o.end_time), reverse=True)
        for i, c in enumerate(new):
            if i < len(lengths):
                out.append(HitObject(column=c, time=t, note_type=NoteType.LN, end_time=t + lengths[i]))
            else:
                out.append(HitObject(column=c, time=t, note_type=NoteType.RICE))
        prev = new
    fixed, _ = legal.sanitize(out, timing)
    return fixed
