"""
Each skill's own cost (spec §8, v0.2.3; ADR-0056).

v0.2 read every skill off the total's event demand `d`, with the skill's membership as the solve's
weights; since `d` is a hand-level, four-second reading, the eight weighted quantiles came out
within a few percent of each other and the skill stars were copies of the total. Here each skill k
has its own event cost `c_ik`, built from the parts of `v` and the event's properties, accumulated
on the same hand accumulator (§6.1) with its own memory, and solved with the same threshold (§3.2)
under unit weights. `D_k` then answers "how much does this chart require skill k", and reads 0 on
a chart with none of it.

The total `D` is untouched (ADR-0056 decision 1): nothing here feeds `evaluate._profile`'s total.
"""

from typing import Dict, Tuple

import numpy as np

from proj7k.engine.attribution import ln_context, smoothstep
from proj7k.engine.demand import HandDemand, accumulate
from proj7k.engine.events import Events
from proj7k.engine.params import Params
from proj7k.engine.skills import SKILLS
from proj7k.engine.solver import solve

#: The interval ratios a hand's consecutive press rows may keep without reading as irregular (§8.3).
CARRIED_RATIOS = np.array([1.0, 2.0, 0.5, 3.0, 1.0 / 3.0, 4.0, 0.25, 1.5, 2.0 / 3.0])
#: The gap classes of a window's press rows against the window's grid (§8.3 snap diversity): the dyadic snaps,
#: then the triplet, dotted and other off-grid ratios a tech chart mixes in.
GAP_CLASSES = np.array([1.0, 2.0, 0.5, 1.5, 1.0 / 3.0, 2.0 / 3.0, 0.75, 4.0 / 3.0, 4.0, 0.25])
#: A gap class holding at least this share of a window's gaps counts towards the window's diversity.
GAP_CLASS_SHARE = 0.1
#: Snap diversity is read over the press rows within this many seconds either side of the row.
DIVERSITY_WINDOW_S = 1.0


def snap_diversity(ev: Events, p: Params) -> np.ndarray:
    """
    §8.3: per event, how many gap classes (of `GAP_CLASSES`, within `tech_ratio_tol`; one more for off-grid)
    hold at least `GAP_CLASS_SHARE` of the press-row gaps within `DIVERSITY_WINDOW_S` of the event's row,
    measured against the window's median gap. A stream on one snap reads 1; a chart that mixes 1/4, 1/6 and
    1/8 within two seconds reads 3. The community's "irregular snapping and polyrhythm" is this number.
    """
    rows = np.unique(ev.row[~ev.rel])
    out = np.zeros(ev.n)
    if len(rows) < 3:
        return out
    t = ev.T[rows]
    gaps = np.diff(t)
    div = np.zeros(ev.n_rows)
    for k, r in enumerate(rows):
        lo = np.searchsorted(t, t[k] - DIVERSITY_WINDOW_S)
        hi = np.searchsorted(t, t[k] + DIVERSITY_WINDOW_S, side="right")
        g = gaps[max(lo, 0):max(hi - 1, 0)]
        g = g[g > 0]
        if len(g) < 4:
            continue
        ratio = g / np.median(g)
        near = np.abs(ratio[:, None] - GAP_CLASSES[None, :]) <= p.tech_ratio_tol * GAP_CLASSES[None, :]
        cls = np.where(near.any(1), near.argmax(1), -1)
        _, counts = np.unique(cls, return_counts=True)
        div[r] = np.sum(counts / len(cls) >= GAP_CLASS_SHARE)
    return div[ev.row]


def hand_irregular(ev: Events, hand: np.ndarray, p: Params) -> np.ndarray:
    """
    §8.3: 1 on a press whose hand's interval to its previous press row is not a carried ratio of the interval
    before that (within `tech_ratio_tol`), 0 otherwise and on releases. A hand that paused longer than
    `tech_max_gap` starts afresh. The row rhythm surprise (§5.2) sees both hands at once; this sees one hand's
    own line, which an alternating pattern can keep regular while the rows are not, and the reverse.
    """
    out = np.zeros(ev.n)
    press = ~ev.rel
    hc = hand[ev.col]
    for hh in (0, 1):
        mask = press & (hc == hh)
        rows = np.unique(ev.row[mask])
        if len(rows) < 3:
            continue
        gaps = np.diff(ev.T[rows])
        bad = np.zeros(len(rows), dtype=bool)
        g1, g0 = gaps[1:], gaps[:-1]
        ok = (g0 > 0) & (g1 > 0) & (g1 <= p.tech_max_gap)
        ratio = np.where(ok, g1 / np.where(g0 > 0, g0, 1.0), 1.0)
        near = np.abs(ratio[:, None] - CARRIED_RATIOS[None, :]) <= p.tech_ratio_tol * CARRIED_RATIOS[None, :]
        bad[2:] = ok & ~near.any(1)
        bad_of_row = np.zeros(ev.n_rows, dtype=bool)
        bad_of_row[rows] = bad
        out[mask] = bad_of_row[ev.row[mask]]
    return out


def run_since_rest(ev: Events, hand: np.ndarray, p: Params) -> np.ndarray:
    """§8.3: seconds each event's hand has been playing since its last gap longer than `stamina_rest`."""
    out = np.zeros(ev.n)
    hc = hand[ev.col]
    t_ev = ev.T[ev.row]
    order = np.argsort(ev.row, kind="stable")
    start = [np.nan, np.nan]
    last = [np.nan, np.nan]
    for i in order:
        hh = hc[i]
        t = t_ev[i]
        if np.isnan(last[hh]) or t - last[hh] > p.stamina_rest:
            start[hh] = t
        last[hh] = t
        out[i] = t - start[hh]
    return out


def skill_costs(ev: Events, dem: HandDemand, p: Params) -> Dict[str, np.ndarray]:
    """§8.3: the eight cost series, in SKILLS order, from the parts of v and the event's properties."""
    lam = ln_context(ev, p)
    rc = 1.0 - lam
    v = dem.v
    rel = ev.rel

    # irregularity: the row's rhythm surprise, the hand's own off-ratio interval, or the window's snap diversity,
    # over a floor every event pays
    q_u = smoothstep((ev.U_rhy[ev.row] - p.u_lo) / (p.u_hi - p.u_lo))
    q_hand = p.tech_hand_irr * hand_irregular(ev, dem.hand, p)
    q_div = np.clip((snap_diversity(ev, p) - p.tech_div_lo) / p.tech_div_span, 0.0, 1.0)
    # snap diversity reads the rice tech ladder (it is what separates a Tech chart from a Speed chart);
    # on LN charts it mostly counts the heads of staggered holds, and the LN Tech ladder reads worse with
    # it, so ln_tech keeps the rhythm-surprise + hand-interval reading
    irregular = p.tech_floor + (1.0 - p.tech_floor) * np.maximum.reduce([q_u, q_hand, p.tech_div_w * q_div])
    irregular_ln = p.tech_floor + (1.0 - p.tech_floor) * np.maximum(q_u, q_hand)

    sustain = np.minimum(1.0, run_since_rest(ev, dem.hand, p) / p.stamina_sustain)
    # the hand's chord size on the row: a chordstream asks more stamina than a single-note stream of the same
    # rate (Kai's ruling 2026-11-03, ADR-0056), floored so single-note streams still read
    if p.stamina_chord_scope == "row":
        chord_size = np.bincount(ev.row[~rel], minlength=ev.n_rows)[ev.row].astype(float)
    elif p.stamina_chord_scope == "window":
        # the mean press-row size within +-1 s of the event's row (a chordstream's density, not one row's)
        row_size = np.bincount(ev.row[~rel], minlength=ev.n_rows).astype(float)
        press_rows = np.flatnonzero(row_size > 0)
        rt = ev.T[press_rows]
        cum = np.concatenate([[0.0], np.cumsum(row_size[press_rows])])
        lo = np.searchsorted(rt, ev.T[ev.row] - 1.0)
        hi = np.searchsorted(rt, ev.T[ev.row] + 1.0, side="right")
        chord_size = (cum[hi] - cum[lo]) / np.maximum(hi - lo, 1)
    else:
        key = ev.row * 2 + dem.hand[ev.col]
        chord_size = np.bincount(key[~rel], minlength=2 * ev.n_rows)[key].astype(float)
    chord = p.stamina_chord_floor + (1.0 - p.stamina_chord_floor) * np.minimum(1.0, (chord_size - 1.0) / p.stamina_chord_span)

    # inverse: a press while columns are held (both hands; §2.6 h), rising to full at inverse_h_ref
    q_inv = np.where(rel, 0.0, np.minimum(1.0, ev.h / p.inverse_h_ref) ** p.inverse_pow)

    # release: the LN's tail lies in a row with no press (§8.1 q_rel), weighted by how precise the release must be
    press_row = np.bincount(ev.row[~rel], minlength=ev.n_rows) > 0
    tr = ev.tail_row[ev.obj]
    has_tail = ev.is_ln & (tr >= 0)
    isolated = (has_tail & ~press_row[np.where(has_tail, tr, 0)]).astype(float)
    head_row = np.full(len(ev.tail_row), -1)
    head_row[ev.obj[~rel]] = ev.row[~rel]
    ell = np.where(has_tail, ev.T[np.where(tr >= 0, tr, 0)] - ev.T[np.where(head_row[ev.obj] >= 0, head_row[ev.obj], 0)], np.inf)
    precise = np.where(rel, np.minimum(1.0, p.release_ell / np.maximum(ell, 1e-3)), 0.0)
    q_rel = isolated * (p.release_floor + (1.0 - p.release_floor) * precise)

    return {
        "rc_jack": dem.v_same * rc,
        "rc_tech": v * irregular * rc,
        "rc_speed": dem.v_rate * rc,
        "rc_stamina": (dem.v_rate if p.stamina_src == "rate" else v) * sustain * chord * rc,
        "ln_general": v * lam,
        "ln_tech": v * irregular_ln * lam,
        "ln_inverse": v * q_inv * lam,
        "ln_release": v * q_rel * lam,
    }


def skill_tau(k: str, p: Params) -> float:
    """The hand memory skill k's cost is accumulated with: rc_speed reads a burst of hand rate, the rest tau_h."""
    return p.speed_tau if k == "rc_speed" else p.tau_h


def skill_eps(k: str, p: Params) -> float:
    return p.eps_rc if k.startswith("rc") else p.eps_ln


def skill_readings(ev: Events, dem: HandDemand, p: Params) -> Tuple[Dict[str, np.ndarray], Dict[str, np.ndarray]]:
    """§8.3/§9.2: per skill, (its cost series c_k, its demand reading d_k): c_k on the hand accumulator with its memory."""
    costs = skill_costs(ev, dem, p)
    d = {k: accumulate(ev, dem.hand, costs[k], p, skill_tau(k, p)) ** (1.0 / p.gamma) for k in SKILLS}
    return costs, d


def skill_demands(ev: Events, dem: HandDemand, p: Params) -> Dict[str, float]:
    """§9.2 (v0.2.3): D_k, each skill's cost on the hand accumulator with its memory, solved under unit weights."""
    _, d = skill_readings(ev, dem, p)
    ones = np.ones(ev.n)
    return {k: solve(d[k], ones, skill_eps(k, p), p) for k in SKILLS}
