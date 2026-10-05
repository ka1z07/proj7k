#!/usr/bin/env python3
"""
PROTOTYPE (throwaway) — does the dominance distribution pi respond to controlled edits of a chart?

No labels. Take benchmark charts, change one construct, and check the direction of the change in the relevant pi_k
(mean over charts, and in how many charts it moves the expected way):
  unchord      keep one note per chord                 pi(rc_stamina) down, pi(rc_speed) up
  chordify     add a same-hand neighbour to every note pi(rc_stamina) up
  jackify      60% of notes repeat the previous column of their hand   pi(rc_jack) up
  jitter       shift every note by up to +-35 ms       pi(rc_tech) up
  lnify        turn every rice with room before the next note in its column into a hold   LN family mass up
  isolate      shift each LN tail so that no press shares its row      pi(ln_release) up
  align        snap each LN tail to the nearest press within 150 ms    pi(ln_release) down

Usage: PYTHONPATH=src python3 prototypes/prototype_spec_v02_response.py [--rel-mode spec|isolated|note]
"""
import argparse
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prototype_spec_v01_engine as E  # noqa: E402

LEFT, RIGHT = (0, 1, 2), (4, 5, 6)


def hand(c, thumb=0):
    return 0 if c in LEFT else 1 if c in RIGHT else thumb


def by_time(notes):
    return sorted(notes, key=lambda n: (n[1], n[0]))


def unchord(notes, rnd):
    out, last_t = [], None
    for c, t, e in by_time(notes):
        if last_t is not None and abs(t - last_t) < 0.002:
            continue
        out.append((c, t, e)); last_t = t
    return out


def chordify(notes, rnd):
    occ = {(c, round(t, 3)) for c, t, e in notes}
    out = list(notes)
    for c, t, e in notes:
        for d in (1, -1):
            c2 = c + d
            if 0 <= c2 <= 6 and hand(c2) == hand(c) and (c2, round(t, 3)) not in occ and c2 != 3:
                out.append((c2, t, e)); occ.add((c2, round(t, 3))); break
    return out


def jackify(notes, rnd, p=0.6):
    last = {0: None, 1: None}
    out, occ = [], set()
    for c, t, e in by_time(notes):
        h = hand(c)
        c2 = c
        if last[h] is not None and rnd.random() < p:
            c2 = last[h]
        if (c2, round(t, 3)) in occ:
            c2 = c
        occ.add((c2, round(t, 3))); last[h] = c2
        out.append((c2, t, e))
    return out


def jitter(notes, rnd, ms=0.035):
    out = []
    for c, t, e in notes:
        d = rnd.uniform(-ms, ms)
        out.append((c, t + d, None if e is None else e + d))
    return out


def lnify(notes, rnd):
    nxt = {}
    ns = by_time(notes)
    out = []
    for i, (c, t, e) in enumerate(ns):
        later = [t2 for c2, t2, _ in ns[i + 1:i + 400] if c2 == c and t2 > t + 0.001]
        if later and later[0] - t > 0.25:
            out.append((c, t, later[0] - 0.08))
        else:
            out.append((c, t, e))
    return out


def tails_shift(notes, rnd, mode):
    press = np.array(sorted(t for c, t, e in notes))
    out = []
    for c, t, e in notes:
        if e is None:
            out.append((c, t, e)); continue
        i = int(np.searchsorted(press, e)); cand = [press[j] for j in (i - 1, i) if 0 <= j < len(press)]
        near = min(cand, key=lambda x: abs(x - e)) if cand else e
        if mode == "isolate" and abs(near - e) < 0.012:
            e2 = e + 0.04
        elif mode == "align" and abs(near - e) < 0.15 and near > t + 0.1:
            e2 = near
        else:
            e2 = e
        out.append((c, t, e2))
    return out


def holdadd(notes, rnd, window=4.0):
    """Inverse-style edit: in every window one finger of one hand (hands alternating) is cleared of its notes and held
    for the whole window instead, while the rest of the chart plays on."""
    ns = list(notes)
    t0 = min(t for _, t, _ in notes); t1 = max((e if e is not None else t) for _, t, e in notes)
    k, t = 0, t0
    while t < t1:
        c = rnd.choice(LEFT if k % 2 == 0 else RIGHT)
        ns = [(c2, t2, e2) for c2, t2, e2 in ns if not (c2 == c and t - 0.02 <= t2 <= t + window)]
        ns.append((c, t + 0.1, t + window - 0.2))
        k += 1; t += window
    return ns


EDITS = {
    "unchord": (unchord, "Regular Stream", "rc_stamina", -1),
    "chordify": (chordify, "Regular Speed", "rc_stamina", +1),
    "jackify": (jackify, "Regular Speed", "rc_jack", +1),
    "jitter": (jitter, "Regular Jack", "rc_tech", +1),
    "isolate": (lambda n, r: tails_shift(n, r, "isolate"), "LN General", "ln_release", +1),
    "holdadd": (holdadd, "LN General", "ln_inverse", +1),
    "align": (lambda n, r: tails_shift(n, r, "align"), "LN Release", "ln_release", -1),
}


def pi_of(notes, P):
    r = E.evaluate(notes, P)
    return {k: v["dominance"] for k, v in r["skills"].items()}, sum(r["skills"][k]["dominance"] for k in E.SKILLS[4:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rel-mode", default=None)
    ap.add_argument("--tiers", default="3rd,5th,7th,9th,10th,Gamma")
    a = ap.parse_args()
    P = dict(E.P0) if a.rel_mode is None else dict(E.P0, rel_mode=a.rel_mode)
    charts = {(c["pool"], c["tier"]): c["notes"] for c in E.load_corpus()}
    tiers = a.tiers.split(",")
    print(f"rel_mode={P['rel_mode']}")
    for name, (fn, pool, skill, sign) in EDITS.items():
        d = []
        for t in tiers:
            rnd = random.Random(7)
            base, _ = pi_of(charts[(pool, t)], P)
            edited, _ = pi_of(fn(charts[(pool, t)], rnd), P)
            d.append(edited[skill] - base[skill])
        if name in ("holdadd", "isolate"):
            cross = "ln_release" if name == "holdadd" else "ln_inverse"
            dc = []
            for t in tiers:
                rnd = random.Random(7)
                b0, _ = pi_of(charts[(pool, t)], P); e0, _ = pi_of(fn(charts[(pool, t)], rnd), P)
                dc.append(e0[cross] - b0[cross])
            print(f"      (cross effect on {cross}: mean {np.mean(dc):+.3f})")
        ok = sum(x * sign > 0 for x in d)
        print(f"  {name:9s} on {pool:15s} d pi({skill:10s}) mean {np.mean(d):+.3f}  expected {'up' if sign > 0 else 'down'}: {ok}/{len(d)}  {[round(x, 2) for x in d]}")
    # lnify: LN family mass
    d = []
    for t in tiers:
        rnd = random.Random(7)
        base = charts[("Regular Stream", t)]
        _, m0 = pi_of(base, P); _, m1 = pi_of(lnify(base, rnd), P)
        d.append(m1 - m0)
    print(f"  lnify     on Regular Stream   d LN-family pi mean {np.mean(d):+.3f}  expected up: {sum(x > 0 for x in d)}/{len(d)}  {[round(x, 2) for x in d]}")


if __name__ == "__main__":
    main()
