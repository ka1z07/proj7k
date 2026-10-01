#!/usr/bin/env python3
"""
PROTOTYPE (throwaway) — the v0.1 spec, implemented literally, at its prior constants.

Source of truth: `osu-mania-7k-difficulty-spec.md` (v0.1 draft, 2026-10-01) and nothing else.
No constant, idea or helper is taken from the `proj7k` engine or from the other prototypes; the
only import from `proj7k` is the `.osu` parser and the corpus loader (the spec's §2.1 input is
"a list of objects", and parsing is outside it).

The question: the spec says (§16.1) "T1 has not been run on any pool". With every parameter at
its §12 prior and no §13 calibration, how far is the engine from its own acceptance tests?

DEFAULT IS NOW SPEC v0.2 (demand_mode="Eh", v_cap=40, delta_0_rel=0.5; see the spec's 修订记录).
`--spec v0.1` restores v0.1 exactly (`.results.v01.json`); the numbers in the VERDICT block below and in
the EXPERIMENT paragraphs are v0.1 unless a line says otherwise. v0.2 at the priors, uncalibrated, same
120 charts (`.results.json`): T1 9 inversions / tau .969 / 8 of 8 ladders within the SR-count floor
(v0.1: 22 / .905 / 3 of 8) / 2 strict (rc_speed, rc_stamina); T3, T4 (.0089), T6, T10 pass;
T5 random max 6.8, 20 ms jack max 5.9; T7 .93 (66% in band); T9 unchanged (Tech 15/15 and 15/15).
No held-out data: the three changes were picked on these charts.

VERDICT (v0.1) at the §12 priors, no calibration (frozen 120-chart corpus, `.results.json`):

  T1  FAIL   0/8 ladders strict, 22 adjacent inversions of 112, mean Kendall tau 0.905.
             Per ladder (tau / inversions): jack .867/3, tech .848/5, speed .924/3,
             stamina .981/1, ln_general .886/4, ln_tech .981/1, ln_inverse .848/3, ln_release .905/2.
  T3  PASS   mirror: max relative change 3.8e-15.
  T4  FAIL*  39 charts with N >= 20 N0; max |d ln D| = 0.0105 (LN Tech 9th), the only one > 0.01.
  T5  O(1/N) N * |d ln D|: random insertion median 3.7 / p95 8.4 / max 12.3; 20 ms jack insertion median
             5.4 / max 8.7. (Corrected: an earlier run let the random rice land inside a hold on its own
             column, an invalid chart, and read 4.5 / 9.7 / 13.3.) The spec gives no constant;
             1/(beta eps) = 4.2 is the hard-limit scale.
  T6  PASS   sum_k a = 1 to 2e-16, sum_k pi = 1 to 2e-15.
  T7  FAIL   rate exponent median 0.69 (p5–p95 0.31–0.89); 12% of (chart, rate) in [0.85, 1.15].
             Cause: Delta_0. The psi time scale is fixed in seconds, so below 40 ms intra-hand
             intervals get *easier* when sped up. Delta_0 -> 0 alone gives 0.97; delta_h, lambda_R,
             mu and eta each move it < 0.02.
  T9  DIAG   argmax pi is the own slot on 15/15 Regular Tech and 14/15 LN Tech charts, 0/15 for
             every other slot. Cause: q_C saturates. Mean M - 1 is 0.33–0.59 against m_0 = 0.5, so
             63–96% of events have q_C > 0.5, and Tech's attribution is w itself while every other
             RC/LN skill is multiplied by (1 - q_C).
  T10 PASS   [15] jack at 170 BPM 1/4 for 8 s after 90 s of 150 BPM 1/4 roll: pi_jack 0.51 against
             coverage 0.17. (A first construction that alternated [12]/[56] was not a jack; it read
             rc_speed.)
  T2, T8 not run (need §13 calibration / player judgements). Stars are null (§10 anchors absent).

EXPERIMENT v_cap=40 (Hz, on top of "Eh"): T1 unchanged (8), T5 jack-insertion max 4.9, random max 11.7;
v_cap 30 gives 10 inversions, v_cap 22 gives 14. EXPERIMENT delta_0_rel=0.5 on top: T7 .93 (66% in
band), T1 9 inversions, T5 random max 6.8; under the spec's d_i the same switch costs +5 inversions.

EXPERIMENT demand_mode="Eh" (default stays "spec"): d_i = sqrt(E^h_i), the §6.1 hand accumulator
(RMS of v over ~4 s) used directly as the §7 demand. Chosen from 9 variants (3 accumulators x 3
quantiles) by own-pool inversions on the same 120 charts, so selection is NOT held out. At the §12
priors, no calibration:
  T1 22 -> 8 inversions, tau .905 -> .974, 2/8 strict ladders (rc_speed, rc_stamina)
  T3 pass | T4 pass (max .0081, was .0105) | T6 pass | T10 pass (jack pi .40)
  T5 (valid insertions): random max 11.7 (spec 12.3); 20 ms jack insertion max 26.8 (spec 8.7). An inserted
     event with a very short gap enters ~60 downstream events through E^h. (An earlier read of
     p95 62 / max 200 used invalid insertions inside a hold.)
  T7 .745 (was .69) and T9 (Tech slots 15/15, 15/15) unchanged: those are Delta_0 and q_C, not demand.

Reading of the spec where it leaves a choice open (each is a decision for the spec owner; the
numbers in the header of `.results.json` are under exactly these readings):

  R1  §2.4  A row opens at its first event; later events within 1 ms of it join it. Every event's
            time is snapped to its row time T_r, so "simultaneous" is exact everywhere after §2.4.
  R2  §2.6  h_i counts held columns across both hands (the spec says "columns", not "fingers of
            the hand"); "held" is strict on both ends: head < T_r < tail.
  R3  §4.2  t_g includes events in the current row, so a chord partner gives psi(0) = 0 and also
            masks that finger's earlier events. Same for t_hbar in §4.3.
  R4  §5.1  A finger with both a press and a release in one row has symbol state "press".
  R5  §5.2  Row 0 has no interval, so it is neither scored (U = 0) nor in later rows' history.
            The sum is exact, truncated at 12 T_c (weight < 1e-5).
  R6  §6.1  A finger with two events in one row updates twice; the second update has dt = 0 and
            so changes nothing. The global accumulator reads a hand's post-update value if that
            hand updated in this row, its decayed value otherwise.
  R7  §3.2  Bisection runs to 1e-9 in ln(theta) (the spec's 1e-4 is a floor); a root outside
            [theta_min, theta_max] is clamped to the bound.
  R8  §10   The anchors are not supplied, so `stars` is null everywhere. D is in Hz.
  R9  §1.2  Pool "Regular Stream" is the slot of rc_stamina.
  R10 §6.1  An accumulator's first update has dt = inf (phi = 0): the spec gives no "previous
            update" time, and the chart's time origin is arbitrary (it would break rate and
            offset invariance). E starts at 0, so this only sets how much of the first input lands.

Usage:
  PYTHONPATH=src python3 prototypes/prototype_spec_v01_engine.py                # all tests
  PYTHONPATH=src python3 prototypes/prototype_spec_v01_engine.py --chart X.osu  # one chart, §9.6 JSON
"""

import argparse
import json
from pathlib import Path
import random
import sys
import time

import numpy as np
from scipy import special, stats

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from proj7k.assets import load_corpus_fixture  # noqa: E402
from proj7k.parser import NoteType, parse_osu_7k  # noqa: E402

TIERS = ["0th", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th",
         "Gamma", "Azimuth", "Zenith", "Stellium"]
SKILLS = ["rc_jack", "rc_tech", "rc_speed", "rc_stamina", "ln_general", "ln_tech", "ln_inverse", "ln_release"]
POOL_OF = {"Regular Jack": "rc_jack", "Regular Tech": "rc_tech", "Regular Speed": "rc_speed",
           "Regular Stream": "rc_stamina", "LN General": "ln_general", "LN Tech": "ln_tech",
           "LN Inverse": "ln_inverse", "LN Release": "ln_release"}

# --------------------------------------------------------------------------- §12 parameters

P0 = dict(
    eps_rc=0.04, eps_ln=0.05,          # §3.3
    beta=6.0, N0=150.0,                # §3.1, §3.2
    l_min=0.100, delta_h=0.050, delta_floor=0.020, delta_0=0.040,
    k_ring_mid=1.0, k_mid_idx=0.7, k_ring_idx=0.5, k_thumb_idx=0.5, k_thumb_other=0.3, k_scale=1.0,
    k_cross=0.2, chi_0=0.5, w_rel=0.7,
    tau_f=0.5, tau_h=4.0, tau_g=40.0, eta_f=0.3, eta_h=0.3, eta_g=0.2, gamma=2.0,
    T_c=4.0, U_max=4.0, alpha=1.0, alpha_0=1.0, alpha_r=1.0, b=0.15,
    mu_p=0.1, mu_r=0.1, nu=0.1, T_w=0.5, lambda_R=0.1,
    r_lo=0.25, r_hi=0.55, phi_ref=0.5, m_0=0.5, h_ref=3.0,
    theta_min=0.01, theta_max=1000.0,
    delta_0_rel=0.5,                   # EXPERIMENT: Delta_0 = c * median row interval over the past 2 s
    v_cap=40.0,                        # EXPERIMENT: v_i <- min(v_i, v_cap) before it enters §6.1 and §7
    demand_mode="Eh",                  # EXPERIMENT: "Eh" d_i = sqrt(E^h_i); "EhM" d_i = sqrt(E^h_i) * M_i
    sym_mode="spec",                   # EXPERIMENT: "press_only" drops the release state from the §5.1 symbol
    U_base="none",                     # EXPERIMENT: "median" subtracts the chart's own median U_pat (clip at 0)
    psi_n=2,                           # EXPERIMENT: psi = dt^(n-1) / (dt^n + Delta_0^n); 2 is the spec
    qC_mode="spec", m_lo=0.0, m_hi=0.5,  # EXPERIMENT: "ss" = smoothstep((M-1 - m_lo) / (m_hi - m_lo))
    star_a=None, star_b=None,          # §10: anchors not supplied
)

#: Restores the v0.1 spec exactly (the three v0.2 changes off). `--spec v0.1` applies it.
V01 = dict(demand_mode="spec", v_cap=None, delta_0_rel=None)

FINGER = ["ring", "mid", "idx", "thumb", "idx", "mid", "ring"]  # §2.5
SIDE = [0, 0, 0, None, 1, 1, 1]                                  # 0 = L, 1 = R; thumb by h_T


def kappa_matrix(P, thumb):
    """§4.2: kappa[f, g] for f, g on the same hand, 0 across hands and on the diagonal."""
    pair = {frozenset(("ring", "mid")): P["k_ring_mid"], frozenset(("mid", "idx")): P["k_mid_idx"],
            frozenset(("ring", "idx")): P["k_ring_idx"], frozenset(("thumb", "idx")): P["k_thumb_idx"],
            frozenset(("thumb", "mid")): P["k_thumb_other"], frozenset(("thumb", "ring")): P["k_thumb_other"]}
    hand = hand_of(thumb)
    K = np.zeros((7, 7))
    for f in range(7):
        for g in range(7):
            if f != g and hand[f] == hand[g]:
                K[f, g] = P["k_scale"] * pair[frozenset((FINGER[f], FINGER[g]))]
    return K


def hand_of(thumb):
    return np.array([thumb if s is None else s for s in SIDE])


# --------------------------------------------------------------------------- §2 preprocessing

def notes_of(content):
    """A chart as (column, head_s, tail_s | None), §2.1."""
    out = []
    for ho in parse_osu_7k(content).hit_objects:
        e = ho.end_time if ho.note_type == NoteType.LN and ho.end_time is not None else None
        out.append((int(ho.column), ho.time / 1000.0, None if e is None else e / 1000.0))
    return out


def preprocess(notes, P):
    """§2.2–2.6 plus everything in §4–5 that does not depend on the thumb's hand."""
    objs = []
    for c, t, e in notes:
        if e is not None and e - t < P["l_min"]:   # §2.2
            e = None
        objs.append((c, t, e))
    objs.sort(key=lambda o: (o[1], o[0]))

    raw = []  # (time, col, is_release, obj)
    for k, (c, t, e) in enumerate(objs):
        raw.append((t, c, 0, k))
        if e is not None:
            raw.append((e, c, 1, k))
    raw.sort(key=lambda x: (x[0], x[2], x[1]))

    # §2.4 rows (R1)
    row_of, T = [], []
    for t, *_ in raw:
        if not T or t - T[-1] > 0.001 + 1e-12:
            T.append(t)
        row_of.append(len(T) - 1)
    T = np.array(T)
    n_rows, n = len(T), len(raw)
    ev_row = np.array(row_of)
    ev_col = np.array([x[1] for x in raw])
    ev_rel = np.array([x[2] for x in raw], dtype=bool)
    ev_obj = np.array([x[3] for x in raw])
    ev_t = T[ev_row]
    is_ln = np.array([objs[k][2] is not None for k in ev_obj])

    # snapped object head/tail rows
    head_row = np.full(len(objs), -1)
    tail_row = np.full(len(objs), -1)
    for i in range(n):
        (tail_row if ev_rel[i] else head_row)[ev_obj[i]] = ev_row[i]
    ell = np.where(is_ln, T[tail_row[ev_obj]] - T[head_row[ev_obj]], 0.0)

    # §2.6 held[r, c]: an LN on c with head row < r < tail row (R2)
    diff = np.zeros((n_rows + 1, 7), dtype=int)
    for k, (c, t, e) in enumerate(objs):
        if e is not None and tail_row[k] - head_row[k] > 1:
            diff[head_row[k] + 1, c] += 1
            diff[tail_row[k], c] -= 1
    held = np.cumsum(diff, axis=0)[:n_rows] > 0
    h = held.sum(1)[ev_row] - held[ev_row, ev_col]

    # §4.1 j, and "same-column predecessor is an LN" for §8.1 q_inv
    j = np.zeros(n)
    pred_ln = np.zeros(n, dtype=bool)
    last_obj = [None] * 7
    for i in range(n):
        if ev_rel[i]:
            j[i] = 1.0 / ell[i]
            continue
        c, k = ev_col[i], ev_obj[i]
        prev = last_obj[c]
        if prev is not None:
            pc, pt, pe = objs[prev]
            e_prime = T[tail_row[prev]] if pe is not None else T[head_row[prev]] + P["delta_h"]
            j[i] = 1.0 / max(P["delta_floor"], ev_t[i] - e_prime + P["delta_h"])
            pred_ln[i] = pe is not None
        last_obj[c] = k

    # last[r, c]: time of the latest event on column c at or before row r (R3)
    has = np.zeros((n_rows, 7), dtype=bool)
    has[ev_row, ev_col] = True
    idx = np.where(has, np.arange(n_rows)[:, None], -1)
    idx = np.maximum.accumulate(idx, axis=0)
    last = np.where(idx >= 0, T[np.clip(idx, 0, None)], -np.inf)

    # per row: per column state for §5.1 (0 none, 1 press, 2 release; R4)
    state = np.zeros((n_rows, 7), dtype=int)
    for i in range(n):
        s = 2 if ev_rel[i] else 1
        if state[ev_row[i], ev_col[i]] != 1:
            state[ev_row[i], ev_col[i]] = s

    return dict(n=n, n_rows=n_rows, T=T, row=ev_row, col=ev_col, rel=ev_rel, t=ev_t, is_ln=is_ln,
                ell=ell, held=held, h=h, j=j, pred_ln=pred_ln, last=last, has=has, state=state,
                U_rhy=rhythm_surprise(T, P))


def psi(dt, P, d0=None):
    """§4.2; psi = 0 where there is no prior event (dt = inf) or the events coincide."""
    n = P["psi_n"]
    d0 = P["delta_0"] if d0 is None else d0
    with np.errstate(invalid="ignore", over="ignore"):
        out = dt ** (n - 1) / (dt ** n + d0 ** n)
    return np.where(np.isfinite(dt), out, 0.0)


# --------------------------------------------------------------------------- §5 cognition

def rhythm_surprise(T, P):
    """§5.2, per row (R5)."""
    n = len(T)
    U = np.zeros(n)
    if n < 2:
        return U
    ell = np.full(n, np.nan)
    ell[1:] = np.log2(np.diff(T))
    floor = P["alpha_r"] * 2.0 ** (-P["U_max"])
    lo = 1
    for r in range(1, n):
        while lo < r and T[r] - T[lo] > 12 * P["T_c"]:
            lo += 1
        if lo < r:
            w = np.exp(-(T[r] - T[lo:r]) / P["T_c"])
            k = np.exp(-((ell[r] - ell[lo:r]) ** 2) / (2 * P["b"] ** 2))
            rho = (np.dot(w, k) + floor) / (w.sum() + P["alpha_r"])
        else:
            rho = floor / P["alpha_r"]
        U[r] = min(P["U_max"], -np.log2(rho))
    return U


def pattern_surprise(pre, hand, P):
    """§5.1: U_pat[r, h] (nan where hand h has no event in row r)."""
    T, state = pre["T"], pre["state"]
    out = np.full((pre["n_rows"], 2), np.nan)
    for hh in (0, 1):
        cols = np.where(hand == hh)[0]
        nh = len(cols)
        base = 2 if P["sym_mode"] == "press_only" else 3
        Ksym = base ** nh - 1
        pw = base ** np.arange(nh)
        st = state[:, cols]
        if P["sym_mode"] == "press_only":
            st = np.where(st == 2, 0, st)
        sym = st @ pw
        n0 = np.zeros(3 ** nh)
        n1 = np.zeros((3 ** nh, 3 ** nh))
        prev, t_prev = None, None
        for r in np.where(sym > 0)[0]:
            s = sym[r]
            if t_prev is not None:  # decay before use
                f = np.exp(-(T[r] - t_prev) / P["T_c"])
                n0 *= f
                n1 *= f
            p0 = (n0[s] + P["alpha_0"] / Ksym) / (n0.sum() + P["alpha_0"])
            if prev is None:
                p = p0
            else:
                p = (n1[prev, s] + P["alpha"] * p0) / (n1[prev].sum() + P["alpha"])
            out[r, hh] = min(P["U_max"], -np.log2(p))
            n0[s] += 1
            if prev is not None:
                n1[prev, s] += 1
            prev, t_prev = s, T[r]
    return out


# --------------------------------------------------------------------------- one thumb assignment

def layers(pre, thumb, P):
    """§4, §5, §6.1 for one thumb hand. Everything here is independent of theta."""
    hand = hand_of(thumb)
    K = kappa_matrix(P, thumb)
    row, col, rel, T = pre["row"], pre["col"], pre["rel"], pre["T"]
    tr = T[row]

    # §4.2 / §4.3
    d0 = None
    if P["delta_0_rel"] is not None:
        iv = np.diff(T, prepend=T[0] - 1.0)
        lo_r = np.searchsorted(T, T - 2.0, side="left")
        d0 = np.array([P["delta_0_rel"] * np.median(iv[max(lo_r[r], 1):r + 1]) if r >= 1 else P["delta_0"]
                       for r in range(len(T))])
    Psi = psi(T[:, None] - pre["last"], P, None if d0 is None else d0[:, None])   # [rows, 7]
    x = (K[col] * Psi[row]).sum(1)
    last_hand = np.stack([pre["last"][:, hand == hh].max(1) for hh in (0, 1)], 1)
    o = P["k_cross"] * psi(tr - last_hand[row, 1 - hand[col]], P, None if d0 is None else d0[row])
    # §4.4
    c = 1.0 + P["chi_0"] * (K[col] * pre["held"][row]).sum(1)
    # §4.5
    omega = np.where(rel, P["w_rel"], 1.0)
    j = pre["j"]
    v = omega * (j + x + o) * c
    if P["v_cap"] is not None:
        v = np.minimum(v, P["v_cap"])

    # §5.1, §5.3, §5.4
    Upat_rh = pattern_surprise(pre, hand, P)
    U_pat = np.nan_to_num(Upat_rh[row, hand[col]])
    if P["U_base"] == "median":
        U_pat = np.maximum(0.0, U_pat - np.median(U_pat))
    U_rhy = pre["U_rhy"][row]
    M = 1.0 + P["mu_p"] * U_pat + P["mu_r"] * U_rhy + P["nu"] * np.maximum(0, pre["h"] - 1)
    S = np.nansum(Upat_rh, 1)
    cum = np.concatenate([[0.0], np.cumsum(S)])
    lo = np.searchsorted(T, tr, side="left")
    hi = np.searchsorted(T, tr + P["T_w"], side="left")
    I = (cum[hi] - cum[lo]) / P["T_w"]
    R = P["lambda_R"] * I

    # §6.1
    g = P["gamma"]
    vg = v ** g
    Ef, Eh, Eg = np.zeros(7), np.zeros(2), 0.0
    tf, th, tg = np.full(7, np.nan), np.full(2, np.nan), np.nan
    E_f, E_h, E_g = np.zeros(pre["n"]), np.zeros(pre["n"]), np.zeros(pre["n"])
    order = np.argsort(row, kind="stable")
    bounds = np.searchsorted(row[order], np.arange(pre["n_rows"] + 1))

    def step(E, t_last, t_now, tau, inp):
        if np.isnan(t_last):
            phi = 0.0  # R10: the first update has dt = inf
        else:
            phi = np.exp(-(t_now - t_last) / tau)
        return E * phi + (1 - phi) * inp

    for r in range(pre["n_rows"]):
        evs = order[bounds[r]:bounds[r + 1]]
        Tr = T[r]
        hand_in = [0.0, 0.0]
        hand_hit = [False, False]
        for i in evs:
            f = col[i]
            Ef[f] = step(Ef[f], tf[f], Tr, P["tau_f"], vg[i])
            tf[f] = Tr
            hh = hand[f]
            hand_in[hh] += vg[i]
            hand_hit[hh] = True
        cur = [0.0, 0.0]
        for hh in (0, 1):
            if hand_hit[hh]:
                Eh[hh] = step(Eh[hh], th[hh], Tr, P["tau_h"], hand_in[hh])
                th[hh] = Tr
                cur[hh] = Eh[hh]
            else:
                cur[hh] = Eh[hh] * (np.exp(-(Tr - th[hh]) / P["tau_h"]) if not np.isnan(th[hh]) else 1.0)
        Eg = step(Eg, tg, Tr, P["tau_g"], 0.5 * (cur[0] + cur[1]))
        tg = Tr
        for i in evs:
            E_f[i], E_h[i], E_g[i] = Ef[col[i]], Eh[hand[col[i]]], Eg

    return dict(thumb=thumb, j=j, x=x, o=o, c=c, v=v, U_pat=U_pat, U_rhy=U_rhy, M=M, R=R,
                E_f=E_f, E_h=E_h, E_g=E_g)


# --------------------------------------------------------------------------- §3, §6.2, §7

def demand(L, theta, P):
    """§7 d_i(theta) for scalar theta."""
    if P["demand_mode"] == "Eh":
        return np.sqrt(L["E_h"])
    if P["demand_mode"] == "EhM":
        return np.sqrt(L["E_h"]) * L["M"]
    tg = theta ** P["gamma"]
    Phi = (P["eta_f"] * L["E_f"] / (L["E_f"] + tg) + P["eta_h"] * L["E_h"] / (L["E_h"] + tg)
           + P["eta_g"] * L["E_g"] / (L["E_g"] + tg))
    return np.hypot(L["v"] * L["M"] * (1.0 + Phi), L["R"])


def loss(d, theta, P):
    """§3.1 p_i(theta); d = 0 gives 0."""
    with np.errstate(divide="ignore"):
        return special.expit(P["beta"] * (np.log(d) - np.log(theta)))


def solve(L, w, eps, P):
    """§3.2, bisection on ln(theta) (R7)."""
    W = w.sum()
    d_pos = demand(L, 1.0, P) > 0
    W_plus = w[d_pos].sum()
    target = eps * (W + P["N0"])
    if W_plus <= target:
        return 0.0
    lo, hi = np.log(P["theta_min"]), np.log(P["theta_max"])

    def g(lt):
        th = np.exp(lt)
        return np.dot(w, loss(demand(L, th, P), th, P)) - target

    if g(lo) <= 0:
        return P["theta_min"]
    if g(hi) >= 0:
        return P["theta_max"]
    while hi - lo > 1e-9:
        mid = 0.5 * (lo + hi)
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    return float(np.exp(0.5 * (lo + hi)))


# --------------------------------------------------------------------------- §8, §9

def smoothstep(z):
    z = np.clip(z, 0.0, 1.0)
    return z * z * (3 - 2 * z)


def features(pre, L, D, P):
    """§8.1 at theta = D."""
    jxo = L["j"] + L["x"] + L["o"]
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(jxo > 0, L["j"] / jxo, 0.0)
        lam = np.maximum(pre["is_ln"].astype(float), np.minimum(1.0, pre["h"] / 2.0))
        qJ = smoothstep((r - P["r_lo"]) / (P["r_hi"] - P["r_lo"]))
        Dg = D ** P["gamma"]
        sat = lambda E: E / (E + Dg)  # noqa: E731
        qPhi = np.minimum(1.0, (P["eta_h"] * sat(L["E_h"]) + P["eta_g"] * sat(L["E_g"]))
                          / (P["eta_h"] + P["eta_g"]) / P["phi_ref"])
        d = demand(L, D, P)
        read = np.where(d > 0, 1.0 - L["R"] ** 2 / np.where(d > 0, d, 1.0) ** 2, 1.0)
        if P["qC_mode"] == "ss":
            cM = smoothstep((L["M"] - 1.0 - P["m_lo"]) / (P["m_hi"] - P["m_lo"]))
        else:
            cM = np.minimum(1.0, (L["M"] - 1.0) / P["m_0"])
        qC = 1.0 - (1.0 - cM) * read
        qinv = np.where(~pre["rel"], pre["pred_ln"] * r * np.minimum(1.0, pre["h"] / P["h_ref"]), 0.0)
        den = jxo * L["c"]
        qrel = np.where(pre["rel"] & (den > 0), 1.0 - L["j"] / np.where(den > 0, den, 1.0), 0.0)
    return dict(lam=lam, r=r, qJ=qJ, qPhi=qPhi, qC=qC, qinv=qinv, qrel=qrel, d=d)


def membership(F):
    """§8.2: (w [n, 8], a [n, 8]) in SKILLS order."""
    lam, qJ, qPhi, qC, qinv, qrel = F["lam"], F["qJ"], F["qPhi"], F["qC"], F["qinv"], F["qrel"]
    rc = 1 - lam
    w = np.stack([rc * qJ, rc * qC, rc * (1 - qJ) * (1 - qPhi), rc * (1 - qJ) * qPhi,
                  lam, lam * qC, lam * qinv, lam * qrel], 1)
    a = np.stack([(1 - qC) * w[:, 0], w[:, 1], (1 - qC) * w[:, 2], (1 - qC) * w[:, 3],
                  lam * (1 - qC) * (1 - qinv - qrel), w[:, 5], (1 - qC) * w[:, 6], (1 - qC) * w[:, 7]], 1)
    return w, a


def stars(D, P):
    if P["star_a"] is None or P["star_b"] is None:
        return None
    return P["star_a"] * D ** P["star_b"]


def evaluate(notes, P=P0, detail=False, pre=None):
    """§11. `pre` lets a caller reuse `preprocess(notes, P)` when no S-class parameter changed."""
    pre = preprocess(notes, P) if pre is None else pre
    if pre["n"] == 0:
        raise ValueError("empty chart")
    lam_mean = np.maximum(pre["is_ln"].astype(float), np.minimum(1.0, pre["h"] / 2.0)).mean()
    eps_total = P["eps_rc"] + 0.01 * lam_mean
    ones = np.ones(pre["n"])
    best = None
    for thumb in (0, 1):
        L = layers(pre, thumb, P)
        D = solve(L, ones, eps_total, P)
        if best is None or D < best["D"]:
            best = dict(D=D, L=L, thumb=thumb)
    L, D = best["L"], best["D"]
    F = features(pre, L, D, P)
    w, a = membership(F)
    assert np.all(np.abs(a.sum(1) - 1.0) < 1e-9), "attribution must sum to 1 (§8.2)"
    Dk = {k: solve(L, w[:, n], P["eps_rc"] if k.startswith("rc") else P["eps_ln"], P)
          for n, k in enumerate(SKILLS)}
    Ck = w.sum(0) / pre["n"]
    p = loss(F["d"], D, P)
    s = p * (1 - p)
    s = s / s.sum()
    pi = a.T @ s
    assert abs(pi.sum() - 1.0) < 1e-9, "dominance must sum to 1 (§9.4)"
    out = {
        "thumb_hand": "LR"[best["thumb"]],
        "total": {"D": D, "stars": stars(D, P)},
        "skills": {k: {"D": Dk[k], "stars": stars(Dk[k], P), "coverage": float(Ck[n]), "dominance": float(pi[n])}
                   for n, k in enumerate(SKILLS)},
        "dominant_skill": SKILLS[int(np.argmax(pi))],
    }
    if detail:
        out["_n_events"] = pre["n"]
        out["_attr_max_err"] = float(np.abs(a.sum(1) - 1.0).max())
        out["_pi_sum_err"] = float(abs(pi.sum() - 1.0))
        out["_eps_total"] = eps_total
    return out


# --------------------------------------------------------------------------- corpus and tests (§14)

def load_corpus():
    manifest = json.loads((REPO / "docs/research/structured_index.json").read_text(encoding="utf-8"))
    corpus = load_corpus_fixture(REPO / "tests/fixtures/benchmark_corpus.json.gz")
    charts = []
    for pool in POOL_OF:
        for ti, tier in enumerate(TIERS):
            e = manifest[pool][tier]
            charts.append(dict(pool=pool, tier=tier, ti=ti, id=e["id"], song=e["song"],
                               notes=notes_of(corpus[int(e["id"])])))
    return charts


def mirror(notes):
    return [(6 - c, t, e) for c, t, e in notes]


def rate(notes, r):
    return [(c, t / r, None if e is None else e / r) for c, t, e in notes]


def concat_self(notes, rest):
    end = max(e if e is not None else t for _, t, e in notes)
    t0 = min(t for _, t, _ in notes)
    shift = end + rest - t0
    return notes + [(c, t + shift, None if e is None else e + shift) for c, t, e in notes]


def sr_floor():
    """Per skill: (inversions of the community SR on that slot's pool, set of exempt adjacent pairs).
    Read ONLY as the pool's noise floor for the T1 tolerance rule (spec v0.2 §14); it is never in
    an objective or a fit."""
    man = json.loads((REPO / "docs/research/structured_index.json").read_text(encoding="utf-8"))
    out = {}
    for pool, k in POOL_OF.items():
        sr = [man[pool][t]["sr"] for t in TIERS]
        out[k] = {i for i in range(14) if sr[i + 1] <= sr[i]}
    return out


def t1_ladders(results):
    out, floor = {}, sr_floor()
    for pool, k in POOL_OF.items():
        v = [results[(pool, t)]["skills"][k]["D"] for t in TIERS]
        lv = np.log(np.maximum(v, 1e-12))
        gaps = np.diff(lv)
        bad = [i for i in range(14) if gaps[i] <= 0]
        inv = [f"{TIERS[i]}>{TIERS[i + 1]}" for i in bad]
        out[k] = dict(D=[round(x, 3) for x in v], kendall_tau=round(stats.kendalltau(range(15), v)[0], 3),
                      min_ln_gap=round(float(gaps.min()), 4), inversions=inv, strict=not inv,
                      sr_floor_inversions=len(floor[k]), within_count_floor=len(bad) <= len(floor[k]),
                      inversions_on_pairs_sr_does_not_invert=[f"{TIERS[i]}>{TIERS[i + 1]}" for i in bad if i not in floor[k]])
    return out


def t10_chart():
    """Long easy stream, then a short hard jack section (§14 T10)."""
    notes, t = [], 0.0
    roll = [0, 2, 4, 6, 1, 3, 5]
    beat = 60.0 / 150
    for k in range(int(90 / (beat / 4))):           # 90 s of 1/4 single-note roll at 150 BPM
        notes.append((roll[k % 7], t, None))
        t += beat / 4
    t += 1.0
    beat = 60.0 / 170
    for k in range(int(8 / (beat / 4))):            # 8 s of 1/4 [15] jack at 170 BPM (88 ms per hit)
        for c in (1, 5):
            notes.append((c, t, None))
        t += beat / 4
    return notes


def valid_insert(notes, rnd, span):
    """A random rice that does not sit inside, or within 5 ms of, an object on its own column."""
    while True:
        c, t = rnd.randrange(7), rnd.uniform(*span)
        if all(not (cc == c and t0 - 0.005 <= t <= (e if e is not None else t0) + 0.005) for cc, t0, e in notes):
            return (c, t, None)


def t5_report(charts, res, rnd):
    t5 = {"random": [], "adversarial": []}
    for ch in charts:
        a = res[(ch["pool"], ch["tier"])]
        N = a["_n_events"]
        notes = ch["notes"]
        span = (min(t for _, t, _ in notes), max(t for _, t, _ in notes))
        for kind in t5:
            if kind == "random":
                ins = valid_insert(notes, rnd, span)
            else:
                c, t, e = rnd.choice([n for n in notes if n[2] is None] or notes)
                ins = (c, (e if e is not None else t) + 0.020, None)
            b = evaluate(notes + [ins])
            t5[kind].append(N * abs(np.log(b["total"]["D"] / a["total"]["D"])))
    return {k: dict(N_times_abs_dlnD_median=round(float(np.median(v)), 3),
                    N_times_abs_dlnD_p95=round(float(np.percentile(v, 95)), 3),
                    N_times_abs_dlnD_max=round(float(np.max(v)), 3)) for k, v in t5.items()}


def run_all(seed=0):
    rnd = random.Random(seed)
    charts = load_corpus()
    t0 = time.time()
    res = {}
    for ch in charts:
        res[(ch["pool"], ch["tier"])] = evaluate(ch["notes"], detail=True)
    t_eval = time.time() - t0
    report = {"runtime_s_120_charts": round(t_eval, 1)}

    report["T1"] = t1_ladders(res)
    report["T1_summary"] = dict(
        ladders_within_sr_count_floor=sum(v["within_count_floor"] for v in report["T1"].values()),
        inversions_on_pairs_sr_does_not_invert=sum(len(v["inversions_on_pairs_sr_does_not_invert"]) for v in report["T1"].values()),
        sr_floor_inversions=sum(v["sr_floor_inversions"] for v in report["T1"].values()),
        ladders_strict=sum(v["strict"] for v in report["T1"].values()),
        adjacent_inversions=sum(len(v["inversions"]) for v in report["T1"].values()),
        mean_kendall_tau=round(float(np.mean([v["kendall_tau"] for v in report["T1"].values()])), 3))
    report["T2"] = "not run: needs the §13 calibration, which this prototype does not perform"

    # T3 mirror
    worst = 0.0
    for ch in charts:
        a, b = res[(ch["pool"], ch["tier"])], evaluate(mirror(ch["notes"]))
        vals = [(a["total"]["D"], b["total"]["D"])]
        vals += [(a["skills"][k]["D"], b["skills"][k]["D"]) for k in SKILLS]
        vals += [(a["skills"][k]["dominance"], b["skills"][k]["dominance"]) for k in SKILLS]
        for x, y in vals:
            worst = max(worst, abs(x - y) / max(abs(x), 1e-12) if x else abs(y))
    report["T3"] = dict(max_rel_change=worst, pass_=worst <= 1e-6)

    # T4 self-concatenation, rest 5 tau_g
    t4 = []
    for ch in charts:
        a = res[(ch["pool"], ch["tier"])]
        if a["_n_events"] < 20 * P0["N0"]:
            continue
        b = evaluate(concat_self(ch["notes"], 5 * P0["tau_g"]))
        t4.append((f'{ch["pool"]} {ch["tier"]}', abs(np.log(b["total"]["D"] / a["total"]["D"]))))
    t4.sort(key=lambda x: -x[1])
    report["T4"] = dict(charts=len(t4), max_abs_dlnD=round(t4[0][1], 5) if t4 else None,
                        worst=[(n, round(v, 5)) for n, v in t4[:5]],
                        n_fail=sum(v > 0.01 for _, v in t4), pass_=all(v <= 0.01 for _, v in t4))

    # T5 single-point insertion
    report["T5"] = t5_report(charts, res, rnd)

    # T6 conservation (asserted in evaluate; report the worst errors)
    report["T6"] = dict(max_attr_err=max(r["_attr_max_err"] for r in res.values()),
                        max_pi_err=max(r["_pi_sum_err"] for r in res.values()))

    # T7 rate scaling exponent
    exps = []
    for ch in charts:
        base = res[(ch["pool"], ch["tier"])]["total"]["D"]
        for r in (0.75, 1.25, 1.5):
            exps.append(np.log(evaluate(rate(ch["notes"], r))["total"]["D"] / base) / np.log(r))
    exps = np.array(exps)
    report["T7"] = dict(exponent_median=round(float(np.median(exps)), 3),
                        exponent_p5_p95=[round(float(np.percentile(exps, 5)), 3), round(float(np.percentile(exps, 95)), 3)],
                        share_in_0_85_1_15=round(float(np.mean((exps >= 0.85) & (exps <= 1.15))), 3))
    report["T8"] = "not run: needs player pairwise judgements"

    # T9 slot purity
    t9 = {}
    for pool, k in POOL_OF.items():
        own = [res[(pool, t)]["skills"][k]["dominance"] for t in TIERS]
        dom = [res[(pool, t)]["dominant_skill"] for t in TIERS]
        t9[k] = dict(own_pi=[round(x, 3) for x in own], dominant_is_own=sum(d == k for d in dom),
                     dominant=dict(zip(*np.unique(dom, return_counts=True))) if dom else {})
        t9[k]["dominant"] = {str(a): int(b) for a, b in t9[k]["dominant"].items()}
    report["T9"] = t9

    # T10
    r10 = evaluate(t10_chart(), detail=True)
    report["T10"] = dict(dominant=r10["dominant_skill"], pass_=r10["dominant_skill"] == "rc_jack",
                         pi={k: round(r10["skills"][k]["dominance"], 3) for k in SKILLS},
                         D={k: round(r10["skills"][k]["D"], 3) for k in SKILLS},
                         coverage={k: round(r10["skills"][k]["coverage"], 3) for k in SKILLS})

    # diagnostics: the total D ladder per pool, and the per-chart outputs
    report["diag_total_D_ladder"] = {
        k: dict(D=[round(res[(pool, t)]["total"]["D"], 3) for t in TIERS],
                kendall_tau=round(stats.kendalltau(range(15), [res[(pool, t)]["total"]["D"] for t in TIERS])[0], 3))
        for pool, k in POOL_OF.items()}
    report["charts"] = {f"{p} {t}": {kk: vv for kk, vv in r.items() if not kk.startswith("_")}
                        for (p, t), r in res.items()}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chart", help="evaluate one .osu file and print the §9.6 JSON")
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--spec", choices=("v0.2", "v0.1"), default="v0.2")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.spec == "v0.1":
        P0.update(V01)
    if args.out is None:
        args.out = str(Path(__file__).with_suffix(".results.json" if args.spec == "v0.2" else ".results.v01.json"))
    if args.chart:
        notes = rate(notes_of(Path(args.chart).read_text(encoding="utf-8")), args.rate)
        print(json.dumps(evaluate(notes), indent=2, ensure_ascii=False))
        return
    report = run_all()
    Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    brief = {k: v for k, v in report.items() if k not in ("charts", "T1", "T9", "diag_total_D_ladder")}
    brief["T1"] = {k: dict(tau=v["kendall_tau"], min_gap=v["min_ln_gap"], inv=len(v["inversions"]),
                           sr_floor=v["sr_floor_inversions"])
                   for k, v in report["T1"].items()}
    brief["T9_dominant_is_own"] = {k: v["dominant_is_own"] for k, v in report["T9"].items()}
    print(json.dumps(brief, indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
