"""
Which keys each row presses, learned from charts people wrote (ADR-0030).

A chart is read as a sequence of *rows* (the notes that start at one moment), each a set of columns,
here a 7-bit mask. The model gives the probability of the next row's mask from what a mapper sees
when they place it:

- how many notes the row has (the rhythm layer decides that),
- how long after the previous row it comes, and after the one before that (in ms, bucketed: a jack
  or a trill means something very different at 70 ms and at 300 ms),
- the previous two rows' masks.

It is a back-off n-gram over masks with Witten–Bell interpolation, four levels from the most
specific context to none:

    (n, g1, r1, g2, r2)  →  (n, g1, r1)  →  (n, g1)  →  (n)

Counts come from ranked, loved and qualified charts of the online catalog (`tools/train_pattern_model.py`),
each chart counted twice, as written and mirrored, so the model has no left/right bias. Rolls,
trills, jacks used on purpose, chord anchors and the shapes mappers favour all come from those
counts, nothing is written in by hand.

Sampling (`PatternSampler`) draws masks from the model one row at a time with a temperature, keeping
an optional hard floor on how soon a column may be hit again.
"""

import gzip
import json
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

COLUMNS = 7
FULL = (1 << COLUMNS) - 1
MODEL_PATH = Path(__file__).with_name("pattern_model.json.gz")
#: Upper edges of the gap buckets, ms. A gap past the last edge is "a pause" (bucket len(GAP_EDGES_MS)).
GAP_EDGES_MS = (55.0, 75.0, 95.0, 120.0, 150.0, 190.0, 250.0, 340.0, 500.0)
#: The context a row after a pause keeps: none of the earlier rows count.
PAUSE = len(GAP_EDGES_MS)
NONE = 0                     # mask of "no row" (start of the chart or after a pause)

MASKS_BY_SIZE: Dict[int, Tuple[int, ...]] = {
    n: tuple(sum(1 << c for c in s) for s in combinations(range(COLUMNS), n)) for n in range(1, COLUMNS + 1)
}


def mirror(mask: int) -> int:
    out = 0
    for c in range(COLUMNS):
        if mask >> c & 1:
            out |= 1 << (COLUMNS - 1 - c)
    return out


def popcount(mask: int) -> int:
    return bin(mask).count("1")


def gap_bucket(gap_ms: float) -> int:
    for i, edge in enumerate(GAP_EDGES_MS):
        if gap_ms <= edge:
            return i
    return PAUSE


def contexts(n: int, g1: int, r1: int, g2: int, r2: int) -> Tuple[str, str, str, str]:
    """The four context keys of a row, most specific first. A pause cuts off what came before it."""
    if g1 == PAUSE:
        r1 = g2 = r2 = 0
    elif g2 == PAUSE:
        r2 = 0
    return (f"{n}|{g1}|{r1}|{g2}|{r2}", f"{n}|{g1}|{r1}", f"{n}|{g1}", f"{n}")


def rows_of_chart(hit_objects: Iterable) -> List[Tuple[float, int]]:
    """(time ms, mask) of every row of a chart; long notes count by their heads."""
    by: Dict[int, int] = {}
    for o in hit_objects:
        if 0 <= o.column < COLUMNS:
            t = int(round(o.time))
            by[t] = by.get(t, 0) | (1 << o.column)
    return sorted(by.items())


def row_contexts(rows: Sequence[Tuple[float, int]]) -> Iterable[Tuple[Tuple[str, str, str, str], int]]:
    """Every row's context keys and mask."""
    r1 = r2 = NONE
    t1 = t2 = None
    for t, m in rows:
        g1 = gap_bucket(t - t1) if t1 is not None else PAUSE
        g2 = gap_bucket(t1 - t2) if t2 is not None else PAUSE
        yield contexts(popcount(m), g1, r1, g2, r2), m
        r2, r1 = r1, m
        t2, t1 = t1, t


class PatternModel:
    """Counts per context level: {context key: {mask: count}}."""

    def __init__(self, levels: Optional[List[Dict[str, Dict[int, int]]]] = None):
        self.levels: List[Dict[str, Dict[int, int]]] = levels or [{} for _ in range(4)]
        self._totals: List[Dict[str, Tuple[int, int]]] = [{} for _ in range(4)]

    # -- training -----------------------------------------------------------------------------------
    def add_chart(self, rows: Sequence[Tuple[float, int]], mirrored: bool = True) -> None:
        variants = [rows, [(t, mirror(m)) for t, m in rows]] if mirrored else [rows]
        for rs in variants:
            for keys, m in row_contexts(rs):
                for level, key in zip(self.levels, keys):
                    d = level.setdefault(key, {})
                    d[m] = d.get(m, 0) + 1
        self._totals = [{} for _ in range(4)]

    def prune(self, min_context: int = 4) -> None:
        """Drop the specific contexts seen too rarely to say anything (the back-off covers them)."""
        for level in self.levels[:2]:
            for key in [k for k, d in level.items() if sum(d.values()) < min_context]:
                del level[key]
        self._totals = [{} for _ in range(4)]

    # -- persistence --------------------------------------------------------------------------------
    def save(self, path: Path = MODEL_PATH, meta: Optional[dict] = None) -> None:
        doc = {"version": 1, "gap_edges_ms": list(GAP_EDGES_MS), "meta": meta or {},
               "levels": [{k: {str(m): c for m, c in d.items()} for k, d in level.items()} for level in self.levels]}
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"), sort_keys=True)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "PatternModel":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            doc = json.load(f)
        if tuple(doc["gap_edges_ms"]) != GAP_EDGES_MS:
            raise ValueError("pattern model was trained with other gap buckets; retrain it")
        levels = [{k: {int(m): c for m, c in d.items()} for k, d in level.items()} for level in doc["levels"]]
        model = cls(levels)
        model.meta = doc.get("meta", {})
        return model

    # -- probabilities ------------------------------------------------------------------------------
    def _total(self, level: int, key: str) -> Tuple[int, int]:
        cache = self._totals[level]
        if key not in cache:
            d = self.levels[level].get(key)
            cache[key] = (sum(d.values()), len(d)) if d else (0, 0)
        return cache[key]

    def distribution(self, keys: Tuple[str, str, str, str], n: int) -> Tuple[Tuple[int, ...], np.ndarray]:
        """The masks of size `n` and their probabilities in the given context (Witten–Bell)."""
        masks = MASKS_BY_SIZE[n]
        p = np.full(len(masks), 1.0 / len(masks))
        for level in (3, 2, 1, 0):
            key = keys[level]
            total, types = self._total(level, key)
            if total == 0:
                continue
            d = self.levels[level][key]
            counts = np.array([d.get(m, 0) for m in masks], dtype=float)
            p = (counts + types * p) / (total + types)
        return masks, p / p.sum()

    def log_prob(self, rows: Sequence[Tuple[float, int]]) -> float:
        """Total natural-log probability of a chart's masks given its rhythm."""
        lp = 0.0
        for keys, m in row_contexts(rows):
            masks, p = self.distribution(keys, popcount(m))
            lp += float(np.log(p[masks.index(m)]))
        return lp


@lru_cache(maxsize=1)
def default_model() -> PatternModel:
    return PatternModel.load(MODEL_PATH)


#: Mappers repeat themselves: when a stretch of rhythm (4, 2 or 1 beats) is the same as one a few
#: stretches back, ranked charts keep its keys as they were about 7 % of the time and mirror them
#: about 9 % (measured on held-out charts; a model drawing row by row does either about 1 %). Tried
#: from the longest span down, each the chance per matching stretch: (span beats, same, mirrored).
#: A copied 4-beat stretch is also two copied 2-beat ones, hence the smaller chances further down;
#: they are set so that all three spans land on the measured rates.
REPEAT_SPANS = ((4, 0.09, 0.13), (2, 0.04, 0.05), (1, 0.02, 0.04))
REPEAT_BACK = (1, 2, 4, 8)
#: Rows this close to a beat boundary belong to the next beat (a chart's rows sit a ms or two early).
_BEAT_EPS = 0.02


@dataclass
class PatternSampler:
    """Draws the masks of a rhythm from the model, row by row, repeating earlier stretches as mappers do."""
    model: PatternModel
    temperature: float = 1.0
    #: A column is not hit again sooner than this (ms); 0 leaves it to the model.
    min_jack_ms: float = 0.0
    repeat: bool = True

    def sample(self, rows: Sequence[Tuple[float, int]], rng: np.random.Generator,
               beats: Optional[Sequence[float]] = None, draws: Optional[Sequence[float]] = None,
               styles: Optional[Sequence[str]] = None) -> List[int]:
        """Masks for rows given as (time ms, size). `beats` places each row on the beat grid (in beats from
        the first beat); without it no stretch is repeated.

        `draws` gives each row its own uniform number for its draw, and with `beats` the repeat choices
        are drawn per stretch from `rng`'s seed and the stretch's number: a row added or removed elsewhere
        then leaves the other rows' draws alone. The generator relies on that, so that its star rating
        moves smoothly with the intensity it bisects.

        `styles` names each row's section style (`STYLES`), which leans the model toward one kind of
        pattern without leaving what mappers write."""
        stretch_seed = int(rng.integers(1 << 31))
        n_rows = len(rows)
        out: List[int] = [0] * n_rows
        windows = _windows(beats) if (self.repeat and beats is not None and n_rows) else {}
        planned: Dict[int, int] = {}          # row index → mask copied from an earlier stretch
        last_hit = np.full(COLUMNS, -1e9)
        r1 = r2 = NONE
        t1 = t2 = None
        for i, (t, n) in enumerate(rows):
            if windows:
                for span, _p_same, _p_mirror in REPEAT_SPANS:
                    w = windows[span].get(i)
                    if w is not None:
                        self._maybe_repeat(rows, out, windows[span], w, span, stretch_seed, planned)
            n = int(min(max(n, 1), COLUMNS))
            g1 = gap_bucket(t - t1) if t1 is not None else PAUSE
            g2 = gap_bucket(t1 - t2) if t2 is not None else PAUSE
            m = planned.pop(i, None)
            if m is not None and self.min_jack_ms > 0 and any(
                    m >> c & 1 and t - last_hit[c] < self.min_jack_ms for c in range(COLUMNS)):
                m = None
            if m is None:
                u = float(draws[i]) if draws is not None else float(rng.random())
                style = styles[i] if styles is not None else "plain"
                m = self._draw(contexts(n, g1, r1, g2, r2), n, t, last_hit, u, style, r1, r2)
            out[i] = m
            for c in range(COLUMNS):
                if m >> c & 1:
                    last_hit[c] = t
            r2, r1 = r1, m
            t2, t1 = t1, t
        return out

    def _draw(self, keys, n: int, t: float, last_hit: np.ndarray, u: float,
              style: str = "plain", r1: int = 0, r2: int = 0) -> int:
        masks, p = self.model.distribution(keys, n)
        lean = STYLES.get(style)
        if lean and r1:
            p = p * np.exp(np.array([_lean(mk, r1, r2, lean) for mk in masks]))
        if self.min_jack_ms > 0:
            blocked = 0
            for c in range(COLUMNS):
                if t - last_hit[c] < self.min_jack_ms:
                    blocked |= 1 << c
            if blocked:
                ok = np.array([(mk & blocked) == 0 for mk in masks])
                if ok.any():
                    p = np.where(ok, p, 0.0)
        if self.temperature != 1.0:
            p = np.power(p, 1.0 / max(self.temperature, 1e-3))
        cdf = np.cumsum(p / p.sum())
        return masks[min(int(np.searchsorted(cdf, u, "right")), len(masks) - 1)]

    def _maybe_repeat(self, rows, out, by_start, w, span, stretch_seed, planned) -> None:
        """At the first row of stretch `w`: perhaps copy (or mirror) an earlier stretch with the same rhythm."""
        k, idx = w
        if any(i in planned for i in idx):
            return
        rhythm = [(rel, rows[i][1]) for rel, i in idx]
        for back in REPEAT_BACK:
            prev = by_start.get(("k", k - back))
            if prev is None:
                continue
            if [(rel, rows[i][1]) for rel, i in prev] != rhythm:
                continue
            _span, p_same, p_mirror = next(s for s in REPEAT_SPANS if s[0] == span)
            u = np.random.default_rng([stretch_seed, span, k + (1 << 20)]).random()
            if u < p_same:
                for (_, i), (_, j) in zip(idx, prev):
                    planned[i] = out[j]
            elif u < p_same + p_mirror:
                for (_, i), (_, j) in zip(idx, prev):
                    planned[i] = mirror(out[j])
            return


#: Section styles (ADR-0031): how strongly each leans toward a kind of pattern, as log-weights on the
#: model's probabilities. "flow": stairs (a single note one column on from the last, in the same
#: direction); "jack": rows that hit a column of the previous row (jacks, chordjacks, anchors);
#: "trill": a row that repeats the one before last (trills, jumptrills); "plain": the model as it is.
STYLES: Dict[str, Dict[str, float]] = {
    "plain": {},
    "flow": {"roll": 1.2},
    "jack": {"jack": 1.0},
    "trill": {"trill": 0.8},
}


def _lean(mask: int, r1: int, r2: int, lean: Dict[str, float]) -> float:
    w = 0.0
    if "jack" in lean and mask & r1:
        w += lean["jack"]
    if "trill" in lean and r2 and mask == r2 and mask != r1:
        w += lean["trill"]
    if "roll" in lean and popcount(mask) == 1 and popcount(r1) == 1:
        a, b = r1.bit_length() - 1, mask.bit_length() - 1
        step = (a - (r2.bit_length() - 1)) if popcount(r2) == 1 else 0
        if abs(b - a) == 1 and (step == 0 or b - a == step):
            w += lean["roll"]
    return w


def _windows(beats: Sequence[float]) -> Dict[int, Dict]:
    """For each span: {first row index of a stretch: (stretch number, [(position in 48ths of a beat, row)])},
    plus ("k", stretch number) → the same list, for looking back."""
    out: Dict[int, Dict] = {}
    for span, _s, _m in REPEAT_SPANS:
        groups: Dict[int, List[Tuple[int, int]]] = {}
        for i, b in enumerate(beats):
            k = int(np.floor((b + _BEAT_EPS) / span))
            groups.setdefault(k, []).append((int(round((b - k * span) * 48)), i))
        table: Dict = {}
        for k, idx in groups.items():
            if len(idx) >= 2:
                table[idx[0][1]] = (k, idx)
                table[("k", k)] = idx
        out[span] = table
    return out


def mask_columns(mask: int) -> List[int]:
    return [c for c in range(COLUMNS) if mask >> c & 1]


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
    - `quarter`, `triplet`: the share of rows on a quarter (1/16 note) or third of a beat.
    """
    out = {"roll": 0.0, "trill": 0.0, "jack_half": 0.0, "key_repeat": 0.0, "rhythm_repeat": 0.0,
           "density_cv": 0.0, "quarter": 0.0, "triplet": 0.0}
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
    frac = t_rel % 1.0
    near = lambda f: np.minimum(np.abs(frac - f), 1 - np.abs(frac - f)) < 0.03  # noqa: E731
    out.update(density_cv=float(counts.std() / counts.mean()) if len(counts) > 1 else 0.0,
               quarter=float(np.mean(near(0.25) | near(0.75))), triplet=float(np.mean(near(1 / 3) | near(2 / 3))))
    out.update(roll=rolls / tri if tri else 0.0, trill=trills / tri if tri else 0.0,
               jack_half=jacks / close if close else 0.0, key_repeat=kept / matched if matched else 0.0,
               rhythm_repeat=bars / n_bars if n_bars else 0.0)
    return out
