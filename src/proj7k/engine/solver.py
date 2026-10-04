"""
Difficulty as a threshold (spec §3): the level `theta` at which the weighted expected number of
events lost reaches the tolerance, `sum_i w_i p_i(theta) = eps (W + N0)`.

`D` is that level for the total (all weights 1); `D_k` is the same solve under skill k's membership
weights, with the tolerance of its family.
"""

import numpy as np
from scipy import special

from proj7k.engine.params import Params


def loss(d: np.ndarray, theta: float, p: Params) -> np.ndarray:
    """§3.1 p_i(theta), the chance an event of difficulty reading d is lost at level theta; d = 0 gives 0."""
    if theta <= 0.0:  # the limit: every event with any difficulty is lost, and log(0) - log(0) is not a number
        return (d > 0).astype(float)
    with np.errstate(divide="ignore"):
        return special.expit(p.beta * (np.log(d) - np.log(theta)))


def solve(d: np.ndarray, w: np.ndarray, eps: float, p: Params) -> float:
    """§3.2, bisection on ln(theta) (R7). A root outside [theta_min, theta_max] is clamped to the bound."""
    target = eps * (w.sum() + p.N0)
    if w[d > 0].sum() <= target:
        return 0.0
    lo, hi = np.log(p.theta_min), np.log(p.theta_max)

    def g(lt: float) -> float:
        th = np.exp(lt)
        return float(np.dot(w, loss(d, th, p)) - target)

    if g(lo) <= 0:
        return p.theta_min
    if g(hi) >= 0:
        return p.theta_max
    while hi - lo > p.theta_tol:
        mid = 0.5 * (lo + hi)
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    return float(np.exp(0.5 * (lo + hi)))
