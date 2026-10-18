"""
Train the generator's rhythm model (ADR-0032) from `tools/export_rhythm_data.py` output.

One song in ten (by its audio file) is held out: those songs are never trained on, and
`python3 -m proj7k.generator.evaluate --holdout` evaluates the generator on them only.

    PYTHONPATH=src python3 tools/train_rhythm_model.py --data ~/.cache/proj7k/rhythm-data.npz
"""

import argparse
import sys
from typing import List, Optional

import numpy as np

from proj7k.generator import rhythm as R

HIDDEN = (64, 32)
MAX_UNMATCHED = 0.2


def init(sizes, rng):
    layers = []
    for a, b in zip(sizes[:-1], sizes[1:]):
        layers.append([rng.normal(0, np.sqrt(2.0 / a), (a, b)).astype(np.float32), np.zeros(b, np.float32)])
    return layers


def forward(layers, X):
    hs = [X]
    h = X
    for i, (W, b) in enumerate(layers):
        h = h @ W + b
        if i < len(layers) - 1:
            h = np.maximum(h, 0.0)
        hs.append(h)
    z = h - h.max(axis=1, keepdims=True)
    p = np.exp(z)
    p /= p.sum(axis=1, keepdims=True)
    return hs, p


def log_loss(layers, X, y, batch=65536):
    total = 0.0
    for i in range(0, len(X), batch):
        _, p = forward(layers, X[i:i + batch])
        total += -np.log(np.maximum(p[np.arange(len(p)), y[i:i + batch]], 1e-9)).sum()
    return total / max(len(X), 1)


def train(X, y, Xv, yv, epochs, seed=0, lr=2e-3, batch=2048, l2=1e-5):
    rng = np.random.default_rng(seed)
    layers = init([X.shape[1], *HIDDEN, R.CLASSES], rng)
    m = [[np.zeros_like(W), np.zeros_like(b)] for W, b in layers]
    v = [[np.zeros_like(W), np.zeros_like(b)] for W, b in layers]
    step = 0
    best = (np.inf, None)
    for ep in range(epochs):
        order = rng.permutation(len(X))
        rate = lr * (0.5 ** (ep // 4))
        for s in range(0, len(X), batch):
            idx = order[s:s + batch]
            hs, p = forward(layers, X[idx])
            g = p
            g[np.arange(len(idx)), y[idx]] -= 1.0
            g /= len(idx)
            step += 1
            for li in range(len(layers) - 1, -1, -1):
                W, b = layers[li]
                gW = hs[li].T @ g + l2 * W
                gb = g.sum(axis=0)
                if li > 0:
                    g = (g @ W.T) * (hs[li] > 0)
                for k, gr in ((0, gW), (1, gb)):
                    m[li][k] = 0.9 * m[li][k] + 0.1 * gr
                    v[li][k] = 0.999 * v[li][k] + 0.001 * gr * gr
                    mh = m[li][k] / (1 - 0.9 ** step)
                    vh = v[li][k] / (1 - 0.999 ** step)
                    layers[li][k] -= rate * mh / (np.sqrt(vh) + 1e-8)
        tr, va = log_loss(layers, X[:200000], y[:200000]), log_loss(layers, Xv, yv)
        print(f"epoch {ep + 1}: train {tr:.4f}  holdout {va:.4f}", flush=True)
        if va < best[0]:
            best = (va, [[W.copy(), b.copy()] for W, b in layers])
    return best[1], best[0]


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data", required=True)
    p.add_argument("--output", default=str(R.MODEL_PATH))
    p.add_argument("--epochs", type=int, default=10)
    args = p.parse_args(argv)

    d = np.load(args.data)
    X, y, chart = d["X"].astype(np.float32), d["y"].astype(np.int64), d["chart"]
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    # Charts whose rows mostly miss the places are off their audio (dan courses chain several songs).
    good = np.asarray(d["unmatched"]) <= MAX_UNMATCHED
    keep = good[chart]
    X, y, chart = X[keep], y[keep], chart[keep]
    print(f"dropped {int((~good).sum())} charts with more than {MAX_UNMATCHED:.0%} of rows off the places", flush=True)
    if X.shape[1] != R.n_features():
        print(f"data has {X.shape[1]} features, the model expects {R.n_features()}: re-export", file=sys.stderr)
        return 1
    hold_chart = np.array([R.is_holdout(str(a)) for a in d["audios"]])
    hold = hold_chart[chart]
    mean = X[~hold].mean(axis=0)
    std = X[~hold].std(axis=0) + 1e-3
    Xn = (X - mean) / std
    print(f"{int(good.sum())} charts ({int((hold_chart & good).sum())} held out), {len(X)} places, "
          f"classes {np.bincount(y, minlength=R.CLASSES).tolist()}", flush=True)
    prior = np.bincount(y[~hold], minlength=R.CLASSES) / (~hold).sum()
    base = -np.log(prior[y[hold]]).mean()
    layers, loss = train(Xn[~hold], y[~hold], Xn[hold], y[hold], args.epochs)
    print(f"holdout log loss {loss:.4f} (class prior alone {base:.4f})")
    model = R.RhythmModel({"mean": mean.tolist(), "std": std.tolist(),
                           "layers": [[W.tolist(), b.tolist()] for W, b in layers],
                           "meta": {"charts": int(len(d["names"])), "holdout_charts": int(hold_chart.sum()),
                                    "holdout_log_loss": round(float(loss), 4), "prior_log_loss": round(float(base), 4)}})
    model.save(args.output)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
