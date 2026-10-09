"""
Short bursts and single-finger overload: what the engine's star smooths away and the downscaler must not leave.

The engine reads each event through its hand's accumulator with a `tau_h` = 4 s memory (§6.1), so the total
star is a reading of *sustained* load: a one-second burst moves it little, and a finger hammered while the rest
of its hand rests is averaged with the fingers that rest. On charts people map this is calibrated (the dan
ladder holds). The downscaler, though, deletes whatever lowers that reading most, and the cheapest way to lower
a 4 s average is to thin the stretches around a burst and leave the burst: measured on the 120 benchmark charts
taken down to 2nd, the busiest column ran a median 20% (up to 2x) faster than on real charts of that star,
and the hardest half second of a hand up to 1.5x harder.

Two readings, both from the engine's own quantities:

- `hand`: the engine's hand reading with a short memory (`HAND_TAU` instead of `tau_h`): the same `v ** gamma`
  accumulated over half a second. Its peak is how hard the chart's hardest burst is.
- `column`: per column, the same accumulator over the column's own presses, fed the same-finger rate `j`
  (§4.1) alone: how fast one finger is made to repeat, sustained over `COLUMN_TAU`.

Each has an envelope over the engine's level `D`: on the 120 benchmark charts `ln(peak) = a + b ln(D)` fits
the population, and the cap at a level is that line plus the residual's 90th percentile, i.e. as bursty as
nine real charts in ten at that level are at most. The downscaler keeps its result under the caps at the
target level (`pruner`), or says it could not.

`fit_envelopes` refits the constants from charts; `tests/test_downscaler_burst.py` holds them to the corpus.
"""

import dataclasses
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

import numpy as np

from proj7k.engine.demand import _accumulate
from proj7k.engine.skills import hand_of
from proj7k.field import ChartField

HAND_TAU = 0.5
COLUMN_TAU = 1.0

#: (a, b, margin) of each envelope: cap(D) = exp(a + b ln D + margin). Fitted by `fit_envelopes` on the 120
#: benchmark charts (`docs/research/structured_index.json`); margin is the residuals' 90th percentile.
ENVELOPES: Dict[str, Tuple[float, float, float]] = {
    "hand": (0.8124, 0.7480, 0.1172),
    "column": (0.5414, 0.4122, 0.2698),
}


@dataclass(frozen=True)
class BurstReading:
    """Per event, the short-memory readings; `column` is 0 on releases (a release does not repeat a finger)."""

    hand: np.ndarray
    column: np.ndarray

    def peaks(self) -> Dict[str, float]:
        return {"hand": float(self.hand.max(initial=0.0)), "column": float(self.column.max(initial=0.0))}


def burst_reading(field: ChartField) -> BurstReading:
    ev, p = field.events, field.params
    hand = _accumulate(ev, hand_of(field.thumb), field.v, dataclasses.replace(p, tau_h=HAND_TAU)) ** (1.0 / p.gamma)

    column = np.zeros(ev.n)
    jg = ev.j ** p.gamma
    for c in range(7):
        idx = np.flatnonzero((field.col == c) & ~field.release)
        E, last = 0.0, None
        for i in idx:
            phi = 0.0 if last is None else float(np.exp(-(field.t[i] - last) / COLUMN_TAU))
            E = E * phi + (1.0 - phi) * jg[i]
            last = field.t[i]
            column[i] = E
    return BurstReading(hand=hand, column=column ** (1.0 / p.gamma))


def cap(kind: str, D: float) -> float:
    """The envelope's cap on `kind`'s peak at level `D` (inf at D = 0: a chart with no level has no burst to bound)."""
    if D <= 0.0:
        return float("inf")
    a, b, m = ENVELOPES[kind]
    return float(np.exp(a + b * np.log(D) + m))


def excess(reading: BurstReading, D: float) -> Dict[str, float]:
    """Each peak over its cap at level `D`, as a ratio; at most 1.0 is within the envelope."""
    return {k: v / cap(k, D) for k, v in reading.peaks().items()}


def fit_envelopes(fields: Iterable[ChartField], quantile: float = 90.0) -> Dict[str, Tuple[float, float, float]]:
    """(a, b, margin) per reading, from charts' peaks against their level D."""
    lD, peaks = [], {"hand": [], "column": []}
    for f in fields:
        if f.total_D <= 0.0:
            continue
        lD.append(np.log(f.total_D))
        for k, v in burst_reading(f).peaks().items():
            peaks[k].append(np.log(v))
    x = np.array(lD)
    out = {}
    for k, y in peaks.items():
        y = np.array(y)
        b, a = np.polyfit(x, y, 1)
        out[k] = (float(a), float(b), float(np.percentile(y - (a + b * x), quantile)))
    return out
