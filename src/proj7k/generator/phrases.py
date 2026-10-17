"""
Measures that sound the same get the same notes (ADR-0030).

A mapper charts a repeated phrase the same way each time it comes back; a note-by-note threshold on
the onset strength does not, because the strength wobbles a little from one pass to the next (a fill,
a crash, the mix), and so the same four bars come out with a note more here and a note less there.

Measures are compared by the onset strengths of their grid points in the three bands. Each measure
joins the earlier group it is most like (cosine similarity at least `SIMILAR`), or starts a group of
its own; then every point takes the mean strengths of its place across its group, unless the group
disagrees there (the weakest under half the strongest, by more than `SPREAD`: a fill, a crash, a
melody note that only one pass has), where each keeps its own. Only measures with the same grid (the same meter and the same binary or ternary split
of every beat) are compared.
"""

from dataclasses import replace
from typing import Dict, List, Sequence, Tuple

import numpy as np

from proj7k.generator.grid import GridPoint

#: Cosine similarity of two measures' band strengths above which they are the same phrase.
SIMILAR = 0.90
#: A measure this quiet (mean strength) is left alone: in a quiet passage everything looks alike.
MIN_ACTIVITY = 0.05
#: Strengths of one place across a group this far apart, the weakest under half the strongest, are a
#: real difference and kept; anything closer is the same hit played a little louder or softer.
SPREAD = 0.15


def unify_repeats(grid: Sequence[GridPoint]) -> Tuple[List[GridPoint], List[int]]:
    """The grid with repeated measures made alike, and for each point the group whose mean strengths it
    took (-1 where it kept its own)."""
    bars: Dict[int, List[int]] = {}
    for i, p in enumerate(grid):
        bars.setdefault(p.bar, []).append(i)
    groups: List[Tuple[tuple, np.ndarray, List[int]]] = []   # (layout, summed vector, bar ids)
    group_of_bar: Dict[int, int] = {}
    vectors: Dict[int, np.ndarray] = {}
    for bar, idx in sorted(bars.items()):
        layout = tuple(grid[i].slot for i in idx)
        v = np.array([[grid[i].low, grid[i].mid, grid[i].high] for i in idx], dtype=float).ravel()
        vectors[bar] = v
        if v.mean() < MIN_ACTIVITY:
            continue
        best, best_sim = -1, SIMILAR
        for g, (lay, total, _members) in enumerate(groups):
            if lay != layout:
                continue
            sim = float(v @ total / (np.linalg.norm(v) * np.linalg.norm(total) + 1e-12))
            if sim >= best_sim:
                best, best_sim = g, sim
        if best < 0:
            groups.append((layout, v.copy(), [bar]))
            group_of_bar[bar] = len(groups) - 1
        else:
            lay, total, members = groups[best]
            groups[best] = (lay, total + v, members + [bar])
            group_of_bar[bar] = best

    out = list(grid)
    point_group = [-1] * len(grid)
    for g, (_lay, _total, members) in enumerate(groups):
        if len(members) < 2:
            continue
        stacks = [bars[b] for b in members]
        for j in range(len(stacks[0])):
            pts = [grid[s[j]] for s in stacks]
            strengths = [p.strength for p in pts]
            if min(strengths) < 0.5 * max(strengths) and max(strengths) - min(strengths) > SPREAD:
                continue
            mean = dict(strength=float(np.mean([p.strength for p in pts])),
                        low=float(np.mean([p.low for p in pts])),
                        mid=float(np.mean([p.mid for p in pts])),
                        high=float(np.mean([p.high for p in pts])),
                        brightness=float(np.mean([p.brightness for p in pts])))
            for s in stacks:
                out[s[j]] = replace(grid[s[j]], **mean)
                point_group[s[j]] = g
    return out, point_group
