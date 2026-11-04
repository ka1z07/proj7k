"""
The skill layer of ADR-0056: each skill's stars say how much a chart requires that skill.

What the eight memberships of v0.2 could not do — because every D_k was the total's four-second reading
under different weights — is what this file holds the cost layer to on the 120 benchmark charts:

- an RC chart requires no LN skill and an LN chart's RC skills read well under its total (cross-family leakage);
- each skill reads highest, tier for tier, on its own pool (specificity), with the two umbrella skills
  (rc_stamina, ln_general) allowed to read as high on their family's other pools;
- on most of a pool's charts the pool's own skill is the highest of its family (own-top);
- the eight skill scales are the fit of `tools/fit_skill_scale.py` (the total's scale is the authority);
- a skill absent from a chart reads 0.

The numbers are ratchets: the engine's measured values, so that a later change cannot loosen them unnoticed.
Raising one is the point of a later change; lowering one needs a ruling.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from engine_support import POOL_OF, TIERS
from proj7k.engine import evaluate_notes
from proj7k.engine.scale import SKILL_SCALE
from proj7k.engine.skills import SKILLS

REPO_ROOT = Path(__file__).resolve().parents[2]

RC_POOLS = [p for p, k in POOL_OF.items() if k.startswith("rc")]
LN_POOLS = [p for p, k in POOL_OF.items() if k.startswith("ln")]

#: Per pool and other-family skill, the tier mean of (that skill's stars / total stars): measured 0.00–0.07 on RC
#: pools' LN skills and 0.21–0.78 on LN pools' RC skills (an LN chart's rice is real work, LN Tech's rice real
#: tech; the LN context only partly discounts it).
CROSS_FAMILY_CEILING = {"rc": 0.10, "ln": 0.80}

#: For each skill, the highest other pool's mean ratio (that pool's stars / own pool's stars, tier by tier).
#: The umbrella skills may read above 1 elsewhere in their family (an Inverse chart is a general LN chart).
SPECIFICITY_CEILING = {
    "rc_jack": 0.92,
    "rc_tech": 0.91,
    "rc_speed": 0.91,
    "rc_stamina": 0.87,
    "ln_general": 1.05,
    "ln_tech": 1.04,
    "ln_inverse": 0.81,
    "ln_release": 0.98,
}

#: Charts of the pool (of 15) on which the pool's own skill is the highest star of its family.
OWN_TOP_FLOOR = {
    "Regular Jack": 12,
    "Regular Tech": 11,
    "Regular Speed": 10,
    "Regular Stream": 11,
    "LN General": 7,
    "LN Tech": 13,
    "LN Inverse": 3,
    "LN Release": 6,
}


def _ratio(profiles, pool, skill):
    return np.mean([profiles[(pool, t)].skills[skill].stars / profiles[(pool, t)].total_stars for t in TIERS])


def test_an_rc_chart_requires_no_ln_skill_and_an_ln_charts_rc_skills_read_under_its_total(engine_profiles):
    for family, pools, other in (("rc", RC_POOLS, "ln"), ("ln", LN_POOLS, "rc")):
        for pool in pools:
            for skill in (k for k in SKILLS if k.startswith(other)):
                assert _ratio(engine_profiles, pool, skill) <= CROSS_FAMILY_CEILING[family], (pool, skill)


def test_each_skill_reads_highest_on_its_own_pool(engine_profiles):
    for own_pool, skill in POOL_OF.items():
        own = {t: engine_profiles[(own_pool, t)].skills[skill].stars for t in TIERS}
        for pool in POOL_OF:
            if pool == own_pool:
                continue
            ratio = np.mean([engine_profiles[(pool, t)].skills[skill].stars / own[t] for t in TIERS if own[t] > 0])
            assert ratio <= SPECIFICITY_CEILING[skill], (skill, pool, ratio)


def test_a_pools_own_skill_tops_its_family_on_most_of_its_charts(engine_profiles):
    for pool, own in POOL_OF.items():
        family = own[:2]
        top = sum(
            engine_profiles[(pool, t)].skills[own].stars
            > max(engine_profiles[(pool, t)].skills[k].stars for k in SKILLS if k.startswith(family) and k != own)
            for t in TIERS
        )
        assert top >= OWN_TOP_FLOOR[pool], (pool, top)


def test_the_skill_scales_are_the_fit_of_the_benchmark_charts(engine_profiles):
    path = REPO_ROOT / "tools" / "fit_skill_scale.py"
    spec = importlib.util.spec_from_file_location("fit_skill_scale", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fit = module.fit_all(engine_profiles)
    for skill in SKILLS:
        assert fit[skill][0] == pytest.approx(SKILL_SCALE[skill][0], rel=1e-9), skill
        assert fit[skill][1] == pytest.approx(SKILL_SCALE[skill][1], rel=1e-9), skill


def _rice(n: int, period: float = 0.1):
    """Single notes walking the columns, one every `period` s."""
    return [(k % 7, 1.0 + period * k, None) for k in range(n)]


def test_a_rice_chart_reads_zero_on_every_ln_skill_and_an_ln_chart_zero_on_every_rc_skill():
    rice = evaluate_notes(_rice(400))
    assert all(rice.skills[k].stars == 0.0 for k in SKILLS if k.startswith("ln"))
    assert rice.skills["rc_speed"].stars > 0

    holds = [(c, t, t + 0.3) for c, t, _ in _rice(400, 0.2)]
    ln = evaluate_notes(holds)
    assert all(ln.skills[k].stars == 0.0 for k in SKILLS if k.startswith("rc"))
    assert ln.skills["ln_general"].stars > 0


def test_a_single_column_jack_requires_jack_and_little_speed():
    jack = evaluate_notes([(3, 1.0 + 0.15 * k, None) for k in range(300)])
    assert jack.skills["rc_jack"].stars > 2 * jack.skills["rc_speed"].stars
