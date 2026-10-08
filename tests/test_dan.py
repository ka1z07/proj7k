"""
Tests for proj7k.dan module (Canonical Dan Progression Hierarchy).
"""

import pytest

from proj7k.dan import CANONICAL_DAN_TIERS, parse_dan_tier


def test_canonical_dan_tiers_order():
    assert len(CANONICAL_DAN_TIERS) == 15
    assert CANONICAL_DAN_TIERS[0] == "0th"
    assert CANONICAL_DAN_TIERS[10] == "10th"
    assert CANONICAL_DAN_TIERS[11] == "Gamma"
    assert CANONICAL_DAN_TIERS[12] == "Azimuth"
    assert CANONICAL_DAN_TIERS[13] == "Zenith"
    assert CANONICAL_DAN_TIERS[14] == "Stellium"


def test_parse_dan_tier():
    assert parse_dan_tier("7th") == "7th"
    assert parse_dan_tier("7th Dan") == "7th"
    assert parse_dan_tier("Dan 7") == "7th"
    assert parse_dan_tier("7") == "7th"
    assert parse_dan_tier("P-7th") == "7th"
    assert parse_dan_tier("[P-7th]") == "7th"
    assert parse_dan_tier("gamma") == "Gamma"
    assert parse_dan_tier("stellium") == "Stellium"
    assert parse_dan_tier("0th") == "0th"

    with pytest.raises(ValueError):
        parse_dan_tier("InvalidDan123")


# --- The table of the spec v0.2 engine (ADR-0018 decision 2) -----------------------------------------

from proj7k.dan import CANONICAL_DAN_SR, estimate_canonical_dan  # noqa: E402


def test_the_dan_table_is_the_engines_regular_tier_levels_and_strictly_increasing():
    assert [CANONICAL_DAN_SR[t] for t in CANONICAL_DAN_TIERS] == [
        3.40, 3.94, 4.42, 5.10, 5.45, 5.74, 6.04, 6.55, 6.96, 7.40, 7.85, 8.44, 9.30, 10.00, 11.05,
    ]


def test_every_tier_star_value_reads_as_its_own_tier():
    for tier, stars in CANONICAL_DAN_SR.items():
        assert estimate_canonical_dan(stars) == tier


def test_a_rating_reads_as_the_nearest_tier_by_ratio_and_the_ends_are_floored():
    assert estimate_canonical_dan(0.5) == "0th"
    assert estimate_canonical_dan(3.0) == "0th"
    assert estimate_canonical_dan(3.64) == "0th"   # between 3.40 and 3.94, nearer the first
    assert estimate_canonical_dan(3.70) == "1st"
    assert estimate_canonical_dan(6.2) == "6th"
    assert estimate_canonical_dan(6.4) == "7th"
    assert estimate_canonical_dan(10.5) == "Zenith"  # between 10.00 and 11.05: the geometric midpoint is 10.51
    assert estimate_canonical_dan(10.55) == "Stellium"
    assert estimate_canonical_dan(30.0) == "Stellium"
