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
