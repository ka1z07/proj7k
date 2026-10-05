#!/usr/bin/env python3
"""
PROTOTYPE (throwaway) — can ONE strain field carry both the total and the eight techniques?

Supports `docs/specs/unified-strain-field-engine-proposal.md`. It answers three questions the
proposal depends on, against the frozen 120-chart Jinjin corpus:

  (a) Does a single per-action strain, pooled over each technique's share of it, order the
      eight technique ladders?
  (b) Is the "bigger subset dominates" defect real, and does unit-partition accounting remove it?
  (c) Does one global two-parameter map put the total on the community SR ruler without the
      per-chart official SR ever entering the loss?

Model, in one paragraph. Every note is a press action and every long note adds a release action.
A press costs `base + hand_clock(dt_hand) + finger_clock(dt_finger)`; fingers locked by holds
compress both clocks. A release costs a base plus one unit per *differently long* hold still
down. Costs are modulated by how unpredictable the press rhythm is, credited to techniques by
the action's pattern class (overlapping classes split the cost; the generic techniques take what
no specific class claims), then accumulated with exponential decay on the finger and on the hand.
The reading at an action is its strain. Total = p-norm of all strains. The accumulation is
linear, so every action's strain splits into eight parts and `sum_k D_k^p == T^p` holds exactly.

VERDICT (coarse calibration; the frozen constants are the lowest-loss of six seeds):

  (a) Mostly yes. Own-pool technique ladders read mean Kendall tau 0.964 with 12 adjacent
      inversions of 112 (the HEAD engine, same metric: 0.880 / 26). Seven ladders improve; LN
      Release regresses (0.905 against 0.962). Nine of the twelve inversions sit on tier pairs
      the official SR also inverts or ties.
  (b) Yes. Full-weight union subsets: own-pool dominant on 50/120, and LN General takes 43 of
      the 45 specialised-LN charts. Unit-partition accounting: 72/120. LN Tech and LN Release
      stay at 0/15 — they have no usable signal of their own yet.
  (c) Yes for the scale. star = 2.547 ln(x) - 9.094 from the 30 (track, tier) medians; the
      per-chart official SR, never in the loss, reads MAE 0.462 / rho 0.986 (HEAD: 0.840 / 0.931).

NOT shown: strict monotonicity. The LN-specific terms (lock compression, release discrimination,
the post-LN finger clock) are calibrated to nearly nothing, so an LN chart is rated by its
press + release action density; read literally, the inverse-BPM-law stretch test fails (BPM* 140
reads 7.11 against a 5.0 ceiling). Constants differ across seeds while the readings agree.

This file reads only the parser from `proj7k`; none of the engine.

Usage:
  PYTHONPATH=src python3 prototypes/prototype_unified_strain_field.py              # full report
  PYTHONPATH=src python3 prototypes/prototype_unified_strain_field.py --chart X.osu
  PYTHONPATH=src python3 prototypes/prototype_unified_strain_field.py --calibrate 1   # ~20 min
  PYTHONPATH=src python3 prototypes/prototype_unified_strain_field.py --holdout 0     # ~15 min
"""

import argparse
import json
from pathlib import Path
import random
import statistics
import sys

import numpy as np
from scipy import optimize, stats

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from proj7k.assets import load_corpus_fixture  # noqa: E402
from proj7k.parser import NoteType, parse_osu_7k  # noqa: E402

TIERS = ["0th", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th",
         "Gamma", "Azimuth", "Zenith", "Stellium"]
POOLS = ["Regular Jack", "Regular Tech", "Regular Speed", "Regular Stream",
         "LN General", "LN Tech", "LN Inverse", "LN Release"]
AXES = ["jack", "tech", "speed", "stream", "ln_general", "ln_tech", "ln_inverse", "ln_release"]
OWN = dict(zip(POOLS, AXES))
K = {a: i for i, a in enumerate(AXES)}

HAND = np.array([0, 0, 0, 2, 1, 1, 1])  # column -> 0 left, 1 right, 2 thumb
STEP_EPS_MS = 8.0          # actions this close are one step (ADR-0007's chord tolerance)
JACK_MAX_MS = 220.0        # ADR-0007's T_jack
CONTEXT_STEPS = 8          # +-steps for the chord context and the local step interval
SMOOTH_HALFLIFE_STEPS = 4  # rhythm-unpredictability smoothing

#: Coarse calibration (differential evolution; see `calibrate`). Orders of magnitude, not a
#: tuned release. `T_H/A_H` and `T_J/A_J` are redundant pairs — each clock is a scale-free power
#: law, so only `A * T**a` is identified.
CONSTANTS = {
    "T_H": 61.9432,
    "a_H": 1.1595,
    "A_H": 2.9531,
    "T_J": 193.742,
    "a_J": 3.0977,
    "A_J": 1.2763,
    "rel_len": 479.2428,
    "lam_s": 0.1037,
    "lam_o": 0.3121,
    "rho0": 0.9653,
    "rho1": 0.0943,
    "kap": 0.1266,
    "alpha": 0.1072,
    "chord_g": 0.2106,
    "tau_F": 0.5212,
    "tau_H": 3.9127,
    "w_H": 0.9056,
    "p": 9.3037,
    "C_ref": 0.2815,
}

BOUNDS = dict(T_H=(60, 220), a_H=(0.3, 3.0), A_H=(0.2, 4), T_J=(80, 260), a_J=(0.5, 3.5), A_J=(0.05, 4),
              rel_len=(0, 600), lam_s=(0, 2.0), lam_o=(0, 0.8), rho0=(0.0, 1.0), rho1=(0, 3), kap=(0, 1),
              alpha=(0, 4), chord_g=(0, 1.0), tau_F=(0.15, 1.2), tau_H=(0.5, 5), w_H=(0.03, 1.0),
              p=(4, 16), C_ref=(0.08, 0.8))
NAMES = list(BOUNDS)


# --------------------------------------------------------------------------- corpus

def notes_of(content):
    """A chart as sorted (time_ms, column, end_ms | None)."""
    notes = []
    for ho in parse_osu_7k(content).hit_objects:
        is_ln = ho.note_type == NoteType.LN and ho.end_time is not None and ho.end_time > ho.time
        notes.append((float(ho.time), int(ho.column), float(ho.end_time) if is_ln else None))
    notes.sort(key=lambda x: (x[0], x[1]))
    return notes


def load_corpus():
    manifest = json.loads((REPO / "docs/research/structured_index.json").read_text(encoding="utf-8"))
    corpus = load_corpus_fixture(REPO / "tests/fixtures/benchmark_corpus.json.gz")
    charts = []
    for pool in POOLS:
        for ti, tier in enumerate(TIERS):
            entry = manifest[pool][tier]
            notes = notes_of(corpus[int(entry["id"])])
            charts.append(dict(pool=pool, tier=tier, ti=ti, sr=entry["sr"], notes=notes, table=action_table(notes)))
    return charts


# --------------------------------------------------------------------------- action table

def _rhythm_unpredictability(step_time):
    """Per press step: how badly the last interval is predicted by any of the four before it."""
    m = len(step_time)
    iv = np.diff(step_time)
    r = np.zeros(m)
    for k in range(2, m):
        a = iv[k - 1]
        if a > 600:
            continue
        best = None
        for lag in (1, 2, 3, 4):
            j = k - 1 - lag
            if j < 0:
                break
            b = iv[j]
            if b > 600:
                continue
            x = abs(np.log2(a / b))
            best = x if best is None else min(best, x)
        if best is not None:
            r[k] = 0.0 if best < 0.1 else min(best, 1.5)
    out = np.zeros(m)
    g = 0.5 ** (1.0 / SMOOTH_HALFLIFE_STEPS)
    acc = 0.0
    for k in range(m):
        acc = acc * g + (1 - g) * r[k]
        out[k] = acc
    return out


def action_table(notes):
    """Everything the cost terms read, per action. Independent of every calibration constant."""
    ev = []
    for i, (t, c, e) in enumerate(notes):
        ev.append((t, 0, c, i))
        if e is not None:
            ev.append((e, 1, c, i))
    ev.sort(key=lambda x: (x[0], x[1], x[2]))
    n = len(ev)
    t = np.array([x[0] for x in ev])
    typ = np.array([x[1] for x in ev])
    col = np.array([x[2] for x in ev])
    nid = np.array([x[3] for x in ev])
    cl = np.zeros(n, dtype=int)
    ct = t.copy()
    for i in range(1, n):
        if t[i] - t[i - 1] <= STEP_EPS_MS:
            cl[i], ct[i] = cl[i - 1], ct[i - 1]
        else:
            cl[i] = cl[i - 1] + 1
    hand = HAND[col]
    starts = np.array([x[0] for x in notes])
    ends = np.array([x[2] if x[2] is not None else np.nan for x in notes])
    ncol = np.array([x[1] for x in notes])
    is_ln_note = ~np.isnan(ends)
    lnlen = np.where(is_ln_note, ends - starts, 0.0)

    press = typ == 0
    _, pstep = np.unique(cl[press], return_inverse=True)
    step_of = np.full(n, -1)
    step_of[press] = pstep
    m = int(pstep.max()) + 1
    step_time = np.zeros(m)
    step_time[pstep] = ct[press]
    step_size = np.bincount(pstep)
    step_iv = np.diff(step_time, prepend=step_time[0] - 1e9)
    local_step = np.zeros(m)
    chord_ctx = np.zeros(m)
    is_chord = (step_size >= 2).astype(float)
    for k in range(m):
        a, b = max(1, k - CONTEXT_STEPS), min(m, k + CONTEXT_STEPS + 1)
        local_step[k] = np.median(step_iv[a:b]) if b > a else 1e9
        a0 = max(0, k - CONTEXT_STEPS)
        chord_ctx[k] = is_chord[a0:b].mean()

    w_fin = np.full(n, np.inf)        # finger clock (ms), before the release lenience
    after_ln = np.zeros(n, bool)      # previous object on this column was a long note
    jack = np.zeros(n, bool)
    inverse = np.zeros(n, bool)
    d_hand = np.full(n, np.inf)       # hand clock (ms), presses
    same_fin = np.zeros(n, bool)      # this finger was in the hand's previous step
    d_hand_ev = np.full(n, np.inf)    # hand clock (ms), any action
    held_s = np.zeros(n)
    held_o = np.zeros(n)
    ndiff_s = np.zeros(n)
    ndiff_o = np.zeros(n)
    opens_inverse = np.zeros(n, bool)  # release whose column is re-pressed within one local step
    release_of = {nid[i]: i for i in range(n) if typ[i] == 1}

    lns = sorted((starts[j], ends[j], ncol[j], lnlen[j]) for j in range(len(notes)) if is_ln_note[j])
    active, ptr = [], 0
    last_obj = {}
    last_step_t, cur_step_t = {0: None, 1: None}, {0: None, 1: None}
    last_cols, cur_cols = {0: set(), 1: set()}, {0: set(), 1: set()}
    last_ev_t, cur_ev_t = {0: None, 1: None}, {0: None, 1: None}

    def count_holds(i, into_same, into_other, only_other_length=None):
        ti, c, h = ct[i], col[i], hand[i]
        for a in active:
            if a[2] == c or a[1] <= ti + STEP_EPS_MS:
                continue
            if only_other_length is not None and abs(a[3] - only_other_length) <= 10.0:
                continue
            ah = HAND[a[2]]
            if h == 2 or ah == 2:
                into_same[i] += 0.5
            elif ah == h:
                into_same[i] += 1
            else:
                into_other[i] += 1

    for i in range(n):
        ti, c, h = ct[i], col[i], hand[i]
        while ptr < len(lns) and lns[ptr][0] < ti - STEP_EPS_MS:
            active.append(lns[ptr])
            ptr += 1
        active = [a for a in active if a[1] > ti - STEP_EPS_MS]
        if typ[i] == 0:
            count_holds(i, held_s, held_o)
            if c in last_obj:
                pj, prev_step = last_obj[c]
                if is_ln_note[pj]:
                    gap = max(ti - ends[pj], 0.0)
                    w_fin[i], after_ln[i] = gap, True
                    inverse[i] = gap <= local_step[step_of[i]] + STEP_EPS_MS
                    if inverse[i]:
                        opens_inverse[release_of[pj]] = True
                else:
                    w_fin[i] = ti - starts[pj]
                    jack[i] = step_of[i] - prev_step == 1 and w_fin[i] <= JACK_MAX_MS
            last_obj[c] = (nid[i], step_of[i])
            if h != 2:
                if cur_step_t[h] is not None and ti - cur_step_t[h] > STEP_EPS_MS:
                    last_step_t[h], last_cols[h], cur_cols[h] = cur_step_t[h], cur_cols[h], set()
                if cur_step_t[h] is None or ti - cur_step_t[h] > STEP_EPS_MS:
                    cur_step_t[h] = ti
                cur_cols[h].add(c)
                if last_step_t[h] is not None:
                    d_hand[i] = ti - last_step_t[h]
                    same_fin[i] = c in last_cols[h]
        else:
            count_holds(i, ndiff_s, ndiff_o, only_other_length=lnlen[nid[i]])
        if h != 2:
            if cur_ev_t[h] is not None and ti - cur_ev_t[h] > STEP_EPS_MS:
                last_ev_t[h] = cur_ev_t[h]
            if cur_ev_t[h] is None or ti - cur_ev_t[h] > STEP_EPS_MS:
                cur_ev_t[h] = ti
            if last_ev_t[h] is not None:
                d_hand_ev[i] = ti - last_ev_t[h]

    chord_h = np.ones(n)
    _, inv_key, cnt = np.unique((cl * 3 + hand)[press], return_inverse=True, return_counts=True)
    chord_h[press] = cnt[inv_key]

    unpredictability = _rhythm_unpredictability(step_time)
    C = np.zeros(n)
    C[press] = unpredictability[pstep]
    rel = np.where(~press)[0]
    if len(rel):
        C[rel] = unpredictability[np.clip(np.searchsorted(step_time, ct[rel], side="right") - 1, 0, m - 1)]
    chi = np.zeros(n)
    chi[press] = chord_ctx[pstep]
    is_head = np.zeros(n, bool)
    is_head[press] = is_ln_note[nid[press]]
    ln_ctx = np.where(press, (is_head | (held_s + held_o > 0)).astype(float), 1.0)

    return dict(n=n, t=ct / 1000.0, press=press, col=col, hand=hand, w_fin=w_fin, after_ln=after_ln,
                jack=jack, inverse=inverse, d_hand=d_hand, same_fin=same_fin, d_hand_ev=d_hand_ev,
                held_s=held_s, held_o=held_o, ndiff_s=ndiff_s, ndiff_o=ndiff_o, chord_h=chord_h,
                C=C, chi=chi, ln_ctx=ln_ctx, is_head=is_head, opens_inverse=opens_inverse)


# --------------------------------------------------------------------------- strain

def action_cost(tb, P):
    """Per action: (press cost, release base, release discrimination), before attribution."""
    lock = 1.0 + P["lam_s"] * tb["held_s"] + P["lam_o"] * tb["held_o"]   # locked fingers compress the clocks
    chord = 1.0 + P["chord_g"] * (tb["chord_h"] - 1.0)
    dh = np.maximum(tb["d_hand"], 20.0)
    hand_clock = np.where(tb["same_fin"], 0.0, P["A_H"] * (P["T_H"] * lock / dh) ** P["a_H"]) * chord
    w = np.maximum(tb["w_fin"] + np.where(tb["after_ln"], P["rel_len"], 0.0), 20.0)
    finger_clock = P["A_J"] * (P["T_J"] * lock / w) ** P["a_J"]
    modulation = 1.0 + P["alpha"] * tb["C"]
    press = np.where(tb["press"], (chord + hand_clock + finger_clock) * modulation, 0.0)
    hr = P["A_H"] * (P["T_H"] / np.maximum(tb["d_hand_ev"], 20.0)) ** P["a_H"]
    rel = ~tb["press"]
    base = np.where(rel, P["rho0"] * (1 + hr) * modulation, 0.0)
    disc = np.where(rel, P["rho1"] * (tb["ndiff_s"] + P["kap"] * tb["ndiff_o"]) * (1 + hr) * modulation, 0.0)
    return press, base, disc


def credited_costs(tb, P):
    """The attribution table: each action's cost, credited to techniques by pattern class. (n, 8)."""
    press, base, disc = action_cost(tb, P)
    pr, q, chi = tb["press"], tb["ln_ctx"], tb["chi"]
    u = np.clip(tb["C"] / P["C_ref"], 0.0, 1.0) * pr          # unpredictable context takes its share first
    rest = (1 - u) * press
    is_jack = tb["jack"] & ~tb["after_ln"]
    is_inv = tb["inverse"] & ~is_jack
    generic = ~is_jack & ~is_inv
    out = np.zeros((tb["n"], 8))
    out[:, K["tech"]] = u * press * (1 - q)
    out[:, K["ln_tech"]] = u * press * q
    out[:, K["jack"]] = np.where(is_jack, rest, 0.0)
    out[:, K["ln_inverse"]] = np.where(is_inv, rest, 0.0) + np.where(tb["opens_inverse"], base, 0.0)
    out[:, K["ln_general"]] = np.where(generic, rest * q, 0.0) + np.where(~tb["opens_inverse"], base, 0.0)
    out[:, K["stream"]] = np.where(generic, rest * (1 - q) * chi, 0.0)
    out[:, K["speed"]] = np.where(generic, rest * (1 - q) * (1 - chi), 0.0)
    out[:, K["ln_release"]] = disc
    return out


def _accumulate(times, cost, tau):
    """S_i = sum_{j<=i} cost_j * exp(-(t_i - t_j)/tau), in overflow-safe segments."""
    S = np.zeros_like(cost)
    m, i0, carry = len(times), 0, np.zeros(cost.shape[1])
    while i0 < m:
        t0 = times[i0]
        i1 = max(int(np.searchsorted(times, t0 + 20 * tau, side="right")), i0 + 1)
        e = np.exp((times[i0:i1] - t0) / tau)[:, None]
        S[i0:i1] = (np.cumsum(cost[i0:i1] * e, axis=0) + carry) / e
        if i1 < m:
            carry = S[i1 - 1] * np.exp(-(times[i1] - times[i1 - 1]) / tau)
        i0 = i1
    if m:  # actions sharing a timestamp read the value after the last simultaneous injection
        last = np.ones(m, bool)
        last[:-1] = times[1:] != times[:-1]
        idx = np.where(last)[0]
        S = S[idx[np.searchsorted(idx, np.arange(m))]]
    return S


def strain(tb, P):
    """Per action, the strain split by technique: finger-level + weighted hand-level. Shape (n, 8)."""
    cm, t = credited_costs(tb, P), tb["t"]
    F = np.zeros_like(cm)
    for c in range(7):
        ix = np.where(tb["col"] == c)[0]
        if len(ix):
            F[ix] = _accumulate(t[ix], cm[ix], P["tau_F"])
    H = []
    for side in (0, 1):
        wgt = np.where(tb["hand"] == side, 1.0, np.where(tb["hand"] == 2, 0.5, 0.0))
        ix = np.where(wgt > 0)[0]
        full = np.zeros_like(cm)
        full[ix] = _accumulate(t[ix], cm[ix] * wgt[ix][:, None], P["tau_H"])
        H.append(full)
    left, right = (tb["hand"] == 0)[:, None], (tb["hand"] == 1)[:, None]
    return F + P["w_H"] * np.where(left, H[0], np.where(right, H[1], 0.5 * (H[0] + H[1])))


def union_subsets(tb):
    """The full-weight, overlapping pattern subsets — the reading the proposal argues against."""
    pr = tb["press"]
    S = np.zeros((tb["n"], 8), bool)
    flow = pr & ~tb["jack"]
    rice = tb["ln_ctx"] == 0
    S[:, K["jack"]] = tb["jack"]
    S[:, K["speed"]] = flow & (tb["chi"] < 0.5)
    S[:, K["stream"]] = flow & (tb["chi"] >= 0.5)
    S[:, K["tech"]] = pr & rice & (tb["C"] > 0.15)
    S[:, K["ln_general"]] = ~pr | tb["is_head"] | (pr & (tb["held_s"] > 0))
    S[:, K["ln_inverse"]] = tb["inverse"]
    S[:, K["ln_release"]] = ~pr & (tb["ndiff_s"] >= 1)
    S[:, K["ln_tech"]] = ~rice & (tb["C"] > 0.15)
    return S


def read(tb, P):
    """Total T, technique difficulties D (unit-partition accounting), and the union reading."""
    sk = strain(tb, P)
    s = sk.sum(1)
    p = P["p"]
    return dict(T=(s ** p).sum() ** (1 / p),
                D=(sk * (s ** (p - 1))[:, None]).sum(0) ** (1 / p),
                D_union=((s ** p)[:, None] * union_subsets(tb)).sum(0) ** (1 / p))


# --------------------------------------------------------------------------- ruler & metrics

def grids(charts, P):
    own, own_u, T = np.zeros((8, 15)), np.zeros((8, 15)), np.zeros((8, 15))
    hit, hit_u, sr = np.zeros((8, 15)), np.zeros((8, 15)), np.zeros((8, 15))
    arg, arg_u = np.zeros((8, 15), int), np.zeros((8, 15), int)
    for c in charts:
        r = read(c["table"], P)
        g, a, ti = POOLS.index(c["pool"]), K[OWN[c["pool"]]], c["ti"]
        own[g, ti], own_u[g, ti], T[g, ti], sr[g, ti] = r["D"][a], r["D_union"][a], r["T"], c["sr"]
        arg[g, ti], arg_u[g, ti] = int(np.argmax(r["D"])), int(np.argmax(r["D_union"]))
        hit[g, ti], hit_u[g, ti] = arg[g, ti] == a, arg_u[g, ti] == a
    return dict(own=own, own_u=own_u, T=T, hit=hit, hit_u=hit_u, sr=sr, arg=arg, arg_u=arg_u)


def ruler(sr):
    """The community ruler: official-SR median per (track, tier). 30 numbers, no per-chart value."""
    return np.vstack([np.median(sr[:4], axis=0), np.median(sr[4:], axis=0)])


def star_map(T, sr, tiers=slice(None)):
    med = np.vstack([np.median(np.log(T[:4][:, tiers]), axis=0), np.median(np.log(T[4:][:, tiers]), axis=0)])
    target = ruler(sr)[:, tiers].ravel()
    A = np.vstack([med.ravel(), np.ones(med.size)]).T
    ab = np.linalg.lstsq(A, target, rcond=None)[0]
    return ab, float(np.abs(A @ ab - target).mean())


def bpm_star(notes):
    onsets = sorted({x[0] for x in notes})
    return 60000.0 / (4.0 * statistics.median([b - a for a, b in zip(onsets, onsets[1:])]))


def inverse_law_penalty(star_inv, bpms):
    """ADR-0006's Inverse BPM scaling law, in stars: <=145 -> <=5; 150-180 -> 6..8; >=190 -> >=9."""
    pen = np.zeros(len(bpms))
    lo, mid, hi = bpms <= 145, (bpms >= 150) & (bpms <= 180), bpms >= 190
    pen[lo] = np.maximum(0, star_inv[lo] - 5.0)
    pen[mid] = np.maximum(0, 6.0 - star_inv[mid]) + np.maximum(0, star_inv[mid] - 8.0)
    pen[hi] = np.maximum(0, 9.0 - star_inv[hi])
    return float(pen.mean())


def taus(own, tiers):
    return [stats.kendalltau(range(len(tiers)), own[g][tiers])[0] for g in range(8)]


def make_loss(charts, tiers):
    """Ladder order + ruler medians + the inverse law + own-pool dominance. No per-chart official SR."""
    inv_pool = POOLS.index("LN Inverse")
    bpms = np.array([bpm_star(c["notes"]) for c in charts if c["pool"] == "LN Inverse"])

    def loss(x):
        P = dict(zip(NAMES, map(float, x)))
        g = grids(charts, P)
        if not np.all(np.isfinite(g["own"])) or g["own"].min() <= 0:
            return 1e3
        lo = np.log(g["own"][:, tiers])
        order = 1 - np.mean([stats.kendalltau(range(len(tiers)), row)[0] for row in lo])
        hinge = np.maximum(0.0, 0.03 - np.diff(lo, axis=1)).mean()
        ab, rul = star_map(g["T"], g["sr"], tiers)
        law = inverse_law_penalty((ab[0] * np.log(g["T"][inv_pool]) + ab[1])[tiers], bpms[tiers])
        return 4 * order + 6 * hinge + 0.5 * rul + 0.6 * (1 - g["hit"][:, tiers].mean()) + 1.0 * law

    return loss


def calibrate(charts, seed, tiers=None, maxiter=36):
    tiers = list(range(15)) if tiers is None else tiers
    res = optimize.differential_evolution(make_loss(charts, tiers), [BOUNDS[n] for n in NAMES], seed=seed,
                                          maxiter=maxiter, popsize=7, tol=1e-6, polish=False,
                                          init="latinhypercube", updating="immediate")
    return dict(zip(NAMES, map(float, res.x))), float(res.fun), int(res.nfev)


# --------------------------------------------------------------------------- invariants

def synthetic_stream(step_ms, count, mode):
    cols = dict(alternating=[0, 4, 1, 5, 2, 6], roll4=[0, 1, 2, 4], one_hand=[0, 1, 2], trill=[0, 2], jack=[1])[mode]
    return [(1000.0 + i * step_ms, cols[i % len(cols)], None) for i in range(count)]


def stretched(notes, s):
    return [(t * s, c, e * s if e is not None else None) for t, c, e in notes]


def doubled(notes):
    end = max(e if e is not None else t for t, c, e in notes)
    off = end + 2000.0 - notes[0][0]
    return notes + [(t + off, c, e + off if e is not None else None) for t, c, e in notes]


def equal_length_ln(notes, length):
    """Every rice note that has room becomes an LN of one fixed length; the rest stay rice."""
    nxt, out = {}, []
    for t, c, e in sorted(notes, reverse=True):
        room = nxt.get(c, 1e18) - t
        out.append((t, c, t + length if room >= length + 40 else None))
        nxt[c] = t
    return sorted(out)


def invariants(charts, P, star):
    by = {(c["pool"], c["tier"]): c for c in charts}
    R = lambda notes: read(action_table(sorted(notes, key=lambda x: (x[0], x[1]))), P)  # noqa: E731
    out = {}

    out["K2 same step, five hand allocations (T must rise)"] = {
        m: round(R(synthetic_stream(75, 800, m))["T"], 1) for m in ("alternating", "roll4", "one_hand", "trill", "jack")}
    roll, jk = R(synthetic_stream(75, 800, "roll4")), R(synthetic_stream(75, 800, "jack"))
    out["K1 roll is not jack (jack share of T^p)"] = dict(
        roll4=float((roll["D"][K["jack"]] / roll["T"]) ** P["p"]), jack=float((jk["D"][K["jack"]] / jk["T"]) ** P["p"]))

    tb = action_table(synthetic_stream(110, 64, "jack"))
    s = strain(tb, P).sum(1)
    out["K4 jack run saturates (strain at hit 2,4,8,16,32,64)"] = [round(float(s[i - 1]), 1) for i in (2, 4, 8, 16, 32, 64)]

    base = by[("LN Inverse", "Stellium")]["notes"]
    out["K5 inverse stretched to BPM* (star)"] = {
        f"{bpm_star(stretched(base, f)):.0f}": round(star(R(stretched(base, f))["T"]), 2)
        for f in (1.0, 238 / 200, 238 / 180, 238 / 160, 238 / 140, 238 / 120, 238 / 100)}

    rice = by[("Regular Stream", "10th")]["notes"]
    eq = R(equal_length_ln(rice, 60.0))
    out["K6 chordstream -> equal-length LN: ln_release D"] = float(eq["D"][K["ln_release"]])
    sync = [(1000.0 + 200 * i, c, 1000.0 + 200 * i + 150) for i in range(200) for c in ((0, 2, 4), (1, 5), (3, 6))[i % 3]]
    out["K6 same start, same length: ln_release D"] = float(R(sync)["D"][K["ln_release"]])

    tech = by[("Regular Tech", "10th")]["notes"]
    a, b = R(tech), R([(t, 6 - c, e) for t, c, e in tech])
    out["K12 mirror: max |dD|, |dT|"] = [float(np.abs(a["D"] - b["D"]).max()), float(abs(a["T"] - b["T"]))]
    shuffled = list(tech)
    random.Random(7).shuffle(shuffled)
    c = read(action_table(sorted(shuffled, key=lambda x: (x[0], x[1]))), P)
    out["K12 note-line order: |dT|"] = float(abs(a["T"] - c["T"]))
    out["K12 rice chart: LN axes"] = [float(a["D"][K[k]]) for k in ("ln_general", "ln_tech", "ln_inverse", "ln_release")]

    one, two = R(rice), R(doubled(rice))
    out["length: doubled chart, T ratio vs 2^(1/p)"] = [float(two["T"] / one["T"]), float(2 ** (1 / P["p"]))]
    out["identity: |sum D^p - T^p| / T^p"] = float(abs((one["D"] ** P["p"]).sum() - one["T"] ** P["p"]) / one["T"] ** P["p"])
    return out


# --------------------------------------------------------------------------- report

def report(charts, P, chart_path=None):
    g = grids(charts, P)
    ab, rul = star_map(g["T"], g["sr"])
    star = lambda x: float(ab[0] * np.log(max(x, 1e-12)) + ab[1])  # noqa: E731
    all15 = list(range(15))
    res = dict(constants=P, star_map=dict(a=float(ab[0]), b=float(ab[1]), ruler_median_mae=rul))

    print("=== own-pool technique ladder (unit-partition accounting | full-weight union subset)")
    ladders = {}
    for gi, pool in enumerate(POOLS):
        row = {}
        for key, name in (("own", "partition"), ("own_u", "union")):
            v = g[key][gi]
            inv = [f"{TIERS[i]}>{TIERS[i + 1]}" for i in range(14) if v[i + 1] < v[i]]
            row[name] = dict(tau=float(stats.kendalltau(all15, v)[0]), rho=float(stats.spearmanr(all15, v)[0]),
                             adjacent_inversions=inv,
                             own_dominant=int(g["hit" if key == "own" else "hit_u"][gi].sum()))
        row["total_tau"] = float(stats.kendalltau(all15, g["T"][gi])[0])
        row["stars_own"] = [round(star(x), 2) for x in g["own"][gi]]
        row["stars_total"] = [round(star(x), 2) for x in g["T"][gi]]
        ladders[pool] = row
        a, b = row["partition"], row["union"]
        print(f"  {pool:15s} tau {a['tau']:.3f} rho {a['rho']:.3f} inv {len(a['adjacent_inversions'])} own {a['own_dominant']:2d}/15"
              f" | tau {b['tau']:.3f} inv {len(b['adjacent_inversions'])} own {b['own_dominant']:2d}/15"
              f" | total tau {row['total_tau']:.3f}   {' '.join(a['adjacent_inversions'])}")
    res["ladders"] = ladders
    summary = dict(
        mean_tau=float(np.mean([v["partition"]["tau"] for v in ladders.values()])),
        mean_rho=float(np.mean([v["partition"]["rho"] for v in ladders.values()])),
        adjacent_inversions=sum(len(v["partition"]["adjacent_inversions"]) for v in ladders.values()),
        own_dominant=int(g["hit"].sum()),
        union_mean_tau=float(np.mean([v["union"]["tau"] for v in ladders.values()])),
        union_adjacent_inversions=sum(len(v["union"]["adjacent_inversions"]) for v in ladders.values()),
        union_own_dominant=int(g["hit_u"].sum()),
        total_mean_tau=float(np.mean([v["total_tau"] for v in ladders.values()])))
    res["summary"] = summary
    print(f"  partition: mean tau {summary['mean_tau']:.4f} mean rho {summary['mean_rho']:.4f} adjacent inversions"
          f" {summary['adjacent_inversions']}/112 own-dominant {summary['own_dominant']}/120")
    print(f"  union    : mean tau {summary['union_mean_tau']:.4f} adjacent inversions"
          f" {summary['union_adjacent_inversions']}/112 own-dominant {summary['union_own_dominant']}/120")

    for key, name in (("arg", "partition"), ("arg_u", "union")):
        M = np.zeros((8, 8), int)
        for gi in range(8):
            for ti in range(15):
                M[gi, g[key][gi, ti]] += 1
        res[f"dominance_confusion_{name}"] = M.tolist()
        print(f"=== dominant technique, {name} (rows pool, cols {' '.join(a[:6] for a in AXES)})")
        for gi, pool in enumerate(POOLS):
            print(f"  {pool:15s} " + " ".join(f"{x:6d}" for x in M[gi]))

    st = ab[0] * np.log(g["T"]) + ab[1]
    diag = dict(mae=float(np.abs(st - g["sr"]).mean()), rho=float(stats.spearmanr(st.ravel(), g["sr"].ravel())[0]))
    for name, sl in (("regular", slice(0, 4)), ("ln", slice(4, 8))):
        diag[name] = dict(mae=float(np.abs(st[sl] - g["sr"][sl]).mean()), bias=float((st[sl] - g["sr"][sl]).mean()))
    res["official_sr_diagnostic"] = diag
    print(f"=== star = {ab[0]:.3f} ln(x) {ab[1]:+.3f}; ruler-median MAE {rul:.3f}")
    print(f"  vs per-chart official SR (diagnostic): MAE {diag['mae']:.3f} rho {diag['rho']:.4f}"
          f" | Regular MAE {diag['regular']['mae']:.3f} bias {diag['regular']['bias']:+.3f}"
          f" | LN MAE {diag['ln']['mae']:.3f} bias {diag['ln']['bias']:+.3f}")
    print("=== stars per ladder: total / own technique / official")
    for gi, pool in enumerate(POOLS):
        print(f"  {pool:15s} T   " + " ".join(f"{x:5.2f}" for x in ladders[pool]["stars_total"]))
        print(f"  {'':15s} own " + " ".join(f"{x:5.2f}" for x in ladders[pool]["stars_own"]))
        print(f"  {'':15s} sr  " + " ".join(f"{x:5.2f}" for x in g["sr"][gi]))

    res["invariants"] = invariants(charts, P, star)
    print("=== invariants")
    for k, v in res["invariants"].items():
        print(f"  {k}: {v}")

    if chart_path:
        notes = notes_of(Path(chart_path).read_text(encoding="utf-8"))
        r = read(action_table(notes), P)
        dur = (notes[-1][0] - notes[0][0]) / 1000.0
        one = dict(notes=len(notes), seconds=round(dur, 1), total_star=round(star(r["T"]), 2),
                   technique_stars={a: round(star(v), 2) for a, v in zip(AXES, r["D"]) if v > 0},
                   shares={a: round(float((v / r["T"]) ** P["p"]), 3) for a, v in zip(AXES, r["D"]) if v > 0})
        res["chart"] = one
        print(f"=== {chart_path}\n  {one}")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chart", help="also read one .osu file with the frozen constants")
    ap.add_argument("--calibrate", type=int, metavar="SEED", help="run the coarse search and print constants")
    ap.add_argument("--holdout", type=int, choices=(0, 1), help="calibrate on tiers of this parity, read the rest")
    ap.add_argument("--json", help="write the report to this path")
    args = ap.parse_args()
    charts = load_corpus()

    if args.calibrate is not None:
        P, fun, nfev = calibrate(charts, args.calibrate)
        print(json.dumps(dict(loss=fun, evaluations=nfev, constants={k: round(v, 4) for k, v in P.items()})))
        report(charts, P)
        return
    if args.holdout is not None:
        train = [i for i in range(15) if i % 2 == args.holdout]
        test = [i for i in range(15) if i % 2 != args.holdout]
        P, fun, nfev = calibrate(charts, seed=7, tiers=train, maxiter=30)
        own = grids(charts, P)["own"]
        conc = tot = 0
        for g in range(8):
            for i in test:
                for j in range(15):
                    if j == i or (j in test and j < i):
                        continue
                    tot += 1
                    conc += (own[g][i] - own[g][j]) * (i - j) > 0
        print(json.dumps(dict(train_tiers=[TIERS[i] for i in train], train_tau=float(np.mean(taus(own, train))),
                              heldout_tau=float(np.mean(taus(own, test))),
                              all_tau=float(np.mean(taus(own, list(range(15))))),
                              heldout_pair_concordance=conc / tot)))
        return

    res = report(charts, CONSTANTS, args.chart)
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
