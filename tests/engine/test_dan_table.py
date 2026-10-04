"""
The dan table (ADR-0018 decision 2) is what the engine reads off the benchmark: the four Regular pools'
tier-level stars. `src/proj7k/dan_table.json` stores them to two decimals; this keeps it from drifting away
from the engine it describes.
"""

import numpy as np
import pytest

from engine_support import POOL_OF, SKILLS_LN, TIERS
from proj7k.dan import CANONICAL_DAN_SR, CANONICAL_DAN_TIERS
from proj7k.engine.scale import stars_of


def test_the_dan_table_is_the_engines_regular_tier_levels(engine_profiles):
    rc_pools = [pool for pool, skill in POOL_OF.items() if skill not in SKILLS_LN]
    assert list(CANONICAL_DAN_SR) == CANONICAL_DAN_TIERS == TIERS
    for tier in TIERS:
        level = stars_of(float(np.median([engine_profiles[(p, tier)].total_D for p in rc_pools])))
        assert CANONICAL_DAN_SR[tier] == pytest.approx(level, abs=0.006), tier
