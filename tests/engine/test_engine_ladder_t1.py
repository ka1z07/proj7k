"""
T1 (spec §14): for every skill k, D_k over the benchmark pool of k rises strictly with the tier; report the
smallest gap.

Recorded rather than passed (ADR-0017 decision 3): every pool has adjacent-tier inversions, 24 in all. Since
ADR-0056 a pool's own skill is that skill's own requirement (its own cost on its own scale), no longer the total's
reading under the skill's membership, so this is a different ladder from the one the eight inversions of v0.2.2
were counted on: a tier can require less of its own skill than the tier below while reading harder in total.
Each such pool is an `xfail(strict=True)`, so the day one of them climbs cleanly the test goes red and the entry
must be deleted; `test_the_known_inversions_do_not_grow` is the per-pool ratchet behind it. The same counts are
what `guard.OWN_SKILL_RATCHET` holds the batch gate to.
"""

import pytest

from engine_support import POOL_OF, TIERS

#: Adjacent-tier inversions per pool of the pool's own skill, as measured on the 120 charts.
KNOWN_INVERSIONS = {
    "Regular Jack": 4,
    "Regular Tech": 6,
    "Regular Speed": 1,
    "Regular Stream": 1,
    "LN General": 3,
    "LN Tech": 4,
    "LN Inverse": 2,
    "LN Release": 3,
}
TOTAL_KNOWN_INVERSIONS = 24


def _own_skill_D(engine_profiles, pool):
    skill = POOL_OF[pool]
    return [engine_profiles[(pool, tier)].skills[skill].D for tier in TIERS]


def _inversions(values):
    return [(TIERS[i], TIERS[i + 1], values[i] - values[i + 1]) for i in range(len(values) - 1) if values[i + 1] <= values[i]]


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param(pool, marks=pytest.mark.xfail(strict=True, reason=f"T1: {KNOWN_INVERSIONS[pool]} inversion(s), tracked"))
        if pool in KNOWN_INVERSIONS
        else pool
        for pool in POOL_OF
    ],
)
def test_a_pools_own_skill_rises_strictly_over_the_fifteen_tiers(engine_profiles, pool):
    values = _own_skill_D(engine_profiles, pool)
    gaps = [b - a for a, b in zip(values, values[1:])]

    assert min(gaps) > 0, f"smallest gap {min(gaps):.3f}; inversions {_inversions(values)}"


def test_the_known_inversions_do_not_grow(engine_profiles):
    counts = {pool: len(_inversions(_own_skill_D(engine_profiles, pool))) for pool in POOL_OF}

    for pool, count in counts.items():
        assert count <= KNOWN_INVERSIONS.get(pool, 0), (pool, count)
    assert sum(counts.values()) == TOTAL_KNOWN_INVERSIONS
