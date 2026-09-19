"""
Tests for proj7k.dan module (Canonical Dan Progression Hierarchy).
"""

import pytest

from proj7k.dan import (
    CANONICAL_DAN_TIERS,
    CANONICAL_DAN_SR,
    estimate_canonical_dan,
    parse_dan_tier,
)


def test_canonical_dan_tiers_order():
    assert len(CANONICAL_DAN_TIERS) == 15
    assert CANONICAL_DAN_TIERS[0] == "0th"
    assert CANONICAL_DAN_TIERS[10] == "10th"
    assert CANONICAL_DAN_TIERS[11] == "Gamma"
    assert CANONICAL_DAN_TIERS[12] == "Azimuth"
    assert CANONICAL_DAN_TIERS[13] == "Zenith"
    assert CANONICAL_DAN_TIERS[14] == "Stellium"


def test_canonical_dan_sr_monotonic():
    ratings = [CANONICAL_DAN_SR[t] for t in CANONICAL_DAN_TIERS]
    assert ratings == sorted(ratings)
    assert len(ratings) == len(set(ratings))  # Strictly increasing


def test_estimate_canonical_dan_sub_zero_floored():
    # Sub-0th star ratings (< 3.5★) are floored to '0th'
    assert estimate_canonical_dan(0.5) == "0th"
    assert estimate_canonical_dan(1.8) == "0th"
    assert estimate_canonical_dan(3.2) == "0th"
    assert estimate_canonical_dan(3.49) == "0th"


def test_estimate_canonical_dan_tiers():
    assert estimate_canonical_dan(3.5) == "1st"
    assert estimate_canonical_dan(3.95) == "1st"
    assert estimate_canonical_dan(4.0) == "2nd"
    assert estimate_canonical_dan(4.5) == "3rd"
    assert estimate_canonical_dan(5.0) == "4th"
    assert estimate_canonical_dan(5.3) == "5th"
    assert estimate_canonical_dan(5.8) == "6th"
    assert estimate_canonical_dan(6.42) == "7th"
    assert estimate_canonical_dan(6.8) == "8th"
    assert estimate_canonical_dan(7.3) == "9th"
    assert estimate_canonical_dan(8.0) == "10th"
    assert estimate_canonical_dan(8.8) == "Gamma"
    assert estimate_canonical_dan(9.5) == "Azimuth"
    assert estimate_canonical_dan(10.2) == "Zenith"
    assert estimate_canonical_dan(10.6) == "Stellium"
    assert estimate_canonical_dan(12.5) == "Stellium"


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
