"""
Does a chart's difficulty depend on the accuracy target in a chart-specific way, and which loss sharpness
(spec §3.1 beta) does the score data ask for?  Internal research on the osu! data dump: the data and the
per-chart output stay on the machine that holds them; only the aggregate summary is printed.

usage: PYTHONPATH=src python3 tools/research/accuracy_curve.py SCORES.sqlite3 CATALOG.sqlite3 OUT.json [WORKERS]

SCORES.sqlite3 is the 7K dump database of the score-validation study (tables charts, scores);
CATALOG.sqlite3 holds the .osu files (table beatmaps(id, osu_blob zlib)), opened read-only.

1. Data only: an additive model log(1 - acc) = c_b - s_u gives each player a skill s_u. Each chart's slope of
   log(1 - acc) against s_u says how fast its misses fall as players improve; its levels at 92/96/99% follow.
   Split-half reliability (two disjoint halves of the players) says whether charts really differ in slope
   and in the 99%-minus-92% gap, beyond noise.
2. Engine: a chart's event demands d_i (§7, the thumb that gives the lower D) give F_b(theta) = mean_i
   sigmoid(beta (ln d_i - ln theta)), the share lost at level theta. For each beta, a level theta_u per player
   is fitted on development charts, then on held-out charts (set hash % 5 == 0) we compare
   (a) the shape test: one free offset per chart, residual of log(1-acc) - log F_b(theta_u);
   (b) the steepness test: Spearman between the engine's and the data's per-chart slope of log(1-acc) on
   ln theta_u (steeper = smaller gap between the 92% and 99% levels), against the data's own split-half ceiling.
"""

import collections
import hashlib
import json
import sqlite3
import sys
import zlib
from multiprocessing import Pool

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import lsqr
from scipy.stats import spearmanr

ALLOWED = {"CL", "MR", "HD", "FI", "SD", "PF", "TD"}
MIN_USER, MIN_CHART, MIN_SLOPE = 30, 20, 40
BETAS = [3, 4, 6, 8, 12, 16, 24, 32]
TARGETS = {"92": 0.08, "96": 0.04, "99": 0.01}
GRID = np.linspace(np.log(0.05), np.log(200.0), 400)  # ln theta
Q = 256  # quantiles of ln d kept per chart


def held(set_id):
    return int(hashlib.sha1(str(set_id).encode()).hexdigest(), 16) % 5 == 0


def half(u):
    return int(hashlib.sha1(f"h{u}".encode()).hexdigest(), 16) % 2


def demands(a):
    bid, blob = a
    try:
        from proj7k.engine.demand import hand_demand
        from proj7k.engine.events import notes_from_osu, preprocess
        from proj7k.engine.params import DEFAULT as P
        from proj7k.engine.solver import solve
        ev = preprocess(notes_from_osu(zlib.decompress(blob).decode("utf-8")), P)
        best = None
        for thumb in (0, 1):
            d = hand_demand(ev, thumb, P).d
            D = solve(d, np.ones(len(d)), P.eps_rc, P)
            if best is None or D < best[0]:
                best = (D, d)
        d = best[1]
        ld = np.log(d[d > 0])
        if len(ld) < 50:
            return bid, None, None
        # zero-demand events are never lost: they count in the denominator only
        return bid, np.quantile(ld, (np.arange(Q) + 0.5) / Q).tolist(), float((d > 0).mean())
    except Exception:
        return bid, None, None


def load(db):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    sets = dict(c.execute("select beatmap_id,set_id from charts"))
    best = {}
    for u, b, mods, rate, pf, gr, go, ok, me, mi in c.execute(
            "select user_id,beatmap_id,mods,rate,perfect,great,good,ok,meh,miss from scores"):
        n = pf + gr + go + ok + me + mi
        if n == 0 or rate != 1.0 or not set(filter(None, (mods or "").split(","))) <= ALLOWED:
            continue
        acc = (300 * (pf + gr) + 200 * go + 100 * ok + 50 * me) / (300 * n)
        y = float(np.log(max(1 - acc, 1e-4)))
        if (u, b) not in best or y < best[(u, b)]:
            best[(u, b)] = y
    while True:
        cu = collections.Counter(u for u, _ in best)
        cb = collections.Counter(b for _, b in best)
        n = {k: v for k, v in best.items() if cu[k[0]] >= MIN_USER and cb[k[1]] >= MIN_CHART}
        if len(n) == len(best):
            return n, sets
        best = n


def additive(rows):
    """log(1-acc) = c_b - s_u, least squares; returns s_u."""
    us = {u: i for i, u in enumerate(sorted({u for u, _, _ in rows}))}
    bs = {b: i for i, b in enumerate(sorted({b for _, b, _ in rows}))}
    I = np.repeat(np.arange(len(rows)), 2)
    J = np.array([[us[u], len(us) + bs[b]] for u, b, _ in rows]).ravel()
    V = np.tile([-1.0, 1.0], len(rows))
    X = sparse.csr_matrix((V, (I, J)), shape=(len(rows), len(us) + len(bs)))
    sol = lsqr(X, np.array([y for _, _, y in rows]), atol=1e-9, btol=1e-9, iter_lim=5000)[0]
    s = sol[:len(us)]
    return {u: s[i] - s.mean() for u, i in us.items()}


def slopes(rows, skill, charts):
    """Per chart: OLS of log(1-acc) on skill. Returns {b: (slope, intercept, n)}."""
    by = collections.defaultdict(list)
    for u, b, y in rows:
        if b in charts:
            by[b].append((skill[u], y))
    out = {}
    for b, v in by.items():
        if len(v) < MIN_SLOPE // 2:
            continue
        x, y = np.array(v).T
        if x.std() < 1e-6:
            continue
        k, c = np.polyfit(x, y, 1)
        if k < 0:
            out[b] = (k, c, len(v))
    return out


def gap(k):
    """ln(0.01) - ln(0.08) in skill units along a chart's line: how much more skill 99% needs than 92%."""
    return (np.log(0.01) - np.log(0.08)) / k


def rel(a, b, ids):
    ids = [i for i in ids if i in a and i in b]
    if len(ids) < 10:
        return None, len(ids)
    return round(float(spearmanr([a[i] for i in ids], [b[i] for i in ids]).statistic), 3), len(ids)


def main():
    db, cat, out = sys.argv[1:4]
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    best, sets = load(db)
    rows = [(u, b, y) for (u, b), y in best.items()]
    charts = sorted({b for _, b, _ in rows})
    R = {"scores": len(rows), "players": len({r[0] for r in rows}), "charts": len(charts)}
    print(R, flush=True)

    # ---- 1. data only
    s = additive(rows)
    ks = slopes(rows, s, set(charts))
    sl = {b: v[0] for b, v in ks.items() if v[2] >= MIN_SLOPE}
    lv = {t: {b: (np.log(e) - ks[b][1]) / ks[b][0] for b in sl} for t, e in TARGETS.items()}
    halves = [slopes([r for r in rows if half(r[0]) == h], s, set(sl)) for h in (0, 1)]
    g = [{b: gap(v[0]) for b, v in hh.items()} for hh in halves]
    k = [{b: v[0] for b, v in hh.items()} for hh in halves]
    l96 = [{b: (np.log(.04) - v[1]) / v[0] for b, v in hh.items()} for hh in halves]
    R["data"] = {
        "charts_with_slope": len(sl),
        "slope_median": float(np.median(list(sl.values()))),
        "slope_iqr": np.percentile(list(sl.values()), [25, 75]).round(3).tolist(),
        "split_half_rho_slope": rel(k[0], k[1], sl),
        "split_half_rho_gap_99_92": rel(g[0], g[1], sl),
        "split_half_rho_level96": rel(l96[0], l96[1], sl),
    }
    # charts with nearly the same 96% level: how far apart are their 92% and 99% levels (in 96%-level SDs)?
    b96 = np.array([lv["96"][b] for b in sl])
    sd96 = b96.std()
    d92, d99 = [], []
    ids = list(sl)
    order = np.argsort(b96)
    for i in range(len(order) - 1):
        x, y = ids[order[i]], ids[order[i + 1]]
        if abs(lv["96"][x] - lv["96"][y]) < 0.05 * sd96:
            d92.append(abs(lv["92"][x] - lv["92"][y]) / sd96)
            d99.append(abs(lv["99"][x] - lv["99"][y]) / sd96)
    R["data"]["neighbours_same96"] = {"pairs": len(d99), "median_abs_diff99_in_sd96": float(np.median(d99)) if d99 else None,
                                      "p90_abs_diff99_in_sd96": float(np.percentile(d99, 90)) if d99 else None,
                                      "median_abs_diff92_in_sd96": float(np.median(d92)) if d92 else None}
    print("data", R["data"], flush=True)

    # ---- 2. engine
    c = sqlite3.connect(f"file:{cat}?mode=ro", uri=True)
    blobs = []
    for i in range(0, len(charts), 500):
        ch = charts[i:i + 500]
        blobs += c.execute(f"select id,osu_blob from beatmaps where id in ({','.join('?' * len(ch))})", ch).fetchall()
    with Pool(workers) as p:
        dm = {b: (np.array(q), f) for b, q, f in p.imap_unordered(demands, blobs, chunksize=8) if q is not None}
    rows = [r for r in rows if r[1] in dm]
    cb = sorted(dm)
    ci = {b: i for i, b in enumerate(cb)}
    LQ = np.array([dm[b][0] for b in cb])            # charts x Q
    FR = np.array([dm[b][1] for b in cb])            # share of events with any demand
    us = sorted({u for u, _, _ in rows})
    ui = {u: i for i, u in enumerate(us)}
    U = np.array([ui[u] for u, _, _ in rows])
    B = np.array([ci[b] for _, b, _ in rows])
    Y = np.array([y for _, _, y in rows])
    H = np.array([held(sets.get(b, b)) for _, b, _ in rows])
    R["engine_rows"] = int(len(rows))
    R["engine"] = {}
    for beta in BETAS:
        # ln F_b on the grid: charts x grid
        lF = np.empty((len(cb), len(GRID)))
        for j, lt in enumerate(GRID):
            lF[:, j] = np.log(np.maximum(FR * (1 / (1 + np.exp(-beta * (LQ - lt)))).mean(1), 1e-12))
        la = 0.0
        dev = ~H
        for _ in range(4):
            # each player's level from development charts; then one global offset
            err = (Y[dev, None] - la - lF[B[dev]]) ** 2
            tot = np.zeros((len(us), len(GRID)))
            np.add.at(tot, U[dev], err)
            th = tot.argmin(1)
            la = float(np.mean(Y[dev] - lF[B[dev], th[U[dev]]]))
        seen = np.bincount(U[dev], minlength=len(us)) > 0
        pred = la + lF[B, th[U]]
        resid = Y - pred
        H_ok = H & seen[U]
        hb = collections.defaultdict(list)
        for i in np.where(H_ok)[0]:
            hb[B[i]].append(i)
        # (a) one free offset per held-out chart: what remains is the curve shape
        shape, raw = [], []
        for b, ii in hb.items():
            r = resid[ii]
            shape += list(r - r.mean())
            raw += list(r)
        # (b) per-chart curve steepness over the levels of the players who played it: the slope of log(1-acc)
        # on ln theta_u, in the data and in the engine's prediction for the same players; a steeper slope is a
        # smaller gap between the 92% and 99% levels. Ceiling: the data's slope from two disjoint player halves.
        lt_u = GRID[th]
        eng_gap, dat_gap, hk = {}, {}, ({}, {})
        for b, ii in hb.items():
            ii = np.array(ii)
            if len(ii) < MIN_SLOPE:
                continue
            x = lt_u[U[ii]]
            if x.std() < 1e-6:
                continue
            dat_gap[b] = np.polyfit(x, Y[ii], 1)[0]
            eng_gap[b] = np.polyfit(x, pred[ii], 1)[0]
            for h in (0, 1):
                m = np.array([half(us[U[i]]) == h for i in ii])
                if m.sum() >= MIN_SLOPE // 2 and x[m].std() > 1e-6:
                    hk[h][b] = np.polyfit(x[m], Y[ii][m], 1)[0]
        ok = [b for b in dat_gap if np.isfinite(eng_gap.get(b, np.nan))]
        R["engine"][beta] = {
            "dev_rmse": float(np.sqrt(np.mean(resid[~H] ** 2))),
            "heldout_rmse": float(np.sqrt(np.mean(np.square(raw)))),
            "heldout_shape_rmse": float(np.sqrt(np.mean(np.square(shape)))),
            "slope_rho_engine_vs_data": rel(eng_gap, dat_gap, ok),
            "slope_rho_data_split_half": rel(hk[0], hk[1], ok),
            "engine_slope_sd": float(np.std([eng_gap[b] for b in ok])) if ok else None,
            "data_slope_sd": float(np.std([dat_gap[b] for b in ok])) if ok else None,
            "offset": la,
        }
        print("beta", beta, R["engine"][beta], flush=True)
    json.dump(R, open(out, "w"), indent=1)
    print("done", out)


if __name__ == "__main__":
    main()
