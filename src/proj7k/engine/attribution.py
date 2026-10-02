"""
Skill membership and attribution (spec §8) and dominance (§9.4).

Membership `w_ik` (overlapping) says how much event i belongs to skill k and weights the solve for
`D_k` and the coverage `C_k`. Attribution `a_ik` (a partition within each family) apportions the
event's share of the total's difficulty and gives dominance `pi_k`. All features are read at
`theta = D`, the total difficulty.
"""

from typing import Tuple

import numpy as np

from proj7k.engine.demand import HandDemand
from proj7k.engine.events import Events
from proj7k.engine.params import Params
from proj7k.engine.skills import RC_COUNT, hand_of
from proj7k.engine.solver import loss


def smoothstep(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, 0.0, 1.0)
    return z * z * (3 - 2 * z)


def ln_context(ev: Events, p: Params) -> np.ndarray:
    """§8.1 lambda: 1 on an LN, and rising to 1 as the event's hand has more columns held."""
    return np.maximum(ev.is_ln.astype(float), np.minimum(1.0, ev.h / p.ln_context_holds))


def memberships(ev: Events, dem: HandDemand, p: Params) -> Tuple[np.ndarray, np.ndarray]:
    """§8.1–8.2: (w [n, 8], a [n, 8]) in SKILLS order."""
    lam = ln_context(ev, p)
    q_jack = smoothstep((dem.r - p.r_lo) / (p.r_hi - p.r_lo))

    # chord share: events this hand plays in this row
    hand = hand_of(dem.thumb)
    key = ev.row * 2 + hand[ev.col]
    size = np.bincount(key, minlength=2 * ev.n_rows)[key].astype(float)
    q_chord = smoothstep((size - 1.0) / (p.k_hi - 1.0))

    q_rhythm = smoothstep((ev.U_rhy[ev.row] - p.u_lo) / (p.u_hi - p.u_lo))
    q_inverse = np.where(~ev.rel, np.minimum(1.0, ev.h / p.h_ref), 0.0)

    # release share: the tail of the LN this event belongs to lies in a row with no press; both its
    # press and its release carry the property of the whole LN
    press_row = np.bincount(ev.row[~ev.rel], minlength=ev.n_rows) > 0
    tr = ev.tail_row[ev.obj]
    has_tail = ev.is_ln & (tr >= 0)
    q_release = (has_tail & ~press_row[np.where(has_tail, tr, 0)]).astype(float)

    rc, ln = 1 - lam, lam
    unit = np.stack([
        q_jack, q_rhythm, (1 - q_jack) * (1 - q_chord), (1 - q_jack) * q_chord,                  # RC, unit mass
        1 - np.maximum(np.maximum(q_rhythm, q_inverse), q_release), q_rhythm, q_inverse, q_release,  # LN, unit mass
    ], 1)
    w = unit * np.concatenate([np.repeat(rc[:, None], RC_COUNT, 1), np.repeat(ln[:, None], RC_COUNT, 1)], 1)
    a = np.zeros_like(w)
    a[:, :RC_COUNT] = w[:, :RC_COUNT] / unit[:, :RC_COUNT].sum(1, keepdims=True)
    a[:, RC_COUNT:] = w[:, RC_COUNT:] / unit[:, RC_COUNT:].sum(1, keepdims=True)
    return w, a


def dominance(a: np.ndarray, d: np.ndarray, D: float, p: Params) -> np.ndarray:
    """§9.4: pi_k = sum_i a_ik s_i, with s_i the event's normalised share of p_i (1 - p_i) at theta = D."""
    pl = loss(d, D, p)
    s = pl * (1 - pl)
    s = s / s.sum()
    return a.T @ s
