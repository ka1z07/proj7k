"""
Physical demand and the hand accumulator (spec §4, §6.1, §7) for one assignment of the thumb.

An event's instantaneous demand `v` comes from the interval since its finger (and its hand's other
fingers) last moved; each hand then accumulates `v ** gamma` with a `tau_h` decay, and the
event's difficulty reading is the square root of its hand's accumulator, `d = E^h ** (1 / gamma)`.
Nothing here depends on the loss threshold `theta`, so `d` is computed once per thumb.
"""

from dataclasses import dataclass

import numpy as np

from proj7k.engine.events import Events
from proj7k.engine.params import Params
from proj7k.engine.skills import hand_of, kappa_matrix


@dataclass(frozen=True)
class HandDemand:
    thumb: int
    v: np.ndarray   # instantaneous demand, Hz (§4.5)
    d: np.ndarray   # event difficulty reading (§7)
    r: np.ndarray   # same-finger share j / (j + x + o), the raw input of membership (§8.1)


def _psi(dt: np.ndarray, d0) -> np.ndarray:
    """§4.2; 0 where there is no earlier event (dt is inf)."""
    with np.errstate(invalid="ignore", over="ignore"):
        out = dt / (dt ** 2 + d0 ** 2)
    return np.where(np.isfinite(dt), out, 0.0)


def hand_demand(ev: Events, thumb: int, p: Params) -> HandDemand:
    hand = hand_of(thumb)
    K = kappa_matrix(p, thumb)
    row, col, rel, T = ev.row, ev.col, ev.rel, ev.T
    tr = T[row]

    # §4.2–4.3: only events strictly before the row count (spec v0.2)
    last = np.vstack([np.full((1, 7), -np.inf), ev.last[:-1]])
    Psi = _psi(T[:, None] - last, ev.delta_0[:, None])  # [rows, 7]
    x = (K[col] * Psi[row]).sum(1)
    last_hand = np.stack([last[:, hand == hh].max(1) for hh in (0, 1)], 1)
    o = p.k_cross * _psi(tr - last_hand[row, 1 - hand[col]], ev.delta_0[row])

    # §4.4–4.5
    c = 1.0 + p.chi_0 * (K[col] * ev.held[row]).sum(1)
    omega = np.where(rel, p.w_rel, 1.0)
    jxo = ev.j + x + o
    v = np.minimum(omega * jxo * c, p.v_cap)

    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(jxo > 0, ev.j / jxo, 0.0)

    return HandDemand(thumb=thumb, v=v, d=_accumulate(ev, hand, v, p) ** (1.0 / p.gamma), r=r)


def _accumulate(ev: Events, hand: np.ndarray, v: np.ndarray, p: Params) -> np.ndarray:
    """§6.1: per event, its hand's accumulator E^h after the event's row, in the units of v ** gamma."""
    vg = v ** p.gamma
    Eh, th = np.zeros(2), np.full(2, np.nan)
    E_h = np.zeros(ev.n)
    order = np.argsort(ev.row, kind="stable")
    bounds = np.searchsorted(ev.row[order], np.arange(ev.n_rows + 1))
    for r in range(ev.n_rows):
        evs = order[bounds[r]:bounds[r + 1]]
        Tr = ev.T[r]
        hand_in, hit = [0.0, 0.0], [False, False]
        for i in evs:
            hh = hand[ev.col[i]]
            hand_in[hh] += vg[i]
            hit[hh] = True
        for hh in (0, 1):
            if hit[hh]:
                # R10: a hand's first update has dt = inf (phi = 0); the chart's time origin is arbitrary
                phi = 0.0 if np.isnan(th[hh]) else np.exp(-(Tr - th[hh]) / p.tau_h)
                Eh[hh] = Eh[hh] * phi + (1 - phi) * hand_in[hh]
                th[hh] = Tr
        for i in evs:
            E_h[i] = Eh[hand[ev.col[i]]]
    return E_h
