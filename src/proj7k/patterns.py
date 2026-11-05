"""
Pattern tags: what a 7K chart is made of, by the names the community uses (ADR-0058).

The engine's eight skills say how much a chart *requires* of each skill; they do not say what the chart
*is*. A player (or the catalog, the generator, the coach) asks the second question in pattern words —
jack, chordstream, bracket, trill, roll, grace, polyrhythm, inverse, shield, release, delay, coordination
— so this module reads a chart's press rows and tags each row with the patterns it takes part in,
then reports every tag's share of the chart and the labels that share earns.

Definitions follow the osu! wiki glossary (osu!mania ranking criteria, "Beatmap/Pattern/osu!mania"), the
7K/4K dan threads and the common mapping-guide usage, written down in `TAG_DEFINITIONS`. All of them are
read against the *local grid*: the median gap between press rows within a second of the row (`unit`), so
"a stream" is rows one unit apart whatever the BPM, and "a grace" is two rows far closer than that.

    report = pattern_tags(notes)           # notes as (column, head_s, tail_s | None)
    report.shares["chordstream"]           # share of press rows that are chordstream
    report.labels                          # tags whose share clears LABEL_SHARE, strongest first

Nothing here feeds the difficulty engine: the module lives beside it (it is not part of the engine package
whose version token stamps every rating), and a tag is never a difficulty.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from proj7k.engine.events import Notes, notes_from_beatmap, notes_from_osu, preprocess
from proj7k.engine.params import DEFAULT, Params
from proj7k.engine.skills import hand_of
from proj7k.parser import Beatmap7K

#: What each tag means, in the words the chart is read with.
TAG_DEFINITIONS: Dict[str, str] = {
    "jack": "three or more consecutive notes in one column, no slower than the local grid",
    "minijack": "two consecutive notes in one column, no slower than the local grid",
    "chordjack": "a jack row of three or more notes",
    "longjack": "a jack run of six or more",
    "stream": "single notes on consecutive grid rows that do not repeat a column",
    "jumpstream": "a stream whose rows carry two notes",
    "chordstream": "a stream whose rows carry three or more notes (7K usage)",
    "dense_chordstream": "chordstream averaging three or more notes a row over two seconds",
    "trill": "two columns alternating single notes over four or more rows",
    "jumptrill": "two two-note chords alternating over four or more rows",
    "split_jumptrill": "a jumptrill whose chords each put one note on each hand",
    "bracket": "two or more trills at once: alternating chords whose columns interleave",
    "roll": "single notes walking one way across five or more columns",
    "stair": "single notes walking one way across three or four columns",
    "anchor": "one column carrying half or more of a hand's notes while the hand keeps moving",
    "grace": "a row less than 0.4 units after the previous one, on other columns, outside a burst",
    "burst": "four or more rows at 0.6 units or less, faster than the surrounding grid",
    "polyrhythm": "two rhythms at once: non-dyadic gaps (at lag one or two) holding a quarter of a two-second window",
    "hybrid": "rice and long notes mixed in the same two seconds",
    "inverse": "a press while three or more other columns are held",
    "release": "a long note's tail in a row with no press (share: such rows per press row)",
    "shield": "a rice note in a long note's column within a unit before its head or after its tail",
    "delay": "long-note heads or tails staggered across columns within half a unit",
    "coordination": "a press and another column's release within a quarter unit of each other",
}
TAGS: Tuple[str, ...] = tuple(TAG_DEFINITIONS)

#: A tag whose share of the chart reaches this is a label of the chart.
LABEL_SHARE = 0.08
#: Row gaps within this of a class ratio of the local unit belong to that class.
GRID_TOL = 0.15
#: The local grid is the median press-row gap within this many seconds either side.
UNIT_WINDOW_S = 1.0
UNIT_PERCENTILE = 70

_DYADIC = (0.25, 0.5, 1.0, 2.0, 4.0)
_POLY = (1.0 / 3.0, 2.0 / 3.0, 1.5, 4.0 / 3.0, 0.75)


@dataclass(frozen=True)
class PatternReport:
    shares: Dict[str, float]     # tag -> share of the chart's press rows
    labels: List[str]            # tags at or above LABEL_SHARE, largest share first
    press_rows: int
    long_notes: int

    def to_dict(self) -> dict:
        return {
            "labels": list(self.labels),
            "shares": {k: round(float(v), 4) for k, v in self.shares.items()},
            "press_rows": self.press_rows,
            "long_notes": self.long_notes,
        }


def _local_unit(t: np.ndarray) -> np.ndarray:
    """Per press row, the local grid: the upper-median (70th percentile) gap between press rows within
    UNIT_WINDOW_S of it, so that graces and bursts, which sit *between* grid rows, do not drag the unit down."""
    gaps = np.diff(t)
    unit = np.zeros(len(t))
    for k in range(len(t)):
        lo = np.searchsorted(t, t[k] - UNIT_WINDOW_S)
        hi = np.searchsorted(t, t[k] + UNIT_WINDOW_S, side="right")
        g = gaps[max(lo, 0):max(hi - 1, 0)]
        g = g[g > 0]
        unit[k] = float(np.percentile(g, UNIT_PERCENTILE)) if len(g) else (float(gaps[gaps > 0].min()) if (gaps > 0).any() else 1.0)
    return unit


def _gap_class(ratio: float) -> Optional[float]:
    for cls in _DYADIC + _POLY:
        if abs(ratio - cls) <= GRID_TOL * cls:
            return cls
    return None


def _monotone_runs(cols: List[Optional[int]]) -> List[Tuple[int, int]]:
    """(start, end) of maximal runs of three or more strictly monotone single-note columns (None breaks a run)."""
    runs: List[Tuple[int, int]] = []
    start, direction = 0, 0
    for k in range(1, len(cols)):
        a, b = cols[k - 1], cols[k]
        d = 0 if a is None or b is None or a == b else (1 if b > a else -1)
        if d != 0 and (direction == 0 or d == direction):
            direction = d
            continue
        if k - start >= 3:
            runs.append((start, k - 1))
        start, direction = (k - 1, d) if d != 0 else (k, 0)
    if len(cols) - start >= 3:
        runs.append((start, len(cols) - 1))
    return runs


def pattern_tags(notes: Notes, p: Params = DEFAULT, thumb: int = 0) -> PatternReport:
    """The tags of a chart given as `(column, head_s, tail_s | None)` notes; see the module docstring."""
    if len(notes) == 0:
        return PatternReport({k: 0.0 for k in TAGS}, [], 0, 0)
    ev = preprocess(notes, p)
    press = ~ev.rel
    rows = np.unique(ev.row[press])
    R = len(rows)
    if R == 0:
        return PatternReport({k: 0.0 for k in TAGS}, [], 0, 0)
    t = ev.T[rows]
    row_index = {int(r): k for k, r in enumerate(rows)}
    cols: List[set] = [set() for _ in range(R)]
    ln_cols: List[set] = [set() for _ in range(R)]
    for i in np.flatnonzero(press):
        k = row_index[int(ev.row[i])]
        cols[k].add(int(ev.col[i]))
        if ev.is_ln[i]:
            ln_cols[k].add(int(ev.col[i]))
    size = np.array([len(c) for c in cols])
    unit = _local_unit(t)
    gap = np.concatenate([[np.inf], np.diff(t)])
    ratio = gap / unit
    hand = hand_of(thumb)
    on_grid = np.array([_gap_class(r) == 1.0 for r in ratio])
    fast = np.array([r <= 0.6 for r in ratio])
    repeat = np.array([k > 0 and bool(cols[k] & cols[k - 1]) for k in range(R)])

    tag: Dict[str, np.ndarray] = {k: np.zeros(R, dtype=bool) for k in TAGS}

    # -- jacks: column repeats across consecutive rows, no slower than the grid
    run_len = np.zeros(R, dtype=int)
    for k in range(R):
        if repeat[k] and ratio[k] <= 1.0 + GRID_TOL:
            run_len[k] = run_len[k - 1] + 1 if run_len[k - 1] else 2
    # a run's length is what its last row reached; write it back over the run
    k = R - 1
    while k >= 0:
        if run_len[k] >= 2:
            L = run_len[k]
            for m in range(k - L + 1, k + 1):
                run_len[m] = L
            k -= L
        else:
            k -= 1
    tag["minijack"] = run_len == 2
    tag["jack"] = run_len >= 3
    tag["longjack"] = run_len >= 6
    tag["chordjack"] = (run_len >= 3) & (size >= 3)

    # -- streams: consecutive grid rows that do not repeat a column, in runs of four or more
    stream_row = on_grid & ~repeat
    stream_run = np.zeros(R, dtype=int)
    for k in range(R):
        stream_run[k] = stream_run[k - 1] + 1 if stream_row[k] and k > 0 else (1 if stream_row[k] else 0)
    k = R - 1
    while k >= 0:
        if stream_run[k] >= 1:
            L = stream_run[k]
            for m in range(k - L + 1, k + 1):
                stream_run[m] = L
            k -= L
        else:
            k -= 1
    in_stream = stream_run >= 4
    tag["stream"] = in_stream & (size == 1)
    tag["jumpstream"] = in_stream & (size == 2)
    tag["chordstream"] = in_stream & (size >= 3)
    dense = np.zeros(R, dtype=bool)
    for k in np.flatnonzero(tag["chordstream"]):
        lo = np.searchsorted(t, t[k] - 1.0)
        hi = np.searchsorted(t, t[k] + 1.0, side="right")
        dense[k] = size[lo:hi].mean() >= 3.0
    tag["dense_chordstream"] = dense

    # -- trills and brackets: two row sets alternating, four rows or more, on the grid
    k = 0
    while k + 3 < R:
        a, b = cols[k], cols[k + 1]
        if not a or not b or a & b:
            k += 1
            continue
        end = k + 1
        while end + 1 < R and cols[end + 1] == (a if (end + 1 - k) % 2 == 0 else b) and ratio[end + 1] <= 1.0 + GRID_TOL:
            end += 1
        L = end - k + 1
        if L >= 4:
            sel = slice(k, end + 1)
            if len(a) == 1 and len(b) == 1:
                tag["trill"][sel] = True
            else:
                both = sorted(a | b)
                owner = [0 if c in a else 1 for c in both]
                interleaved = any(owner[i] != owner[i + 1] and owner[i + 1] != owner[i + 2] for i in range(len(owner) - 2)) \
                    or (len(a) >= 2 and len(b) >= 2 and any(owner[i] != owner[i + 1] for i in range(len(owner) - 1))
                               and max(a) > min(b) and max(b) > min(a))
                if len(a) == 2 and len(b) == 2:
                    tag["jumptrill"][sel] = True
                    if len({hand[c] for c in a}) == 2 and len({hand[c] for c in b}) == 2:
                        tag["split_jumptrill"][sel] = True
                if len(a) >= 2 and len(b) >= 2 and interleaved:
                    tag["bracket"][sel] = True
            k = end + 1
        else:
            k += 1

    # -- rolls and stairs: monotone single-note runs on the grid
    single = [next(iter(c)) if len(c) == 1 and (on_grid[k] or k == 0) else None for k, c in enumerate(cols)]
    for start, end in _monotone_runs(single):
        span = len({single[m] for m in range(start, end + 1)})
        tag["roll" if span >= 5 else "stair"][start:end + 1] = True

    # -- anchors: per hand and two-second window, a column carrying half or more of a moving hand's notes
    hand_rows: Dict[int, List[Tuple[float, int, int]]] = {0: [], 1: []}  # (t, col, row k)
    for k in range(R):
        for c in cols[k]:
            hand_rows[hand[c]].append((t[k], c, k))
    for hh in (0, 1):
        items = hand_rows[hh]
        if len(items) < 8:
            continue
        ts = np.array([x[0] for x in items])
        for k in range(R):
            lo = np.searchsorted(ts, t[k] - 1.0)
            hi = np.searchsorted(ts, t[k] + 1.0, side="right")
            if hi - lo < 8:
                continue
            counts = np.bincount([items[m][1] for m in range(lo, hi)], minlength=7)
            top = int(counts.argmax())
            if counts[top] >= 0.5 * (hi - lo) and (counts > 0).sum() >= 3 and top in cols[k]:
                tag["anchor"][k] = True

    # -- graces, bursts, polyrhythm
    burst_run = np.zeros(R, dtype=int)
    for k in range(R):
        burst_run[k] = burst_run[k - 1] + 1 if fast[k] and k > 0 else 0
    k = R - 1
    while k >= 0:
        if burst_run[k] >= 1:
            L = burst_run[k]
            for m in range(k - L, k + 1):
                burst_run[m] = L
            k -= L + 1
        else:
            k -= 1
    tag["burst"] = burst_run >= 4
    tag["grace"] = (ratio < 0.4) & ~repeat & (burst_run < 3)
    # two rhythms interleaved (3 against 2, 4 against 3) show up as non-dyadic gaps either between
    # neighbouring rows or between a row and the one before that (a 1/6 row between 1/4 rows reads
    # 1/2 + 1/2 at lag one but 3/2 at lag two), so both lags are classed against the local unit
    gap2 = np.full(R, np.inf)
    gap2[2:] = t[2:] - t[:-2]
    classes = np.array([[_gap_class(r) if np.isfinite(r) else np.nan for r in ratio],
                        [_gap_class(r) if np.isfinite(r) else np.nan for r in gap2 / unit]], dtype=float)
    for k in range(R):
        lo = np.searchsorted(t, t[k] - 1.0)
        hi = np.searchsorted(t, t[k] + 1.0, side="right")
        window = classes[:, lo:hi].ravel()
        window = window[~np.isnan(window)]
        if len(window) < 12:
            continue
        poly_share = np.isin(window, _POLY).mean()
        if poly_share >= 0.25 and (np.isin(window, _DYADIC).mean() >= 0.25) and np.isin(classes[:, k], _POLY).any():
            tag["polyrhythm"][k] = True

    # -- long-note tags
    is_ln_row = np.array([len(c) > 0 for c in ln_cols])
    rice_row = np.array([len(cols[k] - ln_cols[k]) > 0 for k in range(R)])
    for k in range(R):
        lo = np.searchsorted(t, t[k] - 1.0)
        hi = np.searchsorted(t, t[k] + 1.0, side="right")
        ln_share = is_ln_row[lo:hi].mean()
        if rice_row[lo:hi].any() and is_ln_row[lo:hi].any() and 0.15 <= ln_share <= 0.85:
            tag["hybrid"][k] = True
    held_other = np.zeros(R)
    for k, r in enumerate(rows):
        held_other[k] = ev.held[r].sum() - sum(ev.held[r, c] for c in cols[k])
    tag["inverse"] = held_other >= 3

    # releases, shields, delays and coordination read the LN objects
    heads = {}
    tails = {}
    for i in range(ev.n):
        if ev.is_ln[i]:
            (tails if ev.rel[i] else heads)[int(ev.obj[i])] = (float(ev.T[ev.row[i]]), int(ev.col[i]), int(ev.row[i]))
    n_ln = len(heads)
    press_rows_set = set(int(r) for r in rows)
    isolated_rows = {r for (tt, c, r) in tails.values() if r not in press_rows_set}
    release_share = min(1.0, len(isolated_rows) / R)

    unit_at = lambda tt: unit[min(max(np.searchsorted(t, tt) - 1, 0), R - 1)]  # noqa: E731
    col_times: Dict[int, np.ndarray] = {}
    for c in range(7):
        col_times[c] = np.array(sorted(t[k] for k in range(R) if c in cols[k] and c not in ln_cols[k]))
    for obj, (tt, c, r) in heads.items():
        u = unit_at(tt)
        k = row_index[r]
        rice = col_times[c]
        before = rice[(rice < tt) & (rice >= tt - u * (1.0 + GRID_TOL))]
        if len(before):
            tag["shield"][k] = True
        if obj in tails:
            te = tails[obj][0]
            after = rice[(rice > te) & (rice <= te + u * (1.0 + GRID_TOL))]
            if len(after):
                tag["shield"][k] = True
    head_times = np.array(sorted((v[0], v[1]) for v in heads.values()))
    tail_times = np.array(sorted((v[0], v[1]) for v in tails.values()))
    for arr in (head_times, tail_times):
        if len(arr) < 2:
            continue
        for m in range(1, len(arr)):
            d = arr[m, 0] - arr[m - 1, 0]
            u = unit_at(arr[m, 0])
            if 0 < d <= 0.5 * u and arr[m, 1] != arr[m - 1, 1]:
                k = min(max(np.searchsorted(t, arr[m, 0], side="right") - 1, 0), R - 1)
                tag["delay"][k] = True
    if len(tail_times):
        tt_sorted = tail_times[:, 0]
        for k in range(R):
            u = unit[k]
            lo = np.searchsorted(tt_sorted, t[k] - 0.25 * u)
            hi = np.searchsorted(tt_sorted, t[k] + 0.25 * u, side="right")
            if any(int(tail_times[m, 1]) not in cols[k] for m in range(lo, hi)):
                tag["coordination"][k] = True

    shares = {k: float(v.mean()) for k, v in tag.items()}
    shares["release"] = release_share
    labels = [k for k, v in sorted(shares.items(), key=lambda kv: -kv[1]) if v >= LABEL_SHARE]
    return PatternReport(shares=shares, labels=labels, press_rows=R, long_notes=n_ln)


def pattern_tags_osu(content: str, p: Params = DEFAULT) -> PatternReport:
    return pattern_tags(notes_from_osu(content), p)


def pattern_tags_beatmap(beatmap: Beatmap7K, p: Params = DEFAULT) -> PatternReport:
    return pattern_tags(notes_from_beatmap(beatmap), p)
