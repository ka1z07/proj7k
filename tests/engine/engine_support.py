"""
Helpers shared by the engine's acceptance tests (spec §14).

The edits below are the constructive changes of T11: each takes a benchmark chart's notes —
`(column, head_s, tail_s | None)` — and changes one construct, so that the dominance
distribution must move in a stated direction without consulting any slot label.
"""

import random
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

Notes = List[Tuple[int, float, Optional[float]]]

TIERS = [
    "0th", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th",
    "8th", "9th", "10th", "Gamma", "Azimuth", "Zenith", "Stellium",
]
POOL_OF = {
    "Regular Jack": "rc_jack",
    "Regular Tech": "rc_tech",
    "Regular Speed": "rc_speed",
    "Regular Stream": "rc_stamina",
    "LN General": "ln_general",
    "LN Tech": "ln_tech",
    "LN Inverse": "ln_inverse",
    "LN Release": "ln_release",
}

SKILLS_LN = ("ln_general", "ln_tech", "ln_inverse", "ln_release")

LEFT, RIGHT = (0, 1, 2), (4, 5, 6)


def mirror(notes: Notes) -> Notes:
    return [(6 - c, t, e) for c, t, e in notes]


def concat_self(notes: Notes, rest: float) -> Notes:
    end = max(e if e is not None else t for _, t, e in notes)
    t0 = min(t for _, t, _ in notes)
    shift = end + rest - t0
    return notes + [(c, t + shift, None if e is None else e + shift) for c, t, e in notes]


def valid_insert(notes: Notes, rnd: random.Random, span: Tuple[float, float]):
    """A random rice that does not sit inside, or within 5 ms of, an object on its own column."""
    while True:
        c, t = rnd.randrange(7), rnd.uniform(*span)
        if all(not (cc == c and t0 - 0.005 <= t <= (e if e is not None else t0) + 0.005) for cc, t0, e in notes):
            return (c, t, None)


def t10_chart() -> Notes:
    """Long easy stream, then a short hard jack section (§14 T10)."""
    notes, t = [], 0.0
    roll = [0, 2, 4, 6, 1, 3, 5]
    beat = 60.0 / 150
    for k in range(int(90 / (beat / 4))):  # 90 s of 1/4 single-note roll at 150 BPM
        notes.append((roll[k % 7], t, None))
        t += beat / 4
    t += 1.0
    beat = 60.0 / 170
    for k in range(int(8 / (beat / 4))):  # 8 s of 1/4 [15] jack at 170 BPM
        for c in (1, 5):
            notes.append((c, t, None))
        t += beat / 4
    return notes


# --------------------------------------------------------------------------- T11 edits

def _hand(c: int) -> int:
    return 0 if c in LEFT else 1 if c in RIGHT else 0


def _by_time(notes: Notes) -> Notes:
    return sorted(notes, key=lambda n: (n[1], n[0]))


def unchord(notes: Notes, rnd: random.Random) -> Notes:
    out, last_t = [], None
    for c, t, e in _by_time(notes):
        if last_t is not None and abs(t - last_t) < 0.002:
            continue
        out.append((c, t, e))
        last_t = t
    return out


def chordify(notes: Notes, rnd: random.Random) -> Notes:
    occ = {(c, round(t, 3)) for c, t, e in notes}
    out = list(notes)
    for c, t, e in notes:
        for d in (1, -1):
            c2 = c + d
            if 0 <= c2 <= 6 and _hand(c2) == _hand(c) and (c2, round(t, 3)) not in occ and c2 != 3:
                out.append((c2, t, e))
                occ.add((c2, round(t, 3)))
                break
    return out


def jackify(notes: Notes, rnd: random.Random, p: float = 0.6) -> Notes:
    last = {0: None, 1: None}
    out, occ = [], set()
    for c, t, e in _by_time(notes):
        h = _hand(c)
        c2 = c
        if last[h] is not None and rnd.random() < p:
            c2 = last[h]
        if (c2, round(t, 3)) in occ:
            c2 = c
        occ.add((c2, round(t, 3)))
        last[h] = c2
        out.append((c2, t, e))
    return out


def jitter(notes: Notes, rnd: random.Random, ms: float = 0.035) -> Notes:
    out = []
    for c, t, e in notes:
        d = rnd.uniform(-ms, ms)
        out.append((c, t + d, None if e is None else e + d))
    return out


def lnify(notes: Notes, rnd: random.Random) -> Notes:
    ns = _by_time(notes)
    out = []
    for i, (c, t, e) in enumerate(ns):
        later = [t2 for c2, t2, _ in ns[i + 1:i + 400] if c2 == c and t2 > t + 0.001]
        if later and later[0] - t > 0.25:
            out.append((c, t, later[0] - 0.08))
        else:
            out.append((c, t, e))
    return out


def _tails_shift(notes: Notes, mode: str) -> Notes:
    press = np.array(sorted(t for c, t, e in notes))
    out = []
    for c, t, e in notes:
        if e is None:
            out.append((c, t, e))
            continue
        i = int(np.searchsorted(press, e))
        cand = [press[j] for j in (i - 1, i) if 0 <= j < len(press)]
        near = min(cand, key=lambda x: abs(x - e)) if cand else e
        if mode == "isolate" and abs(near - e) < 0.012:
            e2 = e + 0.04
        elif mode == "align" and abs(near - e) < 0.15 and near > t + 0.1:
            e2 = near
        else:
            e2 = e
        out.append((c, t, e2))
    return out


def isolate_tails(notes: Notes, rnd: random.Random) -> Notes:
    return _tails_shift(notes, "isolate")


def align_tails(notes: Notes, rnd: random.Random) -> Notes:
    return _tails_shift(notes, "align")


def holdadd(notes: Notes, rnd: random.Random, window: float = 4.0) -> Notes:
    """In every window one finger of one hand (hands alternating) is cleared of its notes and held for the whole window."""
    ns = list(notes)
    t0 = min(t for _, t, _ in notes)
    t1 = max((e if e is not None else t) for _, t, e in notes)
    k, t = 0, t0
    while t < t1:
        c = rnd.choice(LEFT if k % 2 == 0 else RIGHT)
        ns = [(c2, t2, e2) for c2, t2, e2 in ns if not (c2 == c and t - 0.02 <= t2 <= t + window)]
        ns.append((c, t + 0.1, t + window - 0.2))
        k += 1
        t += window
    return ns


#: (edit, pool it is applied to, skill whose dominance must move, expected sign). The LN-family edit
#: (`lnify`) is read on the summed dominance of the four LN skills instead; see test_engine_response.
EDITS: Dict[str, Tuple[Callable[[Notes, random.Random], Notes], str, str, int]] = {
    "unchord": (unchord, "Regular Stream", "rc_stamina", -1),
    "chordify": (chordify, "Regular Speed", "rc_stamina", +1),
    "jackify": (jackify, "Regular Speed", "rc_jack", +1),
    "jitter": (jitter, "Regular Jack", "rc_tech", +1),
    "isolate": (isolate_tails, "LN General", "ln_release", +1),
    "align": (align_tails, "LN Release", "ln_release", -1),
    "holdadd": (holdadd, "LN General", "ln_inverse", +1),
}
T11_TIERS = ["3rd", "5th", "7th", "9th", "10th", "Gamma"]
