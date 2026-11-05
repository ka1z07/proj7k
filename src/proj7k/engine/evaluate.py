"""
The engine's public entry (spec §9.6, §11): a chart in, a difficulty profile out.

    total difficulty D and stars; for each of the eight skills D_k, stars, level, coverage and dominance;
    the dominant skill with the full ranking and the margin; and the hand the thumb plays on.

Since ADR-0056 a skill's D_k and stars say how much the chart requires that skill (its own cost,
`skillcost`, on its own scale); coverage and dominance still say how much of the chart, and of the
total's difficulty, the skill's events carry (`attribution`). A skill's level (ADR-0059) is its stars
over what a chart of this total usually requires of it (`scale.skill_level`), and the dominant skill
is the family's skill with the highest level: the chart is an LN chart when its mean LN context
(§8.1's lambda) is at least one half, a rice chart otherwise, and within the LN family `ln_general`
is the umbrella, held unless a specialty's level reaches `umbrella_level`. Under `dominance_model = "pi"` (v0.2) the
dominant skill is the largest share instead.

The thumb goes to whichever hand gives the lower total difficulty (ties: the left).
"""

from dataclasses import dataclass, replace
from typing import Dict, Tuple

import numpy as np

from proj7k.engine.attribution import dominance, ln_context, memberships
from proj7k.engine.demand import hand_demand
from proj7k.engine.events import Events, Notes, notes_from_osu, preprocess
from proj7k.engine.params import DEFAULT, Params
from proj7k.engine.scale import skill_level, skill_stars_of, stars_of
from proj7k.engine.skillcost import skill_demands
from proj7k.engine.skills import SKILLS
from proj7k.engine.solver import solve

_TOLERANCE = 1e-9


@dataclass(frozen=True)
class SkillReading:
    D: float
    stars: float
    level: float
    coverage: float
    dominance: float


@dataclass(frozen=True)
class Diagnostics:
    n_events: int
    attribution_max_error: float  # max |sum_k a_ik - 1| (§14 T6)
    dominance_sum_error: float    # |sum_k pi_k - 1| (§14 T6)


@dataclass(frozen=True)
class DifficultyProfile:
    thumb_hand: str                       # "L" | "R"
    total_D: float
    total_stars: float
    skills: Dict[str, SkillReading]       # in SKILLS order
    dominant_skill: str
    dominance_rank: Tuple[str, ...]       # most to least dominant
    dominance_margin: float               # pi of the first minus pi of the second
    diagnostics: Diagnostics
    dominance_keys: Tuple[float, ...] = ()   # the §9.4 number each skill is ranked by, in SKILLS order

    def dominance_key(self, skill: str) -> float:
        """The number `skill` is ranked by in `dominance_rank` (its level, the umbrella level, or its share)."""
        return self.dominance_keys[SKILLS.index(skill)]

    def to_dict(self) -> dict:
        """The §9.6 structure."""
        return {
            "thumb_hand": self.thumb_hand,
            "total": {"D": self.total_D, "stars": self.total_stars},
            "skills": {
                k: {"D": s.D, "stars": s.stars, "level": s.level, "coverage": s.coverage, "dominance": s.dominance}
                for k, s in self.skills.items()
            },
            "dominant_skill": self.dominant_skill,
            "dominance_rank": list(self.dominance_rank),
            "dominance_margin": self.dominance_margin,
        }


def evaluate_osu(content: str, params: Params = DEFAULT) -> DifficultyProfile:
    """The profile of a 7K `.osu` file's content."""
    return evaluate_notes(notes_from_osu(content), params)


def evaluate_notes(notes: Notes, params: Params = DEFAULT) -> DifficultyProfile:
    """The profile of a chart given as `(column, head_s, tail_s | None)` notes (§2.1)."""
    if not notes:
        raise ValueError("empty chart")
    return _profile(preprocess(notes, params), params)


def _burst(ev: Events, dem, D: float, eps: float, p: Params) -> float:
    """
    ADR-0054: burst_kappa times the hardest window's difficulty. Each window of burst_window seconds is solved
    on its own, with the short-memory reading and pseudo-count burst_N0, so a dense section that is only a few
    percent of the chart is not forgiven by the whole chart's tolerance. A chart with nothing to solve stays 0.
    """
    if p.burst_kappa <= 0 or D <= 0:
        return 0.0
    pw = replace(p, N0=p.burst_N0)
    t_ev = ev.T[ev.row]
    starts = np.arange(ev.T[0], ev.T[-1] + 1e-9, p.burst_window / 4)
    best = 0.0
    for t0 in starts:
        idx = (t_ev >= t0) & (t_ev < t0 + p.burst_window)
        if idx.any():
            best = max(best, solve(dem.d_burst[idx], np.ones(int(idx.sum())), eps, pw))
    return p.burst_kappa * best


def dominance_keys(levels, pi, lam_mean: float, p: Params) -> np.ndarray:
    """
    §9.4: the number each skill is ranked by. Under "level" (ADR-0059) it is the skill's level, with the
    chart's family first: an LN chart (mean LN context `lam_mean` >= 1/2) ranks its four LN skills above the
    rice skills and a rice chart the reverse, and `ln_general`, the LN umbrella, ranks at `umbrella_level`, so
    that it holds an LN chart unless a specialty stands at least that far above what the total usually
    requires. Under "pi" (v0.2) it is the share itself.
    """
    if p.dominance_model == "pi":
        return np.asarray(pi, dtype=float)
    key = np.asarray(levels, dtype=float).copy()
    key[SKILLS.index("ln_general")] = p.umbrella_level
    rc = np.array([k.startswith("rc") for k in SKILLS])
    ln_chart = lam_mean >= 0.5
    # the other family sits below the chart's family whatever its levels
    return np.where(rc != ln_chart, key, key - _OTHER_FAMILY)


_OTHER_FAMILY = 1e3


def _profile(ev: Events, p: Params) -> DifficultyProfile:
    ones = np.ones(ev.n)
    lam_mean = float(ln_context(ev, p).mean())
    eps_total = p.eps_rc + p.eps_total_ln_slope * lam_mean

    best = None
    for thumb in (0, 1):
        dem = hand_demand(ev, thumb, p)
        D = solve(dem.d, ones, eps_total, p)
        if best is None or D < best[0]:
            best = (D, dem)
    D, dem = best
    D = max(D, _burst(ev, dem, D, eps_total, p))

    w, a = memberships(ev, dem, p)
    attribution_error = float(np.abs(a.sum(1) - 1.0).max())
    if attribution_error >= _TOLERANCE:
        raise ArithmeticError(f"attribution must sum to 1 per event (§8.2); off by {attribution_error}")

    if p.skill_model == "cost":
        # ADR-0056: each skill's own cost on the hand accumulator, on its own star scale
        demands = skill_demands(ev, dem, p)
        D_k = [demands[k] for k in SKILLS]
        stars_k = [skill_stars_of(k, demands[k]) for k in SKILLS]
    else:
        # v0.2: the total's reading under the skill's membership weights (kept for the golden test)
        D_k = [solve(dem.d, w[:, n], p.eps_rc if k.startswith("rc") else p.eps_ln, p) for n, k in enumerate(SKILLS)]
        stars_k = [stars_of(x) for x in D_k]
    coverage = w.sum(0) / ev.n
    pi = dominance(a, dem.d, D, p)
    dominance_error = float(abs(pi.sum() - 1.0))
    if dominance_error >= _TOLERANCE:
        raise ArithmeticError(f"dominance must sum to 1 (§9.4); off by {dominance_error}")

    total_stars = stars_of(D)
    levels = [skill_level(k, stars_k[n], total_stars) for n, k in enumerate(SKILLS)]
    key = dominance_keys(levels, pi, lam_mean, p)
    order = sorted(range(len(SKILLS)), key=lambda n: (-key[n], n))
    return DifficultyProfile(
        thumb_hand="LR"[dem.thumb],
        total_D=D,
        total_stars=total_stars,
        skills={
            k: SkillReading(D=D_k[n], stars=stars_k[n], level=float(levels[n]), coverage=float(coverage[n]), dominance=float(pi[n]))
            for n, k in enumerate(SKILLS)
        },
        dominant_skill=SKILLS[order[0]],
        dominance_rank=tuple(SKILLS[n] for n in order),
        dominance_margin=float(key[order[0]] - key[order[1]]),
        diagnostics=Diagnostics(n_events=ev.n, attribution_max_error=attribution_error, dominance_sum_error=dominance_error),
        dominance_keys=tuple(float(x) for x in key),
    )
