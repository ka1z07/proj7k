"""
The difficulty field: the spec-v0.2 engine read event by event (ADR-0020).

The engine's output is a few numbers, but it is computed from a per-event quantity: each press or
release has a demand reading `d_i` (§7), the level `theta` at which it is lost with probability one
half. The *strain* of a chart, in this engine, is that reading laid out in time. Everything that
once consumed the legacy strain curve (the profiler's response model, the downscaler's window
search, the replay timeline) reads it from here instead.

    field = trace_osu(content)
    field.t, field.d            # when each event happens and how demanding it is
    field.p                     # chance the event is lost at the chart's own level D
    field.curve(bin_s=1.0)      # the timeline: load, risk and the skill that carries it, per bin
    level_for_loss(...)         # invert the engine: the level a player showed by what they lost

Nothing here changes a reading: the profile is the engine's own `evaluate_notes`, and the arrays
are the same ones it solved on (`tests/test_field.py` pins that). The module lives beside the
engine package, not in it, because the engine's version token digests every module in the
package and a consumer must not make stamped charts stale.
"""

from dataclasses import dataclass
from functools import cached_property
from typing import Dict, List, Optional, Tuple

import numpy as np

from proj7k.engine import DifficultyProfile, evaluate_notes
from proj7k.engine.attribution import dominance, ln_context, memberships
from proj7k.engine.demand import hand_demand
from proj7k.engine.events import Events, Notes, notes_from_beatmap, notes_from_osu, preprocess
from proj7k.engine.params import DEFAULT, Params
from proj7k.engine.scale import STAR_A, STAR_B, stars_of
from proj7k.engine.skills import SKILL_TECH_KEY, SKILLS
from proj7k.engine.solver import loss
from proj7k.parser import Beatmap7K

#: The short key each skill is stored and reported under by the profiler, the downscaler and the live
#: frame (`jack`, `tech`, ..., `ln_release`), in the engine's skill order.
TECH_KEYS: Tuple[str, ...] = tuple(SKILL_TECH_KEY[name] for name in SKILLS)


def d_of_stars(stars: float) -> float:
    """The inverse of the star scale (§10): the total difficulty `D` that reads as `stars`."""
    if stars <= 0.0:
        return 0.0
    return float((stars / STAR_A) ** (1.0 / STAR_B))


@dataclass(frozen=True)
class Curve:
    """The chart's difficulty over time, in equal bins from `start_s`."""

    start_s: float
    bin_s: float
    load: np.ndarray    # [bins] the largest reading in the bin over the chart's level D; 1.0 is "as hard as the chart"
    risk: np.ndarray    # [bins] expected events lost in the bin at level D
    skill: np.ndarray   # [bins] index into SKILLS of the skill that carries the bin's risk; -1 if it has none
    events: np.ndarray  # [bins] events in the bin

    def to_dict(self) -> dict:
        return {
            "start_s": self.start_s,
            "bin_s": self.bin_s,
            "load": [round(float(x), 3) for x in self.load],
            "risk": [round(float(x), 3) for x in self.risk],
            "skill": [int(x) for x in self.skill],
            "events": [int(x) for x in self.events],
        }


@dataclass(frozen=True)
class HotSpot:
    """A stretch of the chart that carries an unusual share of its expected loss."""

    start_s: float
    end_s: float
    risk: float          # expected events lost in the stretch at level D
    share: float         # risk over the chart's total risk
    skill: str           # the skill that carries most of it

    def to_dict(self) -> dict:
        return {
            "start_s": round(self.start_s, 3), "end_s": round(self.end_s, 3),
            "risk": round(self.risk, 3), "share": round(self.share, 4), "skill": self.skill,
        }


@dataclass(frozen=True)
class ChartField:
    profile: DifficultyProfile
    params: Params
    events: Events
    thumb: int
    t: np.ndarray        # [n] event times, s (the row the event snapped to)
    col: np.ndarray      # [n]
    release: np.ndarray  # [n] bool
    d: np.ndarray        # [n] demand reading
    v: np.ndarray        # [n] instantaneous demand, Hz
    p: np.ndarray        # [n] chance the event is lost at the chart's level D
    share: np.ndarray    # [n] the event's share of the chart's loss slope (§9.4), sums to 1
    w: np.ndarray        # [n, 8] membership (overlapping)
    a: np.ndarray        # [n, 8] attribution (a partition per family)

    @property
    def total_D(self) -> float:
        return self.profile.total_D

    @property
    def eps_total(self) -> float:
        """The tolerance the total difficulty was solved at (§3.3)."""
        return self.params.eps_rc + self.params.eps_total_ln_slope * ln_context(self.events, self.params).mean()

    @property
    def duration_s(self) -> float:
        return float(self.t[-1] - self.t[0]) if len(self.t) else 0.0

    @cached_property
    def _presses(self) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
        """Per column: the times of its press events (sorted) and the events' indices."""
        out = {}
        for c in range(7):
            idx = np.flatnonzero((self.col == c) & ~self.release)
            out[c] = (self.t[idx], idx)
        return out

    @cached_property
    def _release_of(self) -> Dict[int, int]:
        """Event index of an LN's release by the event index of its press."""
        by_obj = {int(self.events.obj[i]): int(i) for i in np.flatnonzero(self.release)}
        return {int(i): by_obj[int(self.events.obj[i])] for i in np.flatnonzero(~self.release) if int(self.events.obj[i]) in by_obj}

    def press_index(self, col: int, t_s: float, tol_s: float = 0.0025) -> Optional[int]:
        """The press event of a played note: the one on `col` nearest `t_s`, within `tol_s` (rows snap within 1 ms)."""
        times, idx = self._presses.get(col, (np.empty(0), np.empty(0, dtype=int)))
        if len(times) == 0:
            return None
        k = int(np.searchsorted(times, t_s))
        best = min((j for j in (k - 1, k) if 0 <= j < len(times)), key=lambda j: abs(times[j] - t_s))
        return int(idx[best]) if abs(times[best] - t_s) <= tol_s else None

    def release_index(self, press: int) -> Optional[int]:
        """The release event of the LN whose press is event `press`; None for a rice (or an LN folded to one, §2.2)."""
        return self._release_of.get(press)

    def skill_demand(self, t0_s: float, t1_s: float) -> Dict[str, float]:
        """The largest demand reading in [t0_s, t1_s], per skill, each event counting by its membership in the skill."""
        sel = (self.t >= t0_s) & (self.t <= t1_s)
        if not sel.any():
            return {k: 0.0 for k in SKILLS}
        peak = (self.d[sel, None] * self.w[sel]).max(0)
        return {k: float(peak[n]) for n, k in enumerate(SKILLS)}

    def carrying_skill(self, t0_s: float, t1_s: float) -> Optional[str]:
        """The skill that carries most of the expected loss in [t0_s, t1_s] (None if the stretch carries none)."""
        sel = (self.t >= t0_s) & (self.t <= t1_s)
        if not sel.any():
            return None
        carried = (self.a[sel] * self.p[sel, None]).sum(0)
        return SKILLS[int(carried.argmax())] if carried.max() > 1e-12 else None

    def curve(self, bin_s: float = 1.0, start_s: Optional[float] = None, end_s: Optional[float] = None) -> Curve:
        """Load, risk and carrying skill per bin of `bin_s` over [start_s, end_s] (default: the chart's span)."""
        if len(self.t) == 0:
            raise ValueError("empty chart")
        lo = float(self.t[0]) if start_s is None else start_s
        hi = float(self.t[-1]) if end_s is None else end_s
        n_bins = max(1, int(np.ceil((hi - lo) / bin_s + 1e-9)))
        idx = np.clip(((self.t - lo) // bin_s).astype(int), 0, n_bins - 1)
        D = self.total_D if self.total_D > 0 else 1.0
        load = np.zeros(n_bins)
        np.maximum.at(load, idx, self.d / D)
        risk = np.bincount(idx, weights=self.p, minlength=n_bins)
        count = np.bincount(idx, minlength=n_bins)
        carried = np.zeros((n_bins, len(SKILLS)))
        for k in range(len(SKILLS)):
            carried[:, k] = np.bincount(idx, weights=self.a[:, k] * self.p, minlength=n_bins)
        skill = np.where(risk > 1e-12, carried.argmax(1), -1)
        return Curve(start_s=lo, bin_s=bin_s, load=load, risk=risk, skill=skill, events=count)

    def hot_spots(self, window_s: float = 8.0, top: int = 5) -> List[HotSpot]:
        """
        The `top` non-overlapping windows of `window_s` that carry the most expected loss, hardest first.
        """
        if len(self.t) == 0 or self.p.sum() <= 0:
            return []
        total = float(self.p.sum())
        cum = np.concatenate([[0.0], np.cumsum(self.p)])
        lo_idx = np.searchsorted(self.t, self.t, side="left")
        hi_idx = np.searchsorted(self.t, self.t + window_s, side="left")
        score = cum[hi_idx] - cum[lo_idx]  # risk of the window that starts at each event
        taken: List[Tuple[float, float]] = []
        out: List[HotSpot] = []
        for i in np.argsort(-score, kind="stable"):
            start, end = float(self.t[i]), float(self.t[i] + window_s)
            if score[i] <= 0 or any(start < e and end > s for s, e in taken):
                continue
            taken.append((start, end))
            sel = slice(lo_idx[i], hi_idx[i])
            carried = (self.a[sel] * self.p[sel, None]).sum(0)
            out.append(HotSpot(start, end, float(score[i]), float(score[i]) / total, SKILLS[int(carried.argmax())]))
            if len(out) == top:
                break
        return out


def trace_notes(notes: Notes, params: Params = DEFAULT) -> ChartField:
    """The field of a chart given as `(column, head_s, tail_s | None)` notes."""
    profile = evaluate_notes(notes, params)  # raises on an empty chart, as the engine does
    ev = preprocess(notes, params)
    thumb = 0 if profile.thumb_hand == "L" else 1
    dem = hand_demand(ev, thumb, params)
    w, a = memberships(ev, dem, params)
    p = loss(dem.d, profile.total_D, params)
    s = p * (1 - p)
    total = s.sum()
    share = s / total if total > 0 else np.full_like(s, 1.0 / ev.n)
    return ChartField(
        profile=profile, params=params, events=ev, thumb=thumb,
        t=ev.T[ev.row], col=ev.col, release=ev.rel, d=dem.d, v=dem.v, p=p, share=share, w=w, a=a,
    )


def trace_osu(content: str, params: Params = DEFAULT) -> ChartField:
    return trace_notes(notes_from_osu(content), params)


def trace_beatmap(beatmap: Beatmap7K, params: Params = DEFAULT) -> ChartField:
    return trace_notes(notes_from_beatmap(beatmap), params)


def level_for_loss(d: np.ndarray, w: np.ndarray, observed: float, eps: float, params: Params = DEFAULT) -> float:
    """
    The level `theta` at which the weighted expected loss equals what was actually lost, the engine's
    own equation (§3.2) read the other way. `observed` is the weighted sum of what was lost.

    The tolerance side keeps its prior mass: the target is `observed + eps * N0`, so a player who
    loses exactly the tolerance, `observed = eps * W`, sits at exactly the chart's difficulty `D`; one
    who loses nothing sits above it by a finite amount, not at infinity. Returns `theta_max` if even a
    clean play does not pin the level down, and 0 where the weight has nothing to fail at.
    """
    target = observed + eps * params.N0
    live = d > 0
    if w[live].sum() <= 0:
        return 0.0
    lo, hi = np.log(params.theta_min), np.log(params.theta_max)

    def g(lt: float) -> float:
        return float(np.dot(w, loss(d, float(np.exp(lt)), params)) - target)

    if g(lo) <= 0:   # even the lowest level loses less than was lost: the level is below the scale
        return params.theta_min
    if g(hi) >= 0:   # even the highest level loses more than was lost: the play was cleaner than the scale reads
        return params.theta_max
    while hi - lo > params.theta_tol:
        mid = 0.5 * (lo + hi)
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    return float(np.exp(0.5 * (lo + hi)))


__all__ = [
    "ChartField", "Curve", "HotSpot", "d_of_stars", "level_for_loss", "stars_of",
    "trace_beatmap", "trace_notes", "trace_osu",
]
