"""
How a chart's keys read, measured the same way for a generated chart and a chart people wrote (ADR-0030; kept by
ADR-0046 for the evaluation once proj7k's own pattern model went): stairs, trills, jacks, repeated keys and
rhythms, how the density moves, and which snaps the rows sit on.
"""

from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np

COLUMNS = 7
#: How far back (beats) a 1-beat stretch looks for one with its rhythm, nearest first.
REPEAT_BACK = (1, 2, 4, 8)
_BEAT_EPS = 0.02


def mirror(mask: int) -> int:
    out = 0
    for c in range(COLUMNS):
        if mask >> c & 1:
            out |= 1 << (COLUMNS - 1 - c)
    return out


def popcount(mask: int) -> int:
    return bin(mask).count("1")


def rows_of_chart(hit_objects: Iterable) -> List[Tuple[float, int]]:
    """(time ms, mask) of every row of a chart; long notes count by their heads."""
    by: Dict[int, int] = {}
    for o in hit_objects:
        if 0 <= o.column < COLUMNS:
            t = int(round(o.time))
            by[t] = by.get(t, 0) | (1 << o.column)
    return sorted(by.items())


def pattern_stats(rows: Sequence[Tuple[float, int]], beat_ms: float) -> Dict[str, float]:
    """How a chart's keys read, in the terms the generator is measured by against charts people wrote:

    - `roll`, `trill`: of three single notes in a row, the share that are stairs (c, c±1, c±2) and
      trills (a, b, a);
    - `jack_half`: of rows half a beat or less after the previous one, the share that hit a column of it;
    - `key_repeat`: of 1-beat stretches whose rhythm (positions and chord sizes) repeats the most recent
      such stretch up to 8 beats back, the share whose keys repeat too, as they were or mirrored;
    - `rhythm_repeat`: of measures (4 beats from the first row) with notes, the share whose rhythm is
      exactly that of an earlier measure;
    - `density_cv`: how much the number of rows changes from one 16-beat stretch to the next
      (coefficient of variation; ranked charts about 0.3 at every level);
    - `density_floor`, `density_peak`: the 10th and 90th percentile of the notes per 16-beat stretch,
      over its median (how easy the easy parts are, how hard the hard ones);
    - `rows_per_s`, `notes_per_s`: rows and notes per second from the first row to the last;
    - `quarter`, `triplet`: the share of rows on a quarter (1/16 note) or third of a beat.
    """
    out = {"roll": 0.0, "trill": 0.0, "jack_half": 0.0, "key_repeat": 0.0, "rhythm_repeat": 0.0,
           "density_cv": 0.0, "density_floor": 0.0, "density_peak": 0.0, "rows_per_s": 0.0, "notes_per_s": 0.0,
           "quarter": 0.0, "triplet": 0.0}
    if len(rows) < 3 or beat_ms <= 0:
        return out
    times = [t for t, _ in rows]
    masks = [m for _, m in rows]
    tri = rolls = trills = 0
    for i in range(2, len(rows)):
        if all(popcount(masks[j]) == 1 for j in (i - 2, i - 1, i)):
            a, b, c = (masks[j].bit_length() - 1 for j in (i - 2, i - 1, i))
            tri += 1
            rolls += (b - a) == (c - b) and abs(b - a) == 1
            trills += a == c and a != b
    close = jacks = 0
    for i in range(1, len(rows)):
        if times[i] - times[i - 1] <= 0.51 * beat_ms:
            close += 1
            jacks += bool(masks[i] & masks[i - 1])
    t0 = times[0]

    def stretches(span: float) -> Dict[int, List[Tuple[int, int]]]:
        d: Dict[int, List[Tuple[int, int]]] = {}
        for t, m in rows:
            k = int(np.floor((t - t0) / beat_ms / span + _BEAT_EPS))
            d.setdefault(k, []).append((int(round(((t - t0) / beat_ms - k * span) * 48)), m))
        return d

    beats = stretches(1.0)
    matched = kept = 0
    for k, w in beats.items():
        if len(w) < 2:
            continue
        rhythm = [(p, popcount(m)) for p, m in w]
        for back in REPEAT_BACK:
            v = beats.get(k - back)
            if v and [(p, popcount(m)) for p, m in v] == rhythm:
                matched += 1
                kept += [m for _, m in v] == [m for _, m in w] or [mirror(m) for _, m in v] == [m for _, m in w]
                break
    seen = set()
    bars = n_bars = 0
    for k, w in sorted(stretches(4.0).items()):
        sig = tuple((p, popcount(m)) for p, m in w)
        n_bars += 1
        bars += sig in seen
        seen.add(sig)
    t_rel = (np.array(times) - t0) / beat_ms
    counts = np.bincount((t_rel // 16).astype(int))
    counts = counts[counts > 0]
    stretch = (t_rel // 16).astype(int)
    notes = np.bincount(stretch, weights=[popcount(m) for m in masks])
    notes = notes[notes > 0]
    mid = float(np.median(notes))
    span_s = max((times[-1] - t0) / 1000.0, 1e-3)
    out.update(density_floor=float(np.percentile(notes, 10)) / mid, density_peak=float(np.percentile(notes, 90)) / mid,
               rows_per_s=len(rows) / span_s, notes_per_s=sum(popcount(m) for m in masks) / span_s)
    frac = t_rel % 1.0
    near = lambda f: np.minimum(np.abs(frac - f), 1 - np.abs(frac - f)) < 0.03  # noqa: E731
    out.update(density_cv=float(counts.std() / counts.mean()) if len(counts) > 1 else 0.0,
               quarter=float(np.mean(near(0.25) | near(0.75))), triplet=float(np.mean(near(1 / 3) | near(2 / 3))))
    out.update(roll=rolls / tri if tri else 0.0, trill=trills / tri if tri else 0.0,
               jack_half=jacks / close if close else 0.0, key_repeat=kept / matched if matched else 0.0,
               rhythm_repeat=bars / n_bars if n_bars else 0.0)
    return out
