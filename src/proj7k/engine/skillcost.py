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

    # irregularity: the row's rhythm surprise, or the hand's own off-ratio interval, over a floor every event pays
    q_u = smoothstep((ev.U_rhy[ev.row] - p.u_lo) / (p.u_hi - p.u_lo))
    q_irr = np.maximum(q_u, p.tech_hand_irr * hand_irregular(ev, dem.hand, p))
    irregular = p.tech_floor + (1.0 - p.tech_floor) * q_irr

    sustain = np.minimum(1.0, run_since_rest(ev, dem.hand, p) / p.stamina_sustain)

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
        "rc_stamina": v * sustain * rc,
        "ln_general": v * lam,
        "ln_tech": v * irregular * lam,
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
