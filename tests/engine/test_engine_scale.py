"""
The star scale is the anchor file's (ADR-0018), and the version token follows the engine.

Production evaluates with two pinned constants; this test is what ties them to the anchors:
refitting the scale from `anchors.json` and the 120 charts' tier medians must give them back.
"""

import json
from dataclasses import replace

import numpy as np
import pytest

from engine_support import POOL_OF, TIERS
from proj7k.engine import engine_version
from proj7k.engine.params import DEFAULT
from proj7k.engine.scale import ANCHORS_PATH, STAR_A, STAR_B, fit_star_scale, stars_of


@pytest.fixture(scope="module")
def anchors():
    return json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))


def _tier_medians(profiles, pools):
    return {t: float(np.median([profiles[(p, t)].total_D for p in pools])) for t in TIERS}


def test_pinned_constants_are_the_fit_of_the_anchor_file(anchors, engine_profiles):
    a, b, active = fit_star_scale(_tier_medians(engine_profiles, anchors["pools"]), anchors)
    assert a == pytest.approx(STAR_A, rel=1e-9)
    assert b == pytest.approx(STAR_B, rel=1e-9)
    assert active == ["Zenith"]  # the owner's "Zenith above 10" is binding on the RC ladder


def test_rc_anchors_are_met_within_a_quarter_star(anchors, engine_profiles):
    medians = _tier_medians(engine_profiles, anchors["pools"])
    for anchor in anchors["anchors"]:
        got = stars_of(medians[anchor["tier"]])
        if "stars" in anchor:
            assert abs(got - anchor["stars"]) <= 0.25, anchor
            if "range" in anchor:
                assert anchor["range"][0] <= got <= anchor["range"][1], anchor
        else:
            assert got >= anchor["min_stars"] - 1e-9, anchor


def test_rc_tier_medians_are_strictly_increasing_stars(golden, engine_profiles, anchors):
    """The dan table of ADR-0018 is read off these fifteen numbers."""
    medians = _tier_medians(engine_profiles, anchors["pools"])
    got = [stars_of(medians[t]) for t in TIERS]
    assert got == sorted(got) and len(set(got)) == 15
    for t, want in golden["stars"]["rc_tier_level"].items():
        assert stars_of(medians[t]) == pytest.approx(want["stars"], abs=0.006), t  # golden is rounded to 2 decimals


def test_the_four_rc_pools_are_the_anchor_pools(anchors):
    assert anchors["pools"] == [p for p in POOL_OF if p.startswith("Regular")]


def test_version_token_is_stable_and_follows_the_constants():
    assert engine_version() == engine_version()
    assert len(engine_version()) == 8 and all(c in "0123456789abcdef" for c in engine_version())
    assert engine_version(replace(DEFAULT, chi_0=DEFAULT.chi_0 + 0.01)) != engine_version()
