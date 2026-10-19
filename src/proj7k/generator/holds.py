"""
Long notes: which notes are held and for how long, learned from charts people wrote (ADR-0033).

Once the rows are placed, each note is offered to a second small network (`hold_model.json.gz`, the same
kind as the rhythm model) that reads what the audio does from the row on (does the sound ring or stop,
when the next attacks come), where the chart's next rows are, the row's size, and the chart's share of
long notes, and gives the probability that the note is a tap or a hold of about 1/4, 1/2, 1, 2 or 4+
beats (`LENGTH_EDGES`). The generator scales the hold probabilities so each section gets the share of
long notes it asks for, draws the length class, and puts the tail on the strongest onset of the lead
layer within that class (a tail released on the next hit or where the sound stops), snapped to the
places of the timing. A tail never runs into the next note of its column.

Three kinds of long-note writing (`KINDS`):

* ``general``: a row's notes share one length, the classic LN chart.
* ``release``: each note draws its own length, so tails within a beat are ragged (`ln_release`).
* ``inverse``: nearly every column held, released just before its next note (`ln_inverse`): every note of
  the section becomes a hold up to `INVERSE_GAP` of a beat before the next note in its column.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.ndimage import maximum_filter1d

from proj7k.generator.onset import FRAME_S, ONSET_LAG_S, OnsetEnvelope
from proj7k.generator.rhythm import RhythmModel, SLOTS, Places

#: Length classes in beats: tap, then holds up to each edge, and longer.
LENGTH_EDGES = (0.375, 0.75, 1.5, 3.0)
NOMINAL_BEATS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)
CLASSES = len(NOMINAL_BEATS)
KINDS = ("general", "release", "inverse")
#: An inverse hold ends this much of a beat before the next note in its column.
INVERSE_GAP = 0.25
#: The shortest gap left between a tail and the next head in its column, in beats.
MIN_GAP = 0.125
#: A hold shorter than this (beats) after trimming becomes a tap.
MIN_HOLD = 0.125
MODEL_PATH = Path(__file__).with_name("hold_model.json.gz")
#: How far ahead the audio is read, in beats.
AHEAD = (0.25, 0.5, 1.0, 2.0, 4.0)


def length_class(length_beats: float) -> int:
    if length_beats <= 0:
        return 0
    return 1 + int(np.searchsorted(LENGTH_EDGES, length_beats, side="left"))


def n_features(n_rhythm: int) -> int:
    return n_rhythm + 2 + 1 + len(AHEAD) * 2 + 1


def features(X_rows: np.ndarray, row_times: np.ndarray, row_sizes: np.ndarray, beat_ms: np.ndarray,
             env: OnsetEnvelope, shift_ms: float, ln_ratio) -> np.ndarray:
    """[rows, n_features] float32: the rhythm features of each row's place, the next two row gaps (beats),
    the row's size, how loud the audio stays and how strong its attacks are over the next `AHEAD` beats
    (relative to now), and the chart's (or section's) share of long notes."""
    frames_t = np.arange(env.n) * FRAME_S - ONSET_LAG_S
    loud = np.clip(env.loudness_db, -60.0, 0.0) / 60.0 + 1.0
    total = maximum_filter1d(env.total, size=3)

    def at(sig, t_ms):
        return np.interp((t_ms + shift_ms) / 1000.0, frames_t, sig, left=0.0, right=0.0)

    t = np.asarray(row_times, dtype=np.float64)
    L = np.asarray(beat_ms, dtype=np.float64)
    nxt = np.append(t[1:], t[-1] + 8 * L[-1]) if len(t) else t
    nxt2 = np.append(t[2:], [t[-1] + 8 * L[-1]] * min(2, len(t))) if len(t) else t
    cols = [np.clip((nxt - t) / L, 0, 8) / 8.0, np.clip((nxt2 - t) / L, 0, 8) / 8.0,
            np.asarray(row_sizes, dtype=np.float64) / 7.0]
    now = at(loud, t)
    for a in AHEAD:
        cols.append(at(loud, t + a * L) - now)
    for a in AHEAD:
        # The strongest attack in (t + 1/8 beat, t + a beats].
        k = np.linspace(0.125, a, 8)
        cols.append(np.max([at(total, t + kk * L) for kk in k], axis=0))
    cols.append(np.broadcast_to(np.asarray(ln_ratio, dtype=np.float64), t.shape))
    return np.column_stack([X_rows] + cols).astype(np.float32)


@lru_cache(maxsize=1)
def default_model() -> Optional[RhythmModel]:
    return RhythmModel.load(MODEL_PATH) if MODEL_PATH.is_file() else None


@dataclass(frozen=True)
class SectionHolds:
    first_bar: int
    last_bar: int
    ratio: float          # share of the section's notes that are long notes
    kind: str = "general"


def plan(sections: Sequence, ln_ratio: float, sustain: Dict[int, float],
         override: Optional[Dict[int, Tuple[float, str]]] = None) -> List[SectionHolds]:
    """Each section's share and kind of long notes. `override` (first bar -> (ratio, kind), from a design
    brief) wins; otherwise the chart's `ln_ratio` is spread by how much each section's sound rings on
    (`sustain`, 0..1 by rank): the most sustained sections get up to 1.5 times the share, the driest half."""
    override = override or {}
    out = []
    for s in sections:
        if s.first_bar in override:
            r, kind = override[s.first_bar]
        else:
            r, kind = ln_ratio * (0.5 + sustain.get(s.first_bar, 0.5)), "general"
        out.append(SectionHolds(s.first_bar, s.last_bar, float(np.clip(r, 0.0, 1.0)), kind))
    return out


def sustain_by_section(env: OnsetEnvelope, sections: Sequence, bar_span: Dict[int, Tuple[float, float]]) -> Dict[int, float]:
    """How much each section's sound rings on between attacks, ranked over the song to 0..1: the share of
    its frames within 12 dB of its loud level that carry no attack."""
    raw = {}
    for s in sections:
        if s.first_bar not in bar_span:
            continue
        a = max(0, int(bar_span[s.first_bar][0] / 1000.0 / FRAME_S))
        b = min(env.n, int(bar_span.get(s.last_bar, bar_span[s.first_bar])[1] / 1000.0 / FRAME_S) + 1)
        if b - a < 10:
            continue
        db = env.loudness_db[a:b]
        ringing = (db > np.percentile(db, 90) - 12.0) & (env.total[a:b] < 0.15)
        raw[s.first_bar] = float(ringing.mean())
    if not raw:
        return {}
    keys = sorted(raw, key=raw.get)
    return {k: (i + 0.5) / len(keys) for i, k in enumerate(keys)}


def assign(probs: np.ndarray, note_section: np.ndarray, ratios: np.ndarray, kinds: Sequence[str],
           u: np.ndarray, v_note: np.ndarray, v_row: np.ndarray, row_of: np.ndarray) -> np.ndarray:
    """Length class per note (0: tap). Each section's hold probabilities are scaled so its expected share of
    long notes is its ratio; `u` decides hold or tap, and the length is drawn with the row's `v_row` in a
    ``general`` section (one length per row) or the note's own `v_note` in a ``release`` one."""
    p_hold = 1.0 - probs[:, 0]
    out = np.zeros(len(probs), dtype=np.int64)
    cond = probs[:, 1:] / np.maximum(probs[:, 1:].sum(axis=1, keepdims=True), 1e-9)
    cum = np.cumsum(cond, axis=1)[:, :-1]
    for s in np.unique(note_section):
        m = note_section == s
        if s < 0 or ratios[s] <= 0 or kinds[s] == "inverse":
            continue
        p = _scaled(p_hold[m], ratios[s] * m.sum())
        hold = u[m] < p
        vv = v_note[m] if kinds[s] == "release" else v_row[row_of[m]]
        cls = 1 + (vv[:, None] > cum[m]).sum(axis=1)
        out[np.flatnonzero(m)] = np.where(hold, cls, 0)
    return out


def _scaled(p: np.ndarray, target: float) -> np.ndarray:
    if target <= 0 or p.sum() <= 0:
        return np.zeros_like(p)
    lo, hi = 0.0, 1.0
    while np.minimum(1.0, hi * p).sum() < target and hi < 1e9:
        hi *= 2.0
    for _ in range(40):
        c = (lo + hi) / 2.0
        if np.minimum(1.0, c * p).sum() < target:
            lo = c
        else:
            hi = c
    return np.minimum(1.0, hi * p)


def tail_times(heads_ms: np.ndarray, classes: np.ndarray, beat_ms: np.ndarray, pl: Places,
               strength: np.ndarray) -> np.ndarray:
    """A tail for every hold (NaN for taps): among the places between 0.7 and 1.4 times the class's nominal
    length after the head, the one with the strongest onset (`strength`, per place, from the lead layer);
    on a 1/4-beat place unless the class is the shortest."""
    out = np.full(len(heads_ms), np.nan)
    quarter = np.isin(np.array(SLOTS)[pl.slot], (0, 12, 24, 36))
    for i in np.flatnonzero(classes > 0):
        nominal = NOMINAL_BEATS[classes[i]] * beat_ms[i]
        lo, hi = heads_ms[i] + 0.7 * nominal, heads_ms[i] + 1.4 * nominal
        a, b = np.searchsorted(pl.time_ms, [lo, hi])
        cand = np.arange(a, b)
        if classes[i] > 1:
            cand = cand[quarter[cand]]
        if len(cand):
            j = cand[np.argmax(strength[cand] - 1e-3 * np.abs(pl.time_ms[cand] - heads_ms[i] - nominal) / beat_ms[i])]
            out[i] = pl.time_ms[j]
        else:
            out[i] = heads_ms[i] + nominal
    return out


def settle(columns: np.ndarray, heads: np.ndarray, tails: np.ndarray, beat_ms: np.ndarray,
           inverse: np.ndarray) -> np.ndarray:
    """Tails made playable: an inverse note holds until `INVERSE_GAP` before the next head in its column;
    any tail stops `MIN_GAP` before that head; a hold left shorter than `MIN_HOLD` becomes a tap (NaN)."""
    out = tails.copy()
    order = np.lexsort((heads, columns))
    for k, i in enumerate(order):
        nxt = order[k + 1] if k + 1 < len(order) and columns[order[k + 1]] == columns[i] else None
        gap_to = heads[nxt] if nxt is not None else np.inf
        if inverse[i]:
            out[i] = gap_to - INVERSE_GAP * beat_ms[i] if np.isfinite(gap_to) else heads[i] + 2 * beat_ms[i]
        if np.isnan(out[i]):
            continue
        out[i] = min(out[i], gap_to - MIN_GAP * beat_ms[i])
        if out[i] - heads[i] < MIN_HOLD * beat_ms[i] - 1e-6:
            out[i] = np.nan
    return out
