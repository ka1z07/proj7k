"""
The star scale (spec §10) and its authority.

The anchor file `anchors.json` is the only authority for what a star means (ADR-0018): the project
owner's consensus statements of the form "the four Regular maps of tier n taken together read about
S stars". Total stars are `STAR_A * D ** STAR_B`, one line in ln-ln. Evaluation uses the two pinned
constants below; `fit_star_scale` is the fit they came from, and `tests/engine/test_engine_scale.py`
re-runs it on the 120 benchmark charts so that the constants and the file cannot drift apart.
Re-baselining the scale is: change the file, refit, update the pair.

A skill's stars (ADR-0056) are `a_k * D_k ** b_k` on the skill's own line, fitted so that the skill's
own benchmark pool reads, tier by tier, the stars its family's ladder reads on the total scale (the
four RC pools' tier median, the four LN pools' tier geometric mean): "rc_tech 7 stars" is "requires
7th-tier tech". `SKILL_SCALE` pins the eight pairs; `fit_skill_scale` is their fit and
`tests/engine/test_engine_skills.py` re-runs it. The total's scale is the authority; the skill
scales follow it and are refit whenever it, or a skill's cost, moves.

A skill's level (ADR-0059) is its stars over what a chart of this total usually requires of it:
`SKILL_EXPECT` pins, per skill, the ln-ln line of the skill's stars against total stars over its
family's 60 benchmark charts, and `skill_level` reads the ratio. The dominant skill is the one whose
level stands highest (§9.4): a 220 BPM stream at 5 stars is a speed chart because 5-star charts do
not usually ask for that rate, while the same rate at 10 stars is what any 10-star chart asks.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

ANCHORS_PATH = Path(__file__).with_name("anchors.json")

#: The ln-ln line through the anchors' tier-level D and stars (fit of `anchors.json`).
STAR_A = 0.35061692407081574
STAR_B = 0.7563906685450623


def stars_of(D: float) -> float:
    return STAR_A * D ** STAR_B


#: (a_k, b_k) per skill (ADR-0056), the fit of `fit_skill_scale` on the 120 benchmark charts.
SKILL_SCALE: Dict[str, Tuple[float, float]] = {
    "rc_jack": (0.4966972323519079, 0.8033975307437226),
    "rc_tech": (0.7582764177294805, 0.5949991636969195),
    "rc_speed": (0.39654452985789646, 0.7933308739078072),
    "rc_stamina": (0.7164813615327359, 0.6921153938852171),
    "ln_general": (0.30861008527796946, 0.8089341834249038),
    "ln_tech": (0.7537352649078002, 0.6855909798750277),
    "ln_inverse": (0.5936706615058907, 0.6461171119947136),
    "ln_release": (1.5026260041888677, 0.5664022723462133),
}


def skill_stars_of(skill: str, D: float) -> float:
    a, b = SKILL_SCALE[skill]
    return a * D ** b if D > 0.0 else 0.0


#: (ln a_k, b_k) per skill (ADR-0059): ln(stars_k) = ln a_k + b_k ln(total stars), the least-squares line over
#: the skill's family's 60 benchmark charts (the four Regular pools for rc_*, the four LN pools for ln_*);
#: the fit of `fit_skill_expect`, re-run by `tests/engine/test_engine_skills.py`.
SKILL_EXPECT: Dict[str, Tuple[float, float]] = {
    "rc_jack": (-0.13263861254207368, 1.010469691127703),
    "rc_tech": (0.31230412834018784, 0.7619881572052272),
    "rc_speed": (-0.23491667482045256, 1.0534694661923347),
    "rc_stamina": (-0.17196096898845814, 1.0250211109356844),
    "ln_general": (-0.1632286412979236, 1.0647392277607566),
    "ln_tech": (0.031199583234101574, 0.9715814009191315),
    "ln_inverse": (-0.23790125524209876, 0.9983487819102551),
    "ln_release": (0.28313587947298663, 0.729949832564748),
}


def expected_skill_stars(skill: str, total_stars: float) -> float:
    """What a chart of `total_stars` usually requires of `skill` (its family's benchmark line)."""
    la, b = SKILL_EXPECT[skill]
    return float(np.exp(la + b * np.log(total_stars))) if total_stars > 0.0 else 0.0


def skill_level(skill: str, stars: float, total_stars: float) -> float:
    """The skill's stars over the expectation at this total: 1 is usual, 1.2 is a fifth above usual."""
    e = expected_skill_stars(skill, total_stars)
    return stars / e if e > 0.0 else 0.0


def fit_skill_expect(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """(ln a_k, b_k): the least-squares line in ln-ln through (total stars, skill stars) over the family's charts;
    charts where the skill reads 0 carry no point."""
    pts = [(t, s) for t, s in points if t > 0.0 and s > 0.0]
    X = np.log([t for t, _ in pts]); Y = np.log([s for _, s in pts])
    b, la = np.polyfit(X, Y, 1)
    return float(la), float(b)


def skill_D_of_stars(skill: str, stars: float) -> float:
    """The inverse of a skill's scale: the `D_k` that reads as `stars` on skill k's own line."""
    a, b = SKILL_SCALE[skill]
    return float((stars / a) ** (1.0 / b)) if stars > 0.0 else 0.0


def fit_skill_scale(tier_D: Dict[str, float], tier_stars: Dict[str, float]) -> Tuple[float, float]:
    """
    (a_k, b_k): the least-squares line in ln-ln through (the skill's own pool's D_k at each tier, the family
    ladder's stars at that tier). Tiers where the pool reads D_k = 0 carry no point.
    """
    tiers = [t for t, d in tier_D.items() if d > 0.0]
    X = np.log([tier_D[t] for t in tiers])
    Y = np.log([tier_stars[t] for t in tiers])
    b, la = np.polyfit(X, Y, 1)
    return float(np.exp(la)), float(b)


def fit_star_scale(tier_D: Dict[str, float], anchors: dict) -> Tuple[float, float, List[str]]:
    """
    §10: (a, b) = least-squares line in ln-ln through the equality anchors' (tier-level D, stars).
    `tier_D` is the median total D of the anchor pools at each tier. An inequality anchor ("at least")
    the unconstrained line violates becomes an equality at its bound, and the line is refit through it:
    the returned list names those tiers.
    """
    eq = [x for x in anchors["anchors"] if "stars" in x]
    ge = [x for x in anchors["anchors"] if "min_stars" in x]
    X = np.log([tier_D[x["tier"]] for x in eq])
    Y = np.log([x["stars"] for x in eq])
    b, la = np.polyfit(X, Y, 1)
    active = [x["tier"] for x in ge if np.exp(la) * tier_D[x["tier"]] ** b < x["min_stars"]]
    if active:
        bound = [x for x in ge if x["tier"] in active][0]
        xb, yb = float(np.log(tier_D[bound["tier"]])), float(np.log(bound["min_stars"]))
        # least squares over the equality anchors with the active bound held exactly: parametrise through its point
        b = float(np.sum((X - xb) * (Y - yb)) / np.sum((X - xb) ** 2))
        la = yb - b * xb
    return float(np.exp(la)), float(b), active


def load_anchors() -> dict:
    return json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))
