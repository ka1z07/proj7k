"""
proj7k.dan - Authoritative Jinjin 7K Dan hierarchy and tier estimation.

Implements Canonical Dan Progression Hierarchy (0th to Stellium) as single source of truth:
- CANONICAL_DAN_TIERS: 15 strictly ordered progression tiers.
- CANONICAL_DAN_SR: Benchmark star ratings calibrated against ground-truth dataset.
- estimate_canonical_dan: Continuous star rating to canonical Dan tier mapping.
- parse_dan_tier: Robust user input string normalizer.
"""

from typing import Dict, List
import re

CANONICAL_DAN_TIERS: List[str] = [
    "0th", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th",
    "8th", "9th", "10th", "Gamma", "Azimuth", "Zenith", "Stellium",
]

# Canonical Dan benchmark star ratings (ADR-0011, CONTEXT.md)
CANONICAL_DAN_SR: Dict[str, float] = {
    "0th": 3.2,
    "1st": 3.7,
    "2nd": 4.1,
    "3rd": 4.5,
    "4th": 4.9,
    "5th": 5.3,
    "6th": 5.7,
    "7th": 6.1,
    "8th": 6.5,
    "9th": 6.9,
    "10th": 7.4,
    "Gamma": 8.0,
    "Azimuth": 8.8,
    "Zenith": 9.7,
    "Stellium": 10.5,
}

TIER_SYNONYMS: Dict[str, str] = {
    "0": "0th", "0th": "0th", "zero": "0th", "zeroth": "0th",
    "1": "1st", "1st": "1st", "first": "1st",
    "2": "2nd", "2nd": "2nd", "second": "2nd",
    "3": "3rd", "3rd": "3rd", "third": "3rd",
    "4": "4th", "4th": "4th", "fourth": "4th",
    "5": "5th", "5th": "5th", "fifth": "5th",
    "6": "6th", "6th": "6th", "sixth": "6th",
    "7": "7th", "7th": "7th", "seventh": "7th",
    "8": "8th", "8th": "8th", "eighth": "8th",
    "9": "9th", "9th": "9th", "ninth": "9th",
    "10": "10th", "10th": "10th", "tenth": "10th",
    "gamma": "Gamma",
    "azimuth": "Azimuth",
    "zenith": "Zenith",
    "stellium": "Stellium",
}


def parse_dan_tier(dan_str: str) -> str:
    """
    Parses a user-supplied Dan tier string into canonical Dan name.
    Supports formats like '7th', '7th Dan', 'Dan 7', '7', 'p-7th', 'Gamma', etc.
    Raises ValueError if unrecognized.
    """
    clean = dan_str.strip().lower()
    clean = re.sub(r"^\[?p-?", "", clean)
    clean = re.sub(r"\]$", "", clean)
    clean = clean.replace("dan", "").strip()

    if clean in TIER_SYNONYMS:
        return TIER_SYNONYMS[clean]

    for tier in CANONICAL_DAN_TIERS:
        if tier.lower() == clean:
            return tier

    raise ValueError(f"Unrecognized Dan tier '{dan_str}'. Expected one of {CANONICAL_DAN_TIERS}")


def estimate_canonical_dan(star_rating: float) -> str:
    """
    Estimates canonical Jinjin 7K Dan benchmark tier from intrinsic star rating.
    Anchored to Canonical Dan Progression Hierarchy (0th ~ 3.2★, 5th ~ 5.3★, 10th ~ 7.4★, Stellium >= 10.5★).
    Sub-0th star ratings (< 3.5★) are floored to '0th' to maintain monotonic whole-sequence coverage.
    """
    if star_rating < 3.5:
        return "0th"
    if star_rating < 4.0:
        return "1st"
    if star_rating < 4.4:
        return "2nd"
    if star_rating < 4.8:
        return "3rd"
    if star_rating < 5.2:
        return "4th"
    if star_rating < 5.6:
        return "5th"
    if star_rating < 6.0:
        return "6th"
    if star_rating < 6.5:
        return "7th"
    if star_rating < 7.0:
        return "8th"
    if star_rating < 7.5:
        return "9th"
    if star_rating < 8.2:
        return "10th"
    if star_rating < 9.0:
        return "Gamma"
    if star_rating < 9.8:
        return "Azimuth"
    if star_rating < 10.6:
        return "Zenith"
    return "Stellium"
