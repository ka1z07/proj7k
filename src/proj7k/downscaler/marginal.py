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

import numpy as np

from proj7k.engine.skills import hand_of
from proj7k.engine.solver import loss
from proj7k.field import ChartField


def removal_benefit(field: ChartField, theta: float) -> np.ndarray:
    """
    Per event: the first-order drop in the chart's expected loss at level `theta` if the event's own
    demand were taken out of its hand's accumulator.
    """
    ev, p = field.events, field.params
    pl = loss(field.d, theta, p)
    with np.errstate(divide="ignore", invalid="ignore"):
        c = np.where(field.d > 0, p.beta * pl * (1.0 - pl) / (p.gamma * field.d ** p.gamma), 0.0)
    vg = field.v ** p.gamma
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
        phi[1:] = np.exp(-np.diff(T) / p.tau_h)
        G = np.zeros(len(rows))
        run = 0.0
        for k in range(len(rows) - 1, -1, -1):
            run = c_row[k] + (phi[k + 1] * run if k + 1 < len(rows) else 0.0)
            G[k] = run
        benefit[members] = ((1.0 - phi) * G)[pos] * vg[members]
    return benefit
