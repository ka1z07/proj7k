#!/usr/bin/env python3
"""
PROTOTYPE (throwaway) — spec v0.1 §13 calibration of the X-class parameters.

Objective (§13.2, rho_2 = 0): maximise  m - rho_1 * sum_j ((ln Om_j - ln Om0_j) / s_j)^2
where m = min over (skill k, adjacent tier n) of ln D_k(X_{k,n+1}) - ln D_k(X_{k,n}).
The spec gives neither rho_1 nor s_j; THESE ARE MY CHOICES, to be ruled on by the spec owner:
  s_j   = ln 2 for every parameter (a factor-2 prior width)
  rho_1 = 0.002 (a factor-2 move of one parameter costs 0.002 of margin)
  rho_2 = 0 (A13 not adopted, §13.2 allows it)
S-class parameters (Delta_0, m_0, ...) are untouched. Optimiser: CMA-ES, as §13.2 suggests.
`--leave-out N` drops tier N from every ladder (T2 §14): calibrate without it, then report whether
the held-out chart sits strictly between its neighbours on each ladder.

Usage: PYTHONPATH=src python3 prototypes/prototype_spec_v01_calibrate.py [--leave-out N] [--maxfevals 3000]
"""
import argparse
import json
from multiprocessing import Pool
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prototype_spec_v01_engine as E
E.P0.update(E.V01)  # these scripts reproduce the v0.1 runs

# name -> prior (the §12 value); every one is an X-class parameter
X = ["beta", "N0", "k_ring_mid", "k_mid_idx", "k_ring_idx", "k_thumb_idx", "k_thumb_other", "k_cross", "chi_0",
     "w_rel", "eta_f", "eta_h", "eta_g", "mu_p", "mu_r", "nu", "lambda_R", "r_lo", "r_hi"]
S_J = np.log(2.0)
RHO1 = 0.002

_G = {}


def _init():
    ch = E.load_corpus()
    _G["ch"] = ch
    _G["pre"] = [E.preprocess(c["notes"], E.P0) for c in ch]


def _one(args):
    i, over = args
    P = dict(E.P0, **over)
    c = _G["ch"][i]
    r = E.evaluate(c["notes"], P, pre=_G["pre"][i])
    return r["skills"][E.POOL_OF[c["pool"]]]["D"], r["total"]["D"]


def params_of(x):
    return {n: E.P0[n] * float(np.exp(S_J * xi)) for n, xi in zip(X, x)}


def read(pool, x, skip=()):
    over = params_of(x)
    ch = _G_ch()
    out = pool.map(_one, [(i, over) for i in range(len(ch))], chunksize=10)
    D = np.array([o[0] for o in out]).reshape(8, 15)
    T = np.array([o[1] for o in out]).reshape(8, 15)
    return D, T


def _G_ch():
    if "ch_main" not in _G:
        _G["ch_main"] = E.load_corpus()
    return _G["ch_main"]


def margins(D, keep):
    lD = np.log(np.maximum(D[:, keep], E.P0["theta_min"]))
    return np.diff(lD, axis=1)


def objective(x, pool, keep):
    if params_of(x)["r_lo"] >= params_of(x)["r_hi"]:
        return 1e3
    D, _ = read(pool, x)
    m = margins(D, keep).min()
    return -(m - RHO1 * float(np.sum(np.square(x))))


def summarise(D, keep):
    g = margins(D, keep)
    from scipy import stats
    return dict(min_ln_gap=float(g.min()), inversions=int((g <= 0).sum()),
                mean_tau=float(np.mean([stats.kendalltau(range(len(keep)), D[k, keep])[0] for k in range(8)])),
                ladders_strict=int(sum((g[k] > 0).all() for k in range(8))))


def main():
    import cma
    ap = argparse.ArgumentParser()
    ap.add_argument("--leave-out", type=int, default=None)
    ap.add_argument("--maxfevals", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    keep = [n for n in range(15) if n != a.leave_out]
    tag = "full" if a.leave_out is None else f"lo{a.leave_out}"
    out_path = Path(__file__).with_name(f"prototype_spec_v01_calibrate.{tag}.json")
    t0 = time.time()
    with Pool(12, initializer=_init) as pool:
        base_D, _ = read(pool, np.zeros(len(X)))
        es = cma.CMAEvolutionStrategy(np.zeros(len(X)), 0.3, dict(seed=a.seed, popsize=24, maxfevals=a.maxfevals, verbose=-9))
        while not es.stop():
            xs = es.ask()
            es.tell(xs, [objective(x, pool, keep) for x in xs])
            if es.countiter % 10 == 0:
                print(f"gen {es.countiter:4d} evals {es.countevals:5d} best {-es.best.f:+.4f} {time.time()-t0:.0f}s", flush=True)
        x = es.result.xbest
        D, T = read(pool, x)
    res = dict(tag=tag, rho1=RHO1, s_j=S_J, evals=es.countevals, seconds=round(time.time() - t0),
               params={n: dict(prior=E.P0[n], fitted=params_of(x)[n], ratio=float(np.exp(S_J * xi))) for n, xi in zip(X, x)},
               prior_summary=summarise(base_D, keep), fitted_summary=summarise(D, keep),
               fitted_D_ladders={k: [round(float(v), 3) for v in D[n]] for n, k in enumerate(E.SKILLS)})
    if a.leave_out is not None:
        h = a.leave_out
        res["held_out_between_neighbours"] = {k: bool(np.log(max(D[n, h - 1], 1e-9)) < np.log(max(D[n, h], 1e-9)) < np.log(max(D[n, h + 1], 1e-9)))
                                              for n, k in enumerate(E.SKILLS)}
    out_path.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("prior_summary", "fitted_summary") + (("held_out_between_neighbours",) if a.leave_out is not None else ())}, indent=1))


if __name__ == "__main__":
    main()
