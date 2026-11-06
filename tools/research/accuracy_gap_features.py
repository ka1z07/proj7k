"""
What makes a chart's 99% level sit far above its 92% level?  Follow-up to `accuracy_curve.py` (same data,
same additive model): the gap between the 99% and 92% levels is real per chart (split-half partial 0.71 at
equal 96% level) but the engine's own curve explains little of it. Here each chart's excess gap (the gap
minus what its 96% level predicts) is set against chart features. Internal research on the osu! data dump:
prints aggregates only.

usage: PYTHONPATH=src python3 tools/research/accuracy_gap_features.py SCORES.sqlite3 CATALOG.sqlite3 OUT.json [WORKERS]

Reported:
- per feature: Spearman with the excess gap (and with the 96% level, to see confounding);
- a linear model of the excess gap on all features fitted on development charts (set hash % 5 != 0), read on
  held-out charts: Spearman, R^2, and the split-half ceiling of the excess gap on the same charts;
- the same in stars: held-out MAE of the 92% and 99% levels predicted from the 96% level alone vs with features;
- if scikit-learn is importable, a gradient-boosted model too, with its permutation importances.
"""

import collections
import json
import pathlib
import sqlite3
import sys
import zlib
from multiprocessing import Pool

import numpy as np
from scipy.stats import rankdata, spearmanr

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from accuracy_curve import MIN_SLOPE, TARGETS, additive, half, held, load, slopes  # noqa: E402


def features(a):
    """Chart features from the .osu text; None when the chart cannot be read."""
    bid, blob = a
    try:
        from proj7k.engine.demand import hand_demand
        from proj7k.engine.evaluate import evaluate_notes
        from proj7k.engine.events import notes_from_osu, preprocess
        from proj7k.engine.params import DEFAULT as P
        from proj7k.engine.skills import SKILLS
        from proj7k.patterns import pattern_tags
        text = zlib.decompress(blob).decode("utf-8")
        notes = notes_from_osu(text)
        if len(notes) < 50:
            return bid, None
        prof = evaluate_notes(notes)
        f = {"stars": prof.total_stars}
        for k in SKILLS:
            f[f"star_{k}"] = prof.skills[k].stars
            f[f"level_{k}"] = prof.skills[k].level
        heads = np.array(sorted(h for _, h, _ in notes))
        dur = max(heads[-1] - heads[0], 1.0)
        f["log_length"] = float(np.log(dur))
        f["nps"] = len(notes) / dur
        f["ln_share"] = sum(t is not None for _, _, t in notes) / len(notes)
        lens = [t - h for _, h, t in notes if t is not None]
        f["ln_short_share"] = float(np.mean([x < 0.3 for x in lens])) if lens else 0.0
        rows = collections.Counter(round(h, 3) for _, h, _ in notes)
        f["notes_per_row"] = len(notes) / len(rows)
        gaps = np.diff(np.array(sorted(rows)))
        gaps = gaps[gaps > 0.005]
        f["row_gap_cv"] = float(gaps.std() / gaps.mean()) if len(gaps) > 2 else 0.0
        # the 1/4-beat grid misses: share of row gaps that are not a simple ratio of the median gap
        if len(gaps) > 2:
            r = gaps / np.median(gaps)
            simple = np.array([0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1, 1.5, 2, 3, 4])
            f["odd_snap_share"] = float(np.mean(np.min(np.abs(r[:, None] / simple[None, :] - 1), 1) > 0.06))
        else:
            f["odd_snap_share"] = 0.0
        # density unevenness: 4 s window note counts
        w = np.histogram(heads, bins=max(int(dur / 4), 1))[0]
        f["density_cv"] = float(w.std() / max(w.mean(), 1e-9))
        f["peak_over_mean_density"] = float(w.max() / max(w.mean(), 1e-9))
        ev = preprocess(notes, P)
        d = hand_demand(ev, 0, P).d
        ld = np.log(d[d > 0])
        f["logd_sd"] = float(ld.std())
        f["logd_p99_p50"] = float(np.percentile(ld, 99) - np.percentile(ld, 50))
        f["logd_p50_p10"] = float(np.percentile(ld, 50) - np.percentile(ld, 10))
        for line in text.splitlines():
            if line.startswith("OverallDifficulty:"):
                f["od_file"] = float(line.split(":")[1])
        pt = pattern_tags(notes)
        for k, v in pt.shares.items():
            f[f"tag_{k}"] = float(v)
        return bid, f
    except Exception:
        return bid, None


def partial_rank(x, y, c):
    """Spearman of x and y after removing a quadratic in rank(c) from both ranks."""
    n = len(x)
    cc = rankdata(c) / n
    A = np.column_stack([np.ones(n), cc, cc * cc])
    res = []
    for v in (x, y):
        r = rankdata(v)
        res.append(r - A @ np.linalg.lstsq(A, r, rcond=None)[0])
    return float(np.corrcoef(*res)[0, 1])


def main():
    db, cat, out = sys.argv[1:4]
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    best, sets = load(db)
    rows = [(u, b, y) for (u, b), y in best.items()]
    charts = sorted({b for _, b, _ in rows})
    s = additive(rows)
    ks = slopes(rows, s, set(charts))
    ks = {b: v for b, v in ks.items() if v[2] >= MIN_SLOPE}
    lv = {t: {b: (np.log(e) - ks[b][1]) / ks[b][0] for b in ks} for t, e in TARGETS.items()}
    halves = [slopes([r for r in rows if half(r[0]) == h], s, set(ks)) for h in (0, 1)]
    hgap = [{b: (np.log(.01) - np.log(.08)) / v[0] for b, v in hh.items()} for hh in halves]

    c = sqlite3.connect(f"file:{cat}?mode=ro", uri=True)
    ids = sorted(ks)
    blobs = []
    for i in range(0, len(ids), 500):
        ch = ids[i:i + 500]
        blobs += c.execute(f"select id,osu_blob from beatmaps where id in ({','.join('?' * len(ch))})", ch).fetchall()
    with Pool(workers) as p:
        F = {b: f for b, f in p.imap_unordered(features, blobs, chunksize=8) if f is not None}
    try:
        od = dict(sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("select beatmap_id, od from charts"))
    except sqlite3.OperationalError:
        od = {}
    names = sorted({k for f in F.values() for k in f})
    ids = [b for b in ids if b in F]
    X = np.array([[F[b].get(k, 0.0) for k in names] for b in ids])
    if any(od.get(b) is not None for b in ids):
        names.append("od")
        X = np.column_stack([X, [od.get(b) or F[b].get("od_file", 8.0) for b in ids]])
    X = np.nan_to_num(X)

    # stars per additive unit: linear map of the 96% level onto engine stars
    l96 = np.array([lv["96"][b] for b in ids])
    m, c0 = np.polyfit(l96, X[:, names.index("stars")], 1)
    L = {t: m * np.array([lv[t][b] for b in ids]) + c0 for t in TARGETS}
    gap = L["99"] - L["92"]
    # excess gap: what the 96% level (quadratic) does not predict
    A = np.column_stack([np.ones(len(ids)), L["96"], L["96"] ** 2])
    excess = gap - A @ np.linalg.lstsq(A, gap, rcond=None)[0]
    R = {"charts": len(ids), "stars_per_unit": float(m), "gap_stars_median": float(np.median(gap)),
         "excess_sd_stars": float(excess.std())}

    R["feature_rho"] = {}
    for j, k in enumerate(names):
        if X[:, j].std() < 1e-12:
            continue
        R["feature_rho"][k] = {"excess": round(float(spearmanr(X[:, j], excess).statistic), 3),
                               "level96": round(float(spearmanr(X[:, j], L["96"]).statistic), 3),
                               "gap_partial_on_96": round(partial_rank(X[:, j], gap, L["96"]), 3)}
    top = sorted(R["feature_rho"].items(), key=lambda kv: -abs(kv[1]["gap_partial_on_96"]))
    print("charts", len(ids), "gap median", R["gap_stars_median"], "excess sd", R["excess_sd_stars"], flush=True)
    for k, v in top[:25]:
        print(f"  {k:28s} partial {v['gap_partial_on_96']:+.3f}  excess {v['excess']:+.3f}  level96 {v['level96']:+.3f}")

    # held-out prediction
    H = np.array([held(sets.get(b, b)) for b in ids])
    mu, sd = X[~H].mean(0), X[~H].std(0) + 1e-9
    Z = (X - mu) / sd
    Zb = np.column_stack([np.ones(len(ids)), Z, L["96"], L["96"] ** 2])
    lam = 10.0
    G = Zb[~H].T @ Zb[~H] + lam * np.eye(Zb.shape[1])
    wgap = np.linalg.solve(G, Zb[~H].T @ gap[~H])
    pred_gap = Zb @ wgap
    base = np.column_stack([np.ones(len(ids)), L["96"], L["96"] ** 2])
    wb = np.linalg.lstsq(base[~H], gap[~H], rcond=None)[0]
    base_gap = base @ wb

    def r2(y, p):
        return float(1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum())

    hid = [b for b, h in zip(ids, H) if h]
    ok = [i for i, b in enumerate(ids) if H[i] and b in hgap[0] and b in hgap[1]]
    ceil = partial_rank(np.array([hgap[0][ids[i]] for i in ok]), np.array([hgap[1][ids[i]] for i in ok]), L["96"][ok])
    R["heldout"] = {
        "charts": len(hid),
        "excess_rho_linear": round(float(spearmanr(pred_gap[H] - base_gap[H], gap[H] - base_gap[H]).statistic), 3),
        "excess_r2_linear": round(r2(gap[H] - base_gap[H], pred_gap[H] - base_gap[H]), 3),
        "excess_split_half_ceiling_partial": round(ceil, 3),
        # in stars: the 92% and 99% levels, predicted from the 96% level alone or with the features (gap split
        # around 96% in the development charts' proportion)
    }
    lo_share = np.median(((L["96"] - L["92"]) / np.maximum(gap, 1e-6))[~H])
    for name, g in (("from96_only", base_gap), ("with_features", pred_gap)):
        p92 = L["96"] - lo_share * g
        p99 = L["96"] + (1 - lo_share) * g
        R["heldout"][f"mae92_{name}"] = round(float(np.abs(p92 - L["92"])[H].mean()), 3)
        R["heldout"][f"mae99_{name}"] = round(float(np.abs(p99 - L["99"])[H].mean()), 3)
    coef = sorted(zip(names, wgap[1:1 + len(names)]), key=lambda kv: -abs(kv[1]))
    R["linear_top_coefs_stars_per_sd"] = [(k, round(float(v), 3)) for k, v in coef[:15]]
    print("heldout", R["heldout"], flush=True)
    print("linear top", R["linear_top_coefs_stars_per_sd"], flush=True)

    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.inspection import permutation_importance
        Xg = np.column_stack([X, L["96"]])
        y = gap - base_gap
        gb = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40,
                                           random_state=0).fit(Xg[~H], y[~H])
        pg = gb.predict(Xg[H])
        imp = permutation_importance(gb, Xg[H], y[H], n_repeats=5, random_state=0)
        R["gbm"] = {"excess_rho": round(float(spearmanr(pg, y[H]).statistic), 3), "excess_r2": round(r2(y[H], pg), 3),
                    "importance": sorted([(k, round(float(v), 4)) for k, v in zip(names + ["level96"], imp.importances_mean)],
                                         key=lambda kv: -kv[1])[:15]}
        print("gbm", R["gbm"], flush=True)
    except ImportError:
        R["gbm"] = "scikit-learn not installed"
    json.dump(R, open(out, "w"), indent=1)
    print("done", out)


if __name__ == "__main__":
    main()
