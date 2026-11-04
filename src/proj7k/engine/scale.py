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
