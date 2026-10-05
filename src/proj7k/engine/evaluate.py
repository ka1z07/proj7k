"""
The engine's public entry (spec §9.6, §11): a chart in, a difficulty profile out.

    total difficulty D and stars; for each of the eight skills D_k, stars, coverage and dominance;
    the dominant skill with the full ranking and the margin; and the hand the thumb plays on.

The thumb goes to whichever hand gives the lower total difficulty (ties: the left).
"""

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np

from proj7k.engine.attribution import dominance, ln_context, memberships
from proj7k.engine.demand import hand_demand
from proj7k.engine.events import Events, Notes, notes_from_osu, preprocess
from proj7k.engine.params import DEFAULT, Params
from proj7k.engine.scale import stars_of
from proj7k.engine.skills import SKILLS
from proj7k.engine.solver import solve

_TOLERANCE = 1e-9


@dataclass(frozen=True)
class SkillReading:
    D: float
    stars: float
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

    def to_dict(self) -> dict:
        """The §9.6 structure."""
        return {
            "thumb_hand": self.thumb_hand,
            "total": {"D": self.total_D, "stars": self.total_stars},
            "skills": {
                k: {"D": s.D, "stars": s.stars, "coverage": s.coverage, "dominance": s.dominance}
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


def _profile(ev: Events, p: Params) -> DifficultyProfile:
    ones = np.ones(ev.n)
    eps_total = p.eps_rc + p.eps_total_ln_slope * ln_context(ev, p).mean()

    best = None
    for thumb in (0, 1):
        dem = hand_demand(ev, thumb, p)
        D = solve(dem.d, ones, eps_total, p)
        if best is None or D < best[0]:
            best = (D, dem)
    D, dem = best

    w, a = memberships(ev, dem, p)
    attribution_error = float(np.abs(a.sum(1) - 1.0).max())
    if attribution_error >= _TOLERANCE:
        raise ArithmeticError(f"attribution must sum to 1 per event (§8.2); off by {attribution_error}")

    D_k = [solve(dem.d, w[:, n], p.eps_rc if k.startswith("rc") else p.eps_ln, p) for n, k in enumerate(SKILLS)]
    coverage = w.sum(0) / ev.n
    pi = dominance(a, dem.d, D, p)
    dominance_error = float(abs(pi.sum() - 1.0))
    if dominance_error >= _TOLERANCE:
        raise ArithmeticError(f"dominance must sum to 1 (§9.4); off by {dominance_error}")

    order = np.argsort(-pi, kind="stable")
    ranked = np.sort(pi)
    return DifficultyProfile(
        thumb_hand="LR"[dem.thumb],
        total_D=D,
        total_stars=stars_of(D),
        skills={
            k: SkillReading(D=D_k[n], stars=stars_of(D_k[n]), coverage=float(coverage[n]), dominance=float(pi[n]))
            for n, k in enumerate(SKILLS)
        },
        dominant_skill=SKILLS[int(np.argmax(pi))],
        dominance_rank=tuple(SKILLS[int(k)] for k in order),
        dominance_margin=float(ranked[-1] - ranked[-2]),
        diagnostics=Diagnostics(n_events=ev.n, attribution_max_error=attribution_error, dominance_sum_error=dominance_error),
    )
