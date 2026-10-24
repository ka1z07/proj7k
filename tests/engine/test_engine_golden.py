"""
The production engine reproduces the frozen spec-v0.2 prototype on all 120 benchmark charts.

`tests/fixtures/engine_golden_v02.json` is the prototype's committed output (ADR-0017). The port
dropped the prototype's experiment switches and the cognitive and fatigue terms that reach no
output in v0.2 (spec 修订记录); this test is what says the drop changed nothing.

The engine has since moved its constants (ADR-0044: the same-finger weight `k_same`, and the star
scale refitted on it), so the port is checked under the prototype's own constants (`params.V02`) and
the prototype's own star line (the golden's `a`, `b`).
"""

import pytest

from engine_support import POOL_OF, TIERS
from proj7k.engine import SKILLS

REL = 1e-9
ABS = 1e-12


def test_golden_covers_every_benchmark_chart(golden, chart_keys):
    assert set(golden["charts"]) == {f"{pool} {tier}" for pool, tier in chart_keys}


def test_total_difficulty_matches_the_prototype(golden, v02_profiles, chart_keys):
    for pool, tier in chart_keys:
        want = golden["charts"][f"{pool} {tier}"]["total"]["D"]
        assert v02_profiles[(pool, tier)].total_D == pytest.approx(want, rel=REL, abs=ABS), f"{pool} {tier}"


def test_skill_readings_match_the_prototype(golden, v02_profiles, chart_keys):
    for pool, tier in chart_keys:
        want = golden["charts"][f"{pool} {tier}"]["skills"]
        got = v02_profiles[(pool, tier)].skills
        for k in SKILLS:
            where = f"{pool} {tier} {k}"
            assert got[k].D == pytest.approx(want[k]["D"], rel=REL, abs=ABS), where
            assert got[k].coverage == pytest.approx(want[k]["coverage"], rel=REL, abs=ABS), where
            assert got[k].dominance == pytest.approx(want[k]["dominance"], rel=REL, abs=ABS), where


def test_dominant_skill_rank_margin_and_thumb_match_the_prototype(golden, v02_profiles, chart_keys):
    for pool, tier in chart_keys:
        want = golden["charts"][f"{pool} {tier}"]
        got = v02_profiles[(pool, tier)]
        assert got.thumb_hand == want["thumb_hand"], f"{pool} {tier}"
        assert got.dominant_skill == want["dominant_skill"], f"{pool} {tier}"
        assert list(got.dominance_rank) == want["dominance_rank"], f"{pool} {tier}"
        assert got.dominance_margin == pytest.approx(want["dominance_margin"], rel=REL, abs=ABS), f"{pool} {tier}"


def test_stars_match_the_prototype(golden, v02_profiles, chart_keys):
    a, b = golden["stars"]["a"], golden["stars"]["b"]
    for pool, tier in chart_keys:
        want = golden["charts"][f"{pool} {tier}"]
        got = v02_profiles[(pool, tier)]
        assert a * got.total_D ** b == pytest.approx(want["total"]["stars"], rel=REL, abs=ABS), f"{pool} {tier}"
        for k in SKILLS:
            assert a * got.skills[k].D ** b == pytest.approx(want["skills"][k]["stars"], rel=REL, abs=ABS), f"{pool} {tier} {k}"


def test_output_has_the_spec_9_6_shape(engine_profiles):
    out = engine_profiles[("Regular Jack", "5th")].to_dict()
    assert set(out) == {"thumb_hand", "total", "skills", "dominant_skill", "dominance_rank", "dominance_margin"}
    assert set(out["total"]) == {"D", "stars"}
    assert list(out["skills"]) == list(SKILLS)
    assert set(out["skills"]["rc_jack"]) == {"D", "stars", "coverage", "dominance"}
    assert out["thumb_hand"] in ("L", "R")
    assert sorted(out["dominance_rank"]) == sorted(SKILLS)


def test_pools_map_onto_the_eight_skills():
    assert list(POOL_OF.values()) == list(SKILLS)
    assert len(TIERS) == 15
