#!/usr/bin/env python3
"""
PROTOTYPE (throwaway) — spec v0.2 §13 calibration of the X-class parameters, with the LN anchors in the loss.

What the spec fixes (§13.2): maximise  m - rho_1 * sum_j ((ln Om_j - ln Om0_j) / s_j)^2  with m the minimum
adjacent-tier gap in ln D_k over the 8 own-pool ladders; (a, b) are solved afterwards from the RC anchors (§10).
What it does NOT give, and this script chooses (implementer's choices, to be ruled on by the spec owner):
  s_j = ln 2 for every parameter, rho_1 = 0.002, rho_2 = 0                       (as the v0.1 calibration)
  LN anchors (§10 says they constrain the X-class exchange parameters): a term  - rho_A * rms, where rms is the
      log-error of the four-LN-map tier stars against the LN anchors, with (a, b) re-solved inside every
      evaluation from the RC anchors (Zenith bound active-set, as the engine does).  rho_A = 0.3
  --obj spec   the spec's max-min margin
  --obj hinge  sum over all 112 adjacent pairs of max(0, 0.02 - gap): a relaxation for an infeasible T1
S-class parameters and the three v0.2 structural switches stay at their v0.2 values.
`--leave-out N` drops tier N from every ladder (T2 §14) and reports whether it falls between its neighbours.

Usage: PYTHONPATH=src python3 prototypes/prototype_spec_v02_calibrate.py --obj spec|hinge [--leave-out N]
"""
import argparse
import json
from multiprocessing import Pool
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prototype_spec_v01_engine as E  # noqa: E402  (v0.2 defaults)

X = ["beta", "N0", "k_ring_mid", "k_mid_idx", "k_ring_idx", "k_thumb_idx", "k_thumb_other", "k_cross", "chi_0",
     "w_rel", "eta_h", "eta_g", "mu_p", "mu_r", "nu", "lambda_R", "r_lo", "r_hi"]
S_J, RHO1, RHO_A, HINGE = float(np.log(2.0)), 0.002, 0.3, 0.02
CFG = json.loads((Path(__file__).with_name("prototype_spec_v01_anchors.json")).read_text(encoding="utf-8"))
RC_POOLS = list(CFG["pools"])
LN_POOLS = list(CFG["ln_pools"])
_G = {}


def _init():
    ch = E.load_corpus()
    _G["ch"] = ch
    _G["pre"] = [E.preprocess(c["notes"], E.P0) for c in ch]


def _one(args):
    i, over = args
    c = _G["ch"][i]
    try:
        r = E.evaluate(c["notes"], dict(E.P0, **over), pre=_G["pre"][i])
        return r["skills"][E.POOL_OF[c["pool"]]]["D"], r["total"]["D"]
    except Exception:
        return float("nan"), float("nan")


def params_of(x):
    return {n: E.P0[n] * float(np.exp(S_J * xi)) for n, xi in zip(X, x)}


def read(pool, x):
    over = params_of(x)
    out = pool.map(_one, [(i, over) for i in range(120)], chunksize=10)
    return np.array([o[0] for o in out]).reshape(8, 15), np.array([o[1] for o in out]).reshape(8, 15)


def scale_from_rc(T):
    """(a, b) from the RC anchors: ln-ln least squares over the equalities, an unmet inequality held as equality."""
    pools = [E.POOL_IDX[p] for p in RC_POOLS]
    tier = lambda t, rows: float(np.exp(np.mean(np.log(T[rows, E.TIERS.index(t)]))))  # noqa: E731
    rows = [list(E.POOL_OF).index(p) for p in RC_POOLS]
    eq = [a for a in CFG["anchors"] if "stars" in a]
    ge = [a for a in CFG["anchors"] if "min_stars" in a]
    X_ = np.log([tier(a["tier"], rows) for a in eq])
    Y_ = np.log([a["stars"] for a in eq])
    b, la = np.polyfit(X_, Y_, 1)
    for g in ge:
        if np.exp(la) * tier(g["tier"], rows) ** b < g["min_stars"]:
            xb, yb = float(np.log(tier(g["tier"], rows))), float(np.log(g["min_stars"]))
            b = float(np.sum((X_ - xb) * (Y_ - yb)) / np.sum((X_ - xb) ** 2))
            la = yb - b * xb
    return float(np.exp(la)), float(b), tier


def ln_rms(T, a, b):
    rows = [list(E.POOL_OF).index(p) for p in LN_POOLS]
    errs = []
    for an in CFG["ln_anchors"]:
        d = float(np.exp(np.mean(np.log(T[rows, E.TIERS.index(an["tier"])]))))
        errs.append(np.log(a * d ** b / an["stars"]))
    return float(np.sqrt(np.mean(np.square(errs))))


def terms(D, T, keep):
    lD = np.log(np.maximum(D[:, keep], E.P0["theta_min"]))
    g = np.diff(lD, axis=1)
    a, b, _ = scale_from_rc(T)
    return g, ln_rms(T, a, b), (a, b)


def objective(x, pool, keep, obj):
    pr = params_of(x)
    if pr["r_lo"] >= pr["r_hi"]:
        return 1e3
    D, T = read(pool, x)
    if not (np.all(np.isfinite(D)) and np.all(np.isfinite(T))):
        return 1e3
    g, rms, _ = terms(D, T, keep)
    fit = g.min() if obj == "spec" else -float(np.maximum(0.0, HINGE - g).sum())
    return -(fit - RHO1 * float(np.sum(np.square(x))) - RHO_A * rms)


def main():
    import cma
    from scipy import stats
    ap = argparse.ArgumentParser()
    ap.add_argument("--obj", choices=("spec", "hinge"), required=True)
    ap.add_argument("--leave-out", type=int, default=None)
    ap.add_argument("--maxfevals", type=int, default=2400)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    E.POOL_IDX = {p: i for i, p in enumerate(E.POOL_OF)}
    keep = [n for n in range(15) if n != a.leave_out]
    tag = f"{a.obj}" + ("" if a.leave_out is None else f"_lo{a.leave_out}")
    out_path = Path(__file__).with_name(f"prototype_spec_v02_calibrate.{tag}.json")
    t0 = time.time()

    def summ(D, T):
        g, rms, (A, B) = terms(D, T, keep)
        return dict(min_ln_gap=float(g.min()), inversions=int((g <= 0).sum()), hinge_sum=float(np.maximum(0, HINGE - g).sum()),
                    mean_tau=float(np.mean([stats.kendalltau(range(len(keep)), D[k, keep])[0] for k in range(8)])),
                    ladders_strict=int(sum((g[k] > 0).all() for k in range(8))), ln_anchor_rms_log=rms, a=A, b=B)

    with Pool(a.workers, initializer=_init) as pool:
        D0, T0 = read(pool, np.zeros(len(X)))
        es = cma.CMAEvolutionStrategy(np.zeros(len(X)), 0.3, dict(seed=a.seed, popsize=24, maxfevals=a.maxfevals, verbose=-9))
        while not es.stop():
            xs = es.ask()
            es.tell(xs, [objective(x, pool, keep, a.obj) for x in xs])
            if es.countiter % 10 == 0:
                print(f"gen {es.countiter:4d} evals {es.countevals:5d} best {-es.best.f:+.4f} {time.time() - t0:.0f}s", flush=True)
        x = es.result.xbest
        D, T = read(pool, x)
    res = dict(tag=tag, obj=a.obj, rho1=RHO1, rho_A=RHO_A, s_j=S_J, hinge=HINGE, evals=es.countevals, seconds=round(time.time() - t0),
               params={n: dict(prior=E.P0[n], fitted=params_of(x)[n], ratio=float(np.exp(S_J * xi))) for n, xi in zip(X, x)},
               prior_summary=summ(D0, T0), fitted_summary=summ(D, T),
               fitted_D_ladders={k: [round(float(v), 3) for v in D[n]] for n, k in enumerate(E.SKILLS)})
    if a.leave_out is not None:
        h = a.leave_out
        res["held_out_between_neighbours"] = {k: bool(np.log(max(D[n, h - 1], 1e-9)) < np.log(max(D[n, h], 1e-9)) < np.log(max(D[n, h + 1], 1e-9)))
                                              for n, k in enumerate(E.SKILLS)}
    out_path.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("prior_summary", "fitted_summary") + (("held_out_between_neighbours",) if a.leave_out is not None else ())}, indent=1))


if __name__ == "__main__":
    main()
