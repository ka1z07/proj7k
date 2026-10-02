"""
The star scale (spec §10) and its authority.

The anchor file `anchors.json` is the only authority for what a star means (ADR-0018): the project
owner's consensus statements of the form "the four Regular maps of tier n taken together read about
S stars". Stars are `STAR_A * D ** STAR_B`, one line in ln-ln shared by every skill. Evaluation uses
the two pinned constants below; `fit_star_scale` is the fit they came from, and
`tests/engine/test_engine_scale.py` re-runs it on the 120 benchmark charts so that the constants and
the file cannot drift apart. Re-baselining the scale is: change the file, refit, update the pair.
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

ANCHORS_PATH = Path(__file__).with_name("anchors.json")

#: The ln-ln line through the anchors' tier-level D and stars (fit of `anchors.json`).
STAR_A = 0.22219312824310733
STAR_B = 1.00661910788821


def stars_of(D: float) -> float:
    return STAR_A * D ** STAR_B


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
