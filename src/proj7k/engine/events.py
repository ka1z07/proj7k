"""
Input preprocessing (spec §2): a chart as notes becomes rows and events.

Everything here is independent of which hand the thumb is on and of the loss threshold, so it is
computed once per chart. The only imports from the rest of the project are the `.osu` parser; the
spec's §2.1 input is "a list of objects" and parsing is outside it.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from proj7k.engine.params import Params
from proj7k.parser import Beatmap7K, NoteType, parse_osu_7k

#: A chart as `(column, head_s, tail_s | None)`; a rice has no tail (§2.1).
Notes = List[Tuple[int, float, Optional[float]]]


def notes_from_osu(content: str) -> Notes:
    return notes_from_beatmap(parse_osu_7k(content))


def notes_from_beatmap(beatmap: Beatmap7K) -> Notes:
    """A parsed chart as `(column, head_s, tail_s | None)` notes (§2.1)."""
    out: Notes = []
    for ho in beatmap.hit_objects:
        tail = ho.end_time if ho.note_type == NoteType.LN and ho.end_time is not None else None
        out.append((int(ho.column), ho.time / 1000.0, None if tail is None else tail / 1000.0))
    return out


@dataclass(frozen=True)
class Events:
    """Press and release events of a chart, snapped to rows. Arrays are indexed by event unless noted."""

    n: int                    # events
    n_rows: int
    T: np.ndarray             # [rows] row times
    row: np.ndarray           # row of each event
    col: np.ndarray
    rel: np.ndarray           # True for a release
    obj: np.ndarray           # the object (LN or rice) each event belongs to
    tail_row: np.ndarray      # [objects] row of an LN's tail, -1 for a rice
    is_ln: np.ndarray         # the event's object is an LN at all (its LN-ness g > 0)
    held: np.ndarray          # [rows, 7] how much the column is held strictly between head and tail rows (§2.6, R2): the LN's g
    h: np.ndarray             # held columns other than the event's own (§2.6)
    j: np.ndarray             # same-finger rate (§4.1)
    last: np.ndarray          # [rows, 7] time of the latest event on each column at or before the row
    delta_0: np.ndarray       # [rows] psi time scale from the local rhythm (§4.2–4.3)
    U_rhy: np.ndarray         # [rows] rhythm surprise (§5.2)
    lnw: np.ndarray           # ADR-0062: the LN-ness g of the event's object, 0 on a rice, 1 on an LN of l_full or longer
    wt: np.ndarray            # ADR-0062: the event's weight, 1 on a press, the LN's g on a release


def ln_weight(length: float, p: Params) -> float:
    """ADR-0062: an LN's LN-ness, 0 at or below l_min, 1 at or above l_full, a smoothstep between."""
    if length >= p.l_full:
        return 1.0
    if length <= p.l_min:
        return 0.0
    z = (length - p.l_min) / (p.l_full - p.l_min)
    return z * z * (3.0 - 2.0 * z)


def preprocess(notes: Notes, p: Params) -> Events:
    """§2.2–2.6 plus the parts of §4–5 that do not depend on the thumb's hand."""
    objs = []
    for c, t, e in notes:
        g = 0.0 if e is None else ln_weight(e - t, p)
        if g <= 0.0:  # §2.2
            e = None
        objs.append((c, t, e, g))
    objs.sort(key=lambda o: (o[1], o[0]))
    g_obj = np.array([o[3] for o in objs])

    raw = []  # (time, col, is_release, obj)
    for k, (c, t, e, _) in enumerate(objs):
        raw.append((t, c, 0, k))
        if e is not None:
            raw.append((e, c, 1, k))
    raw.sort(key=lambda x: (x[0], x[2], x[1]))

    # §2.4 rows (R1): a row opens at its first event; later events within row_tol of it join it
    row_of, T = [], []
    for t, *_ in raw:
        if not T or t - T[-1] > p.row_tol + 1e-12:
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

    head_row = np.full(len(objs), -1)
    tail_row = np.full(len(objs), -1)
    for i in range(n):
        (tail_row if ev_rel[i] else head_row)[ev_obj[i]] = ev_row[i]
    ell = np.where(is_ln, T[tail_row[ev_obj]] - T[head_row[ev_obj]], 0.0)

    # §2.6 held[r, c]: an LN on c with head row < r < tail row (R2)
    diff = np.zeros((n_rows + 1, 7))
    for k, (c, t, e, g) in enumerate(objs):
        if e is not None and tail_row[k] - head_row[k] > 1:
            diff[head_row[k] + 1, c] += g
            diff[tail_row[k], c] -= g
    held = np.clip(np.cumsum(diff, axis=0)[:n_rows], 0.0, 1.0)
    held[held < 1e-12] = 0.0
    h = held.sum(1)[ev_row] - held[ev_row, ev_col]

    # §4.1 j
    j = np.zeros(n)
    last_obj = [None] * 7
    for i in range(n):
        if ev_rel[i]:
            j[i] = 1.0 / ell[i]
            continue
        c, k = ev_col[i], ev_obj[i]
        prev = last_obj[c]
        if prev is not None:
            pe, pg = objs[prev][2], objs[prev][3]
            e_rice = T[head_row[prev]] + p.delta_h
            # ADR-0062: a partial LN's end sits between the rice's and the full LN's
            e_prime = pg * T[tail_row[prev]] + (1.0 - pg) * e_rice if pe is not None else e_rice
            j[i] = 1.0 / max(p.delta_floor, ev_t[i] - e_prime + p.delta_h)
        last_obj[c] = k

    lnw = g_obj[ev_obj]
    wt = np.where(ev_rel, lnw, 1.0)

    # last[r, c]: time of the latest event on column c at or before row r; ADR-0062: a partial LN's release
    # counts as a motion at head + g * length, the head's own time as g goes to 0 (a rice) and the tail's at 1
    t_eff = np.where(ev_rel, T[head_row[ev_obj]] + lnw * (ev_t - T[head_row[ev_obj]]), ev_t)
    grid = np.full((n_rows, 7), -np.inf)
    np.maximum.at(grid, (ev_row, ev_col), t_eff)
    last = np.maximum.accumulate(grid, axis=0)

    return Events(
        n=n, n_rows=n_rows, T=T, row=ev_row, col=ev_col, rel=ev_rel, obj=ev_obj, tail_row=tail_row,
        is_ln=is_ln, held=held, h=h, j=j, last=last, delta_0=_local_delta_0(T, p), U_rhy=rhythm_surprise(T, p),
        lnw=lnw, wt=wt,
    )


def _local_delta_0(T: np.ndarray, p: Params) -> np.ndarray:
    """§4.2–4.3: the psi time scale of each row, a fraction of the median row interval over the past seconds."""
    iv = np.diff(T, prepend=T[0] - 1.0)
    lo = np.searchsorted(T, T - p.delta_0_window, side="left")
    return np.array([
        p.delta_0_rel * np.median(iv[max(lo[r], 1):r + 1]) if r >= 1 else p.delta_0
        for r in range(len(T))
    ])


def rhythm_surprise(T: np.ndarray, p: Params) -> np.ndarray:
    """§5.2, per row (R5): how unexpected the row's interval is against the decayed history of intervals."""
    n = len(T)
    U = np.zeros(n)
    if n < 2:
        return U
    ell = np.full(n, np.nan)
    ell[1:] = np.log2(np.diff(T))
    floor = p.alpha_r * 2.0 ** (-p.U_max)
    lo = 1
    for r in range(1, n):
        while lo < r and T[r] - T[lo] > p.rhythm_horizon * p.T_c:
            lo += 1
        if lo < r:
            w = np.exp(-(T[r] - T[lo:r]) / p.T_c)
            k = np.exp(-((ell[r] - ell[lo:r]) ** 2) / (2 * p.b ** 2))
            rho = (np.dot(w, k) + floor) / (w.sum() + p.alpha_r)
        else:
            rho = floor / p.alpha_r
        U[r] = min(p.U_max, -np.log2(rho))
    return U
