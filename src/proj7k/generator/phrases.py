"""
Measures that sound the same get the same notes (ADR-0030).

A mapper charts a repeated phrase the same way each time it comes back; a note-by-note threshold on
the onset strength does not, because the strength wobbles from one pass to the next (the mix, a
cymbal, a vocal line on top), and so the same four bars come out with a note more here and a note
less there. On real songs the wobble is large: point by point, only about a tenth of measures are
close enough to share their strengths, so the rhythm is made alike after it is picked instead.

1. `group_measures`: measures are compared by the onset strengths of their grid points in the three
   bands. Each measure joins the earlier group it is most like (cosine similarity at least `SIMILAR`)
   or starts a group of its own. Only measures with the same grid (the same meter and the same binary
   or ternary split of every beat) are compared; quiet measures are left alone.
2. `harmonize`: for each group and each place in the measure, a row picked there in most of the
   group's measures is picked in all of them, with the chord size most of them had, and in the others
   in none. A note that only some passes have survives where the music clearly has it there (`FILL`
   above the group's mean strength at that place): a fill, a crash.
"""

from collections import Counter
from typing import Dict, List, Sequence, Tuple

import numpy as np

from proj7k.generator.grid import GridPoint

#: Cosine similarity of two measures' band strengths at or above which they are the same phrase.
SIMILAR = 0.85
#: A measure this quiet (mean strength) is left alone: in a quiet passage everything looks alike.
MIN_ACTIVITY = 0.05
#: A note picked in only some of a group's measures is kept where its strength is this far above the
#: group's mean strength at that place.
FILL = 0.35
#: The most measures of one group that are given one rhythm together.
MAX_RUN = 2


def group_measures(grid: Sequence[GridPoint]) -> List[int]:
    """Each point's group of alike measures (-1: its measure is like no other)."""
    bars: Dict[int, List[int]] = {}
    for i, p in enumerate(grid):
        bars.setdefault(p.bar, []).append(i)
    groups: List[Tuple[tuple, np.ndarray, List[int]]] = []   # (layout, summed vector, bar ids)
    for bar, idx in sorted(bars.items()):
        layout = tuple(grid[i].slot for i in idx)
        v = np.array([[grid[i].low, grid[i].mid, grid[i].high] for i in idx], dtype=float).ravel()
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
        else:
            lay, total, members = groups[best]
            groups[best] = (lay, total + v, members + [bar])
    out = [-1] * len(grid)
    n = 0
    for _lay, _total, members in groups:
        # A group is charted alike in runs of at most MAX_RUN measures: within a run the rhythm is one,
        # from run to run it may move (a mapper varies a riff when it has come round a few times), and
        # the star rating climbs with the intensity in smaller steps than with one rhythm for the song.
        for start in range(0, len(members), MAX_RUN):
            run = members[start:start + MAX_RUN]
            if len(run) >= 2:
                for b in run:
                    for i in bars[b]:
                        out[i] = n
                n += 1
    return out


def harmonize(grid: Sequence[GridPoint], groups: Sequence[int], sizes: Sequence[int]) -> List[int]:
    """Chord sizes per grid point (0: no note), with the measures of each group given one rhythm."""
    out = list(sizes)
    by: Dict[Tuple[int, int], List[int]] = {}
    for i, (p, g) in enumerate(zip(grid, groups)):
        if g >= 0:
            by.setdefault((g, p.slot), []).append(i)
    for idx in by.values():
        picked = [sizes[i] for i in idx if sizes[i] > 0]
        if len(picked) * 2 > len(idx):
            size = Counter(picked).most_common(1)[0][0]
            for i in idx:
                out[i] = size
        else:
            mean = float(np.mean([grid[i].strength for i in idx]))
            for i in idx:
                if not (sizes[i] > 0 and grid[i].strength >= mean + FILL):
                    out[i] = 0
    for i, p in enumerate(grid):
        if p.silent:
            out[i] = 0
    return out
