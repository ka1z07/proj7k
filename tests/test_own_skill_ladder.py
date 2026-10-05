"""
The `own_skill` ladder (ADR-0018 decision 3): each benchmark pool is gated on the stars of the one skill it
is the ladder of, so no pool is judged on a skill it is not about.
"""

import json
from pathlib import Path

from proj7k.batch import BenchmarkItemResult
from proj7k.engine import SKILLS
from proj7k.guard import MonotonicityGuardConfig
from proj7k.monotonicity import OWN_SKILL_METRIC, read_ladder_metric

MANIFEST = Path(__file__).resolve().parents[1] / "docs" / "research" / "structured_index.json"

#: Written out, not derived from the code under test.
EXPECTED_OWN_SKILL = {
    "Regular Jack": "rc_jack",
    "Regular Tech": "rc_tech",
    "Regular Speed": "rc_speed",
    "Regular Stream": "rc_stamina",
    "LN General": "ln_general",
    "LN Tech": "ln_tech",
    "LN Inverse": "ln_inverse",
    "LN Release": "ln_release",
}


def _result(technique: str) -> BenchmarkItemResult:
    return BenchmarkItemResult(
        technique=technique,
        tier="5th",
        status="SUCCESS",
        skills={name: float(index) for index, name in enumerate(SKILLS)},
    )


def test_a_pool_reads_the_stars_of_its_own_skill():
    for pool, skill in EXPECTED_OWN_SKILL.items():
        assert read_ladder_metric(_result(pool), OWN_SKILL_METRIC) == float(SKILLS.index(skill)), pool


def test_a_result_without_skills_or_from_an_unknown_pool_supplies_no_reading():
    assert read_ladder_metric(BenchmarkItemResult(technique="LN Release", tier="5th", status="SUCCESS"), OWN_SKILL_METRIC) is None
    assert read_ladder_metric(_result("Some Other Pool"), OWN_SKILL_METRIC) is None


def test_every_manifest_pool_has_an_own_skill_and_the_eight_skills_are_covered_once():
    from proj7k.engine.skills import BENCHMARK_POOL_SKILL

    manifest_pools = set(json.loads(MANIFEST.read_text(encoding="utf-8")))
    assert BENCHMARK_POOL_SKILL == EXPECTED_OWN_SKILL
    assert set(BENCHMARK_POOL_SKILL) == manifest_pools
    assert sorted(BENCHMARK_POOL_SKILL.values()) == sorted(SKILLS)


def test_the_guard_gates_the_own_skill_ladder_by_default():
    assert OWN_SKILL_METRIC in MonotonicityGuardConfig().metrics
