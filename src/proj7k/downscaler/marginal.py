"""
What removing an event is worth, read off the engine's own equations (ADR-0021).

The downscaler's job in the engine's terms: bring the expected loss at the target level
`D*`, `L(D*) = sum_i p_i(D*)`, down to the tolerance. The engine builds `p_i` from the demand reading
`d_i = E_i ** (1 / gamma)`, where `E_i` is the event's hand accumulator (§6.1):

    E_r = phi_r * E_{r-1} + (1 - phi_r) * in_r,      phi_r = exp(-(T_r - T_prev) / tau_h)

(`in_r` is the sum of `v ** gamma` over the hand's events in row r). That is linear in each event's
`v ** gamma`, so the effect on the expected loss of taking an event's demand away has a closed form:

    dL / d(v_j ** gamma) = (1 - phi_r) * G_r,       G_r = c_r + phi_{r+1} * G_{r+1}
    c_i = beta * p_i * (1 - p_i) / (gamma * d_i ** gamma)

`G` is a backward exponential recursion over a hand's rows, so the whole field is scored in O(n). This is a
first-order score used to rank candidates; the loop re-evaluates the engine after every batch, so the
ranking does not have to be exact. It does not see second-order effects (a deleted note also lengthens
the gaps its neighbours are read against); those the re-evaluation catches.
"""

from typing import Optional

import numpy as np

from proj7k.engine.skills import hand_of
from proj7k.engine.solver import loss
from proj7k.field import ChartField


def removal_benefit(
    field: ChartField, theta: float, weights: Optional[np.ndarray] = None, *,
    d: Optional[np.ndarray] = None, v: Optional[np.ndarray] = None, tau: Optional[float] = None,
) -> np.ndarray:
    """
    Per event: the first-order drop in the chart's expected loss at level `theta` if the event's own
    demand were taken out of its hand's accumulator. With `weights` (per event) the loss is that weighted sum.
    By default the series is the total's (`field.d` read off `field.v` with memory `tau_h`); a skill's own
    series (ADR-0056: `field.dk[:, k]` read off `field.ck[:, k]` with `skill_tau`) is passed as `d`, `v`, `tau`,
    and its loss is the unit-weight sum its `D_k` is solved on.
    """
    ev, p = field.events, field.params
    d = field.d if d is None else d
    v = field.v if v is None else v
    tau = p.tau_h if tau is None else tau
    pl = loss(d, theta, p)
    with np.errstate(divide="ignore", invalid="ignore"):
        c = np.where(d > 0, p.beta * pl * (1.0 - pl) / (p.gamma * d ** p.gamma), 0.0)
    if weights is not None:
        c = c * weights
    vg = v ** p.gamma
    hand = hand_of(field.thumb)[ev.col]
    benefit = np.zeros(ev.n)
    for h in (0, 1):
        members = np.flatnonzero(hand == h)
        if len(members) == 0:
            continue
        rows, pos = np.unique(ev.row[members], return_inverse=True)   # this hand's update rows, in order
        c_row = np.bincount(pos, weights=c[members], minlength=len(rows))
        T = ev.T[rows]
        phi = np.zeros(len(rows))                                      # a hand's first update has phi = 0 (R10)
        phi[1:] = np.exp(-np.diff(T) / tau)
        G = np.zeros(len(rows))
        run = 0.0
        for k in range(len(rows) - 1, -1, -1):
            run = c_row[k] + (phi[k + 1] * run if k + 1 < len(rows) else 0.0)
            G[k] = run
        benefit[members] = ((1.0 - phi) * G)[pos] * vg[members]
    return benefit
