"""
Where the rows go: a rhythm model learned from charts people wrote to the same audio (ADR-0032).

Every beat of the chart's timing is offered at twelve places, the union of the 1/8 and the 1/6 grid
(`SLOTS`, in 48ths of a beat), which between them hold the 1/1, 1/2, 1/4, 1/3, 1/6 and 1/8 snaps
mappers use for nearly every row. Each place is described by what the audio does there and around it
(`features`): the onset strength in three bands at the place and at fractions of a beat before and
after it, how loud and how busy the music is over a beat, two measures and the song, the place in the
measure, the tempo, and one number that is not audio: how many rows per second the chart has
(`density`). A small network (`RhythmModel`) gives, for each place, the probability of no row and of
a row of 1, 2, 3 or 4+ notes. Trained on people's charts with their audio, it puts rows where mappers
put them for a chart of that density: a 1/8 burst on a drum fill, a rest where the music breaks, single
notes on a melody and chords on the hits. The generator sets the density (closed loop on the star
rating) and draws the rows.
"""

import gzip
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.ndimage import maximum_filter1d, uniform_filter1d

from proj7k.generator.onset import FRAME_S, ONSET_LAG_S, OnsetEnvelope
from proj7k.parser import TimingPoint

#: Places in a beat, in 48ths: the 1/8 grid (multiples of 6) and the 1/6 grid (multiples of 8).
SLOTS = (0, 6, 8, 12, 16, 18, 24, 30, 32, 36, 40, 42)
#: Context: onset strength this many 24ths of a beat before and after the place.
CONTEXT_24THS = (-6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, -24, -48, 24, 48)
#: A chart's row belongs to a place when it is this close (ms); rows elsewhere (1/12, 1/16, unsnapped)
#: are left out of training.
MATCH_MS = 4.0
#: Output classes: no row, a row of 1, 2, 3, 4 or more notes.
CLASSES = 5
MODEL_PATH = Path(__file__).with_name("rhythm_model.json.gz")


@dataclass
class Places:
    time_ms: np.ndarray      # [n]
    beat_ms: np.ndarray      # [n] beat length of the timing section
    slot: np.ndarray         # [n] index into SLOTS
    beat: np.ndarray         # [n] beat number, counted across timing sections
    bar: np.ndarray          # [n] measure number
    beat_in_bar: np.ndarray  # [n]

    def __len__(self) -> int:
        return len(self.time_ms)


def places(timing: Sequence[TimingPoint], end_ms: float) -> Places:
    """Every place of every beat of the timing, from the song's start (the first section extended back,
    as osu! does) to `end_ms`, in time order."""
    sections = sorted((tp for tp in timing if tp.uninherited and tp.beat_length > 0), key=lambda tp: tp.time)
    if not sections:
        raise ValueError("没有可用的 timing（缺少非继承 timing point）")
    cols: List[list] = [[], [], [], [], [], []]
    beat0 = bar0 = 0
    frac = np.array(SLOTS) / 48.0
    for i, tp in enumerate(sections):
        L = tp.beat_length
        meter = tp.meter if tp.meter and tp.meter > 0 else 4
        sec_end = sections[i + 1].time if i + 1 < len(sections) else end_ms
        k0 = -int(np.floor(tp.time / L)) if i == 0 else 0
        n_beats = int(np.ceil((sec_end - tp.time) / L - 1e-9)) - k0
        for j in range(max(n_beats, 0)):
            k = k0 + j
            tb = tp.time + k * L
            t = tb + frac * L
            ok = (t >= 0) & (t < sec_end - 1e-6)
            if not ok.any():
                continue
            m = int(ok.sum())
            cols[0].extend(t[ok])
            cols[1].extend([L] * m)
            cols[2].extend(np.flatnonzero(ok))
            cols[3].extend([beat0 + j] * m)
            cols[4].extend([bar0 + j // meter] * m)
            cols[5].extend([j % meter] * m)
        beat0 += max(n_beats, 0)
        beat0 = -(-beat0 // 4) * 4
        bar0 += -(-max(n_beats, 0) // meter)
    arr = [np.asarray(c) for c in cols]
    order = np.argsort(arr[0], kind="stable")
    return Places(arr[0][order].astype(np.float64), arr[1][order].astype(np.float64), arr[2][order].astype(np.int64),
                  arr[3][order].astype(np.int64), arr[4][order].astype(np.int64), arr[5][order].astype(np.int64))


def n_features() -> int:
    return 4 + len(CONTEXT_24THS) + 1 + 5 + 2 + len(SLOTS) + 2 + 1 + 1


def features(env: OnsetEnvelope, pl: Places, shift_ms: float, density: float = 0.0) -> np.ndarray:
    """[n, n_features()] float32: what the audio does at and around each place (read `shift_ms` later, where
    the chart's timing says the music is), plus the chart density (log2 rows per second)."""
    frames_t = np.arange(env.n) * FRAME_S - ONSET_LAG_S
    bands = maximum_filter1d(env.bands, size=3, axis=1)
    total = maximum_filter1d(env.total, size=3)

    def at(sig: np.ndarray, t_ms: np.ndarray) -> np.ndarray:
        return np.interp((t_ms + shift_ms) / 1000.0, frames_t, sig, left=0.0, right=0.0)

    t, L = pl.time_ms, pl.beat_ms
    cols = [at(bands[0], t), at(bands[1], t), at(bands[2], t), at(total, t)]
    cols += [at(total, t + k * L / 24.0) for k in CONTEXT_24THS]
    cols.append(at(env.brightness, t))
    loud = np.clip(env.loudness_db, -60.0, 0.0) / 60.0 + 1.0
    beat_frames = max(1, int(round(float(np.median(L)) / 1000.0 / FRAME_S)))
    for size in (1, 8):
        cols.append(at(uniform_filter1d(loud, size=beat_frames * size), t))
    cols.append(at(loud, t))
    song_loud = uniform_filter1d(loud, size=beat_frames * 8)
    lz = (song_loud - song_loud.mean()) / (song_loud.std() + 1e-6)
    cols.append(np.clip(at(lz, t), -3, 3) / 3.0)
    cols.append(np.full(len(t), float(np.percentile(loud, 50))))
    for size in (2, 8):
        cols.append(at(uniform_filter1d(env.total, size=beat_frames * size), t))
    onehot = np.zeros((len(t), len(SLOTS)), dtype=np.float32)
    onehot[np.arange(len(t)), pl.slot] = 1.0
    downbeat = ((pl.slot == 0) & (pl.beat_in_bar == 0)).astype(np.float32)
    odd_beat = (pl.beat_in_bar % 2).astype(np.float32)
    tempo = L / 500.0
    dens = np.full(len(t), float(density))
    X = np.column_stack(cols + [onehot, downbeat, odd_beat, tempo, dens]).astype(np.float32)
    return X


def labels(pl: Places, row_times_ms: Sequence[float], row_sizes: Sequence[int]) -> Tuple[np.ndarray, int]:
    """Class per place (0 none, 1..4 notes), and how many rows matched no place."""
    y = np.zeros(len(pl), dtype=np.int8)
    if not len(row_times_ms):
        return y, 0
    rt = np.asarray(row_times_ms, dtype=np.float64)
    idx = np.clip(np.searchsorted(pl.time_ms, rt), 1, len(pl) - 1)
    left = pl.time_ms[idx - 1]
    right = pl.time_ms[idx]
    best = np.where(np.abs(rt - left) <= np.abs(rt - right), idx - 1, idx)
    ok = np.abs(pl.time_ms[best] - rt) <= MATCH_MS
    y[best[ok]] = np.minimum(np.asarray(row_sizes)[ok], 4)
    return y, int((~ok).sum())


def is_holdout(audio_name: str) -> bool:
    """One song in ten, by its audio file name, is never trained on (evaluation uses only these)."""
    return int(hashlib.md5(audio_name.encode("utf-8")).hexdigest(), 16) % 10 == 0


def chart_density(row_times_ms: Sequence[float]) -> float:
    """log2 rows per second over the charted span."""
    if len(row_times_ms) < 2:
        return 0.0
    span = (max(row_times_ms) - min(row_times_ms)) / 1000.0
    return float(np.log2(max(len(row_times_ms) / max(span, 1.0), 1e-3)))


class RhythmModel:
    """A two-hidden-layer network over standardized features, softmax over `CLASSES`."""

    def __init__(self, weights: dict):
        self.mean = np.asarray(weights["mean"], dtype=np.float32)
        self.std = np.asarray(weights["std"], dtype=np.float32)
        self.layers = [(np.asarray(W, dtype=np.float32), np.asarray(b, dtype=np.float32)) for W, b in weights["layers"]]
        self.meta = weights.get("meta", {})

    def predict(self, X: np.ndarray) -> np.ndarray:
        h = (X - self.mean) / self.std
        for i, (W, b) in enumerate(self.layers):
            h = h @ W + b
            if i < len(self.layers) - 1:
                h = np.maximum(h, 0.0)
        h = h - h.max(axis=1, keepdims=True)
        p = np.exp(h)
        return p / p.sum(axis=1, keepdims=True)

    def save(self, path: Path) -> None:
        data = {"mean": self.mean.tolist(), "std": self.std.tolist(),
                "layers": [[W.tolist(), b.tolist()] for W, b in self.layers], "meta": self.meta}
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(data, f)

    @classmethod
    def load(cls, path: Path) -> "RhythmModel":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return cls(json.load(f))


@lru_cache(maxsize=1)
def default_model() -> Optional[RhythmModel]:
    return RhythmModel.load(MODEL_PATH) if MODEL_PATH.is_file() else None


#: Feature columns the generator reads back.
F_LOW, F_MID, F_HIGH, F_TOTAL = 0, 1, 2, 3
F_BRIGHT = 4 + len(CONTEXT_24THS)
F_LOUD = F_BRIGHT + 3
#: The density knob's range, log2 rows per second (about 0.35 .. 28).
D_MIN, D_MAX = -1.5, 4.8
#: Past D_MAX the knob widens the chords instead (`pick`'s `chords`), up to this much further.
CHORD_RANGE, CHORD_GAIN = 1.5, 2.0
SHARPEN = 1.5
#: Share of the song's measures that keep their 1/8 and 1/6 places (see `pick`).
FINE_MEASURES = 0.3
#: ... or more, so the places that can take a row number this many times the rows asked for.
ROOM = 2.0
#: A beat goes 1/6 only when the model expects at least this many rows on its 1/6-only places.
TRIPLET_MIN = 0.3
#: How much of the model's swing in density from one stretch of `STRETCH_BARS` measures to the next
#: the chart keeps (see `pick`): 1 keeps it all, 0 makes every stretch that sounds equally dense.
CONTRAST = 0.5
STRETCH_BARS = 4
_QUARTER = (0, 12, 24, 36)
_EIGHTH_ONLY = (6, 18, 30, 42)
_SIXTH_ONLY = (8, 16, 32, 40)


def grid_points(pl: Places, X: np.ndarray, silence_db: float):
    """The places as `grid.GridPoint`s (for phrases, sections and keys)."""
    from proj7k.generator.grid import BEAT, DOWNBEAT, HALF, SUB, GridPoint

    out = []
    loud_db = (X[:, F_LOUD] - 1.0) * 60.0
    for i in range(len(pl)):
        s = SLOTS[pl.slot[i]]
        level = (DOWNBEAT if pl.beat_in_bar[i] == 0 else BEAT) if s == 0 else HALF if s == 24 else SUB
        low, mid, high = float(X[i, F_LOW]), float(X[i, F_MID]), float(X[i, F_HIGH])
        out.append(GridPoint(time_ms=float(pl.time_ms[i]), level=level, strength=float(X[i, F_TOTAL]),
                             low=low, mid=mid, high=high, brightness=float(X[i, F_BRIGHT]),
                             silent=bool(loud_db[i] < silence_db), beat=float(pl.beat[i] + s / 48.0),
                             bar=int(pl.bar[i]), slot=int(pl.beat_in_bar[i] * 48 + s)))
    return out


def pick(probs: np.ndarray, pl: Places, u: np.ndarray, v: np.ndarray, silent: np.ndarray,
         rows_per_s: Optional[float] = None, chords: float = 0.0, gain: Optional[np.ndarray] = None,
         section: Optional[np.ndarray] = None, contrast: float = CONTRAST) -> np.ndarray:
    """Chord size per place (0: no row), drawn from the model's probabilities with fixed uniforms `u`
    (row or not) and `v` (its size), so a higher density only adds rows.

    The model rates every place on its own, so it spreads its expectation of 1/8 and 1/6 rows thinly
    over the whole song, where a mapper writes them in bursts: a measure of 1/8, a triplet fill, then
    back to 1/4. So the finer places are gathered: the measures where the model expects the most of
    them relative to their 1/4 grid (`FINE_MEASURES` of the song's measures) keep them, multiplied so
    the song keeps its expected number, and the rest lose them. In those measures each beat goes
    either 1/8 or 1/6, whichever the model expects more rows on (1/6 only when it expects at least
    `TRIPLET_MIN` of them; a 1/6 beat also drops its 1/4s).
    With `rows_per_s`, the row probabilities are then scaled together so the chart has that many rows
    per second (the model alone undershoots dense charts: it cannot know which song is a stream chart).
    `chords` > 0 moves every row's size draw toward wider chords, for targets the density alone cannot
    reach. `gain` (per place) is a design brief's density for each section relative to the others. With
    `section` (per place, which brief section) and `rows_per_s`, each section gets its own share of the
    rows: its sounding time times its gain. Scaling the model's probabilities alone could not do that: a
    section the model rates sparse stays sparse under any uniform gain, and the song-wide rescale hands
    its rows to the other sections (ADR-0033). The gain also ranks the measures that keep their 1/8 and
    1/6 places, so a dense section gets them first.
    Last, the rows are moved between stretches of `STRETCH_BARS` measures so each stretch's density is
    its density so far raised to `contrast` (the song's rows kept): the model follows the onsets, and a
    quiet verse read that way gets a few notes a measure while the star rating is set by the chorus, so
    the chart swings from trivial to hard and is sparse overall (ADR-0038). With a brief's sections the
    rows move only within a section: the brief's own contrast between sections stays as written."""
    p_row = np.where(silent, 0.0, 1.0 - probs[:, 0])
    by_section = gain is not None and section is not None and rows_per_s is not None
    if gain is not None and not by_section:
        p_row = np.minimum(1.0, p_row * gain)
    slot48 = np.array(SLOTS)[pl.slot]
    quarter = np.isin(slot48, _QUARTER)
    eighth = np.isin(slot48, _EIGHTH_ONLY)
    sixth = np.isin(slot48, _SIXTH_ONLY)
    fine = eighth | sixth
    if len(pl):
        bars = pl.bar - pl.bar.min()
        n_bars = int(bars.max()) + 1
        base = np.bincount(bars[quarter], weights=p_row[quarter], minlength=n_bars)
        fine_mass = np.bincount(bars[fine], weights=p_row[fine], minlength=n_bars)
        ratio = fine_mass / (base + 1e-6)
        if by_section:
            bar_gain = np.zeros(n_bars)
            np.maximum.at(bar_gain, bars, gain)
            ratio = ratio * bar_gain
        order = np.argsort(-ratio, kind="stable")
        k = max(1, int(round(FINE_MEASURES * n_bars)))
        if rows_per_s is not None:
            # A dense target needs room: more measures keep their fine places until the places that can
            # take a row hold the asked rows with some slack (a stream chart is 1/8 nearly throughout).
            span_s = max((pl.time_ms[-1] - pl.time_ms[0]) / 1000.0, 1.0)
            open_place = ~silent & ~fine
            fine_per_bar = np.bincount(bars[fine & ~silent], minlength=n_bars)
            room = open_place.sum() + np.cumsum(fine_per_bar[order])
            need = np.searchsorted(room, ROOM * rows_per_s * span_s) + 1
            k = min(n_bars, max(k, int(need)))
        keep_bar = np.zeros(n_bars, bool)
        keep_bar[order[:k]] = True
        kept = keep_bar[bars]
        fine_gain = fine_mass.sum() / max(fine_mass[keep_bar].sum(), 1e-9)
        beats = pl.beat - pl.beat.min()
        n_beats = int(beats.max()) + 1
        e8 = np.bincount(beats[eighth], weights=p_row[eighth], minlength=n_beats)
        e6 = np.bincount(beats[sixth], weights=p_row[sixth], minlength=n_beats)
        ternary = ((e6 > e8) & (e6 >= TRIPLET_MIN))[beats] & kept
        p_row = np.where(fine & kept, np.minimum(1.0, p_row * fine_gain), np.where(fine, 0.0, p_row))
        p_row = np.where((eighth | quarter) & ternary, 0.0, p_row)
        p_row = np.where(sixth & kept & ~ternary, 0.0, p_row)
    if rows_per_s is not None and len(pl) and p_row.sum() > 0:
        span_s = max((pl.time_ms[-1] - pl.time_ms[0]) / 1000.0, 1.0)
        if by_section:
            p_row = _scaled_by_section(p_row, rows_per_s * span_s, pl.time_ms, silent, gain, section)
        else:
            p_row = _scaled(p_row, rows_per_s * span_s)
    if contrast < 1.0 and len(pl) and p_row.sum() > 0:
        p_row = _evened(p_row, pl, silent, contrast, section if by_section else None)
    row = u < p_row
    if chords > 0:
        v = v ** (1.0 / (1.0 + CHORD_GAIN * chords))
    cond = probs[:, 1:] / np.maximum(probs[:, 1:].sum(axis=1, keepdims=True), 1e-9)
    size = 1 + (v[:, None] > np.cumsum(cond, axis=1)[:, :-1]).sum(axis=1)
    return np.where(row, size, 0)


def _scaled_by_section(p: np.ndarray, target: float, time_ms: np.ndarray, silent: np.ndarray,
                       gain: np.ndarray, section: np.ndarray) -> np.ndarray:
    """`_scaled` within each section, its target the song's `target` split by sounding time times gain."""
    step = np.diff(time_ms, append=time_ms[-1]) / 1000.0
    sounding = np.where(silent, 0.0, step)
    ids = np.unique(section)
    weight = {s: float(sounding[section == s].sum() * gain[section == s].max()) for s in ids}
    total = sum(weight.values())
    out = np.zeros_like(p)
    for s in ids:
        m = section == s
        if weight[s] > 0 and p[m].sum() > 0:
            out[m] = _scaled(p[m], target * weight[s] / total)
    return out


def _evened(p: np.ndarray, pl: Places, silent: np.ndarray, contrast: float,
            section: Optional[np.ndarray] = None) -> np.ndarray:
    """`p` with each stretch's rows per sounding second d set to c d^contrast, c keeping the total (of
    each `section`, when given)."""
    step = np.diff(pl.time_ms, append=pl.time_ms[-1]) / 1000.0
    sounding = np.where(silent, 0.0, step)
    stretch = (pl.bar - pl.bar.min()) // STRETCH_BARS
    part = np.zeros(len(p), int) if section is None else np.unique(section, return_inverse=True)[1]
    group = np.unique(part * (int(stretch.max()) + 1) + stretch, return_inverse=True)[1]
    n = int(group.max()) + 1
    mass = np.bincount(group, weights=p, minlength=n)
    secs = np.bincount(group, weights=sounding, minlength=n)
    part_of = np.zeros(n, int)
    part_of[group] = part
    live = (mass > 0) & (secs > 0)
    dens = np.where(live, mass / np.maximum(secs, 1e-9), 0.0)
    want = np.where(live, secs * dens ** contrast, 0.0)
    for q in np.unique(part_of):
        m = part_of == q
        if want[m].sum() > 0:
            want[m] *= mass[m].sum() / want[m].sum()
    out = p.copy()
    for k in np.flatnonzero(live):
        m = group == k
        out[m] = _scaled(p[m], want[k], sharpen=1.0)
    return out


def _scaled(p: np.ndarray, target: float, sharpen: float = SHARPEN) -> np.ndarray:
    """min(1, c p**sharpen) with c such that the expected count is `target` (or as close as the places
    allow)."""
    q = p ** sharpen
    lo, hi = 0.0, 1.0
    while np.minimum(1.0, hi * q).sum() < target and hi < 1e9:
        hi *= 2.0
    for _ in range(50):
        c = (lo + hi) / 2.0
        if np.minimum(1.0, c * q).sum() < target:
            lo = c
        else:
            hi = c
    return np.minimum(1.0, hi * q)
