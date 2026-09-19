"""
TwoTierDanMapper - Dual-tier Dan cascade mapping engine (ADR-0011, SPEC-P5.1-03).

Resolves target Dan or continuous star rating into target strain S_target,
technique radar target profile, and benchmark feature centroids:
- Tier 1: Ground-truth retrieval from distilled_ground_truth.json for known
  dominant technique and standard Dan tier.
- Tier 2: Continuous interpolation along the 15-tier Canonical Dan Progression
  Hierarchy and inversion of the physical strain star rating formula.
"""

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from proj7k.parser import Beatmap7K
from proj7k.radar import TECHNIQUE_NAMES, TechniqueRadar, compute_technique_radar
from proj7k.rating import RatingOptions
from proj7k.strain import compute_raw_strain_star_rating

from proj7k.dan import (
    CANONICAL_DAN_SR,
    CANONICAL_DAN_TIERS,
    estimate_canonical_dan,
    parse_dan_tier,
)

TECH_ALIAS_MAP: Dict[str, str] = {
    "jack": "Regular Jack",
    "regular_jack": "Regular Jack",
    "regular jack": "Regular Jack",
    "tech": "Regular Tech",
    "regular_tech": "Regular Tech",
    "regular tech": "Regular Tech",
    "speed": "Regular Speed",
    "regular_speed": "Regular Speed",
    "regular speed": "Regular Speed",
    "stream": "Regular Stream",
    "regular_stream": "Regular Stream",
    "regular stream": "Regular Stream",
    "ln_general": "LN General",
    "ln general": "LN General",
    "ln_tech": "LN Tech",
    "ln tech": "LN Tech",
    "ln_inverse": "LN Inverse",
    "ln inverse": "LN Inverse",
    "ln_release": "LN Release",
    "ln release": "LN Release",
}

CANONICAL_TO_TECH_KEY: Dict[str, str] = {
    "Regular Jack": "jack",
    "Regular Tech": "tech",
    "Regular Speed": "speed",
    "Regular Stream": "stream",
    "LN General": "ln_general",
    "LN Tech": "ln_tech",
    "LN Inverse": "ln_inverse",
    "LN Release": "ln_release",
}


def normalize_technique_name(tech: str) -> str:
    """Normalizes any technique name to its canonical name in distilled_ground_truth.json."""
    clean = tech.strip().lower().replace("-", "_")
    if clean in TECH_ALIAS_MAP:
        return TECH_ALIAS_MAP[clean]
    clean_no_reg = clean.replace("regular_", "").replace("regular ", "")
    if clean_no_reg in TECH_ALIAS_MAP:
        return TECH_ALIAS_MAP[clean_no_reg]
    return tech




def star_rating_to_strain(
    sr: float,
    options: Optional[RatingOptions] = None,
) -> float:
    """
    Inverts the physical strain star rating formula:
    SR_raw = a * S^exp + b  ==>  S = max(0, (SR - b) / a)^(1 / exp).
    Uses canonical constants from RatingOptions (strain_a, strain_b, strain_exp).
    """
    opts = options or RatingOptions()
    a = opts.strain_a
    b = opts.strain_b
    exp = opts.strain_exp

    if sr <= b:
        return 0.0
    return math.pow((sr - b) / a, 1.0 / exp)


@dataclass(frozen=True)
class DanTarget:
    """Target difficulty specifications resolved by TwoTierDanMapper."""
    target_dan: str
    target_sr: float
    target_strain: float
    dominant_skill: str
    features: Dict[str, float] = field(default_factory=dict)
    radar_profile: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_dan": self.target_dan,
            "target_sr": round(self.target_sr, 3),
            "target_strain": round(self.target_strain, 3),
            "dominant_skill": self.dominant_skill,
            "features": {k: round(v, 4) for k, v in self.features.items()},
            "radar_profile": {k: round(v, 4) for k, v in self.radar_profile.items()},
        }


class TwoTierDanMapper:
    """
    Two-Tier Dan Cascade Mapping Engine (SPEC-P5.1-03, ADR-0011):
    1. Tier 1: Distilled Ground Truth Retrieval:
       Fetches the benchmark fingerprint and exact tier profile from
       docs/research/distilled_ground_truth.json.
    2. Tier 2: Canonical Dan Progression Hierarchy & SR Scale Inversion:
       Provides smooth continuous interpolation for custom star ratings (--target-sr)
       and unknown tiers.
    """

    def __init__(
        self,
        ground_truth_path: Optional[Union[str, Path]] = None,
        rating_options: Optional[RatingOptions] = None,
    ):
        self.rating_options = rating_options or RatingOptions()
        self._ground_truth: Dict[str, Any] = {}
        if ground_truth_path is None:
            candidates = [
                Path("docs/research/distilled_ground_truth.json"),
                Path(__file__).resolve().parent.parent.parent.parent / "docs" / "research" / "distilled_ground_truth.json",
            ]
            for p in candidates:
                if p.exists():
                    ground_truth_path = p
                    break

        if ground_truth_path and Path(ground_truth_path).exists():
            try:
                with open(ground_truth_path, "r", encoding="utf-8") as f:
                    self._ground_truth = json.load(f)
            except Exception:
                self._ground_truth = {}

    def resolve(
        self,
        target_dan: Optional[str] = None,
        target_sr: Optional[float] = None,
        dominant_skill: Optional[str] = None,
    ) -> DanTarget:
        """
        Resolves target Dan tier and/or target star rating into a complete DanTarget.
        """
        skill_key = dominant_skill.strip().lower() if dominant_skill else "jack"
        canonical_tech_name = normalize_technique_name(skill_key)
        short_tech_name = CANONICAL_TO_TECH_KEY.get(canonical_tech_name, skill_key)

        # 1. Resolve effective SR and canonical Dan name
        if target_sr is not None:
            effective_sr = float(target_sr)
            if target_dan is not None:
                canonical_dan = parse_dan_tier(target_dan)
            else:
                canonical_dan = self._estimate_closest_dan(effective_sr)
        elif target_dan is not None:
            canonical_dan = parse_dan_tier(target_dan)
            effective_sr = CANONICAL_DAN_SR[canonical_dan]
        else:
            canonical_dan = "7th"
            effective_sr = CANONICAL_DAN_SR["7th"]

        target_strain = star_rating_to_strain(effective_sr, self.rating_options)

        # 2. Try Tier 1 Lookup from distilled_ground_truth.json
        tier_profile = self._get_tier_profile(canonical_tech_name, canonical_dan)
        features: Dict[str, float] = {}
        radar_profile: Dict[str, float] = {}

        tech_entry = self._ground_truth.get("techniques", {}).get(canonical_tech_name, {})
        fingerprint = tech_entry.get("fingerprint", {})

        if tier_profile and "features" in tier_profile:
            features = dict(tier_profile["features"])
        else:
            features = self._interpolate_features(canonical_tech_name, effective_sr)

        # 3. Build radar profile from ground truth or technique orientation
        gt_radar = fingerprint.get("radar_profile", {})
        for t in TECHNIQUE_NAMES:
            if t == short_tech_name:
                radar_profile[t] = effective_sr
            else:
                # Derive secondary proportion from ground truth separability if available
                weight = float(gt_radar.get(t, 0.15))
                radar_profile[t] = round(effective_sr * max(0.05, min(0.35, weight)), 2)

        return DanTarget(
            target_dan=canonical_dan,
            target_sr=effective_sr,
            target_strain=target_strain,
            dominant_skill=short_tech_name,
            features=features,
            radar_profile=radar_profile,
        )

    def resolve_from_beatmap(
        self,
        beatmap: Beatmap7K,
        target_dan: Optional[str] = None,
        target_sr: Optional[float] = None,
    ) -> DanTarget:
        """Resolves target, auto-detecting dominant technique from the beatmap."""
        radar = compute_technique_radar(beatmap)
        dominant = radar.dominant_technique
        return self.resolve(target_dan=target_dan, target_sr=target_sr, dominant_skill=dominant)

    def _get_tier_profile(self, tech_name: str, dan_tier: str) -> Optional[Dict[str, Any]]:
        """Retrieves ground truth tier profile if present."""
        techs = self._ground_truth.get("techniques", {})
        if tech_name in techs:
            profiles = techs[tech_name].get("tier_profiles", {})
            if dan_tier in profiles:
                return profiles[dan_tier]
        return None

    def _estimate_closest_dan(self, sr: float) -> str:
        """Finds closest canonical Dan tier for a given star rating."""
        if sr <= CANONICAL_DAN_SR["0th"]:
            return "0th"
        if sr >= CANONICAL_DAN_SR["Stellium"]:
            return "Stellium"

        best_tier = "7th"
        best_diff = float("inf")
        for tier in CANONICAL_DAN_TIERS:
            diff = abs(CANONICAL_DAN_SR[tier] - sr)
            if diff < best_diff:
                best_diff = diff
                best_tier = tier
        return best_tier

    def _interpolate_features(self, tech_name: str, sr: float) -> Dict[str, float]:
        """Interpolates feature vectors between two surrounding canonical tiers."""
        if sr <= CANONICAL_DAN_SR["0th"]:
            p = self._get_tier_profile(tech_name, "0th")
            return dict(p["features"]) if p and "features" in p else {}
        if sr >= CANONICAL_DAN_SR["Stellium"]:
            p = self._get_tier_profile(tech_name, "Stellium")
            return dict(p["features"]) if p and "features" in p else {}

        lower_tier = "0th"
        upper_tier = "Stellium"
        for i in range(len(CANONICAL_DAN_TIERS) - 1):
            t_low = CANONICAL_DAN_TIERS[i]
            t_high = CANONICAL_DAN_TIERS[i + 1]
            if CANONICAL_DAN_SR[t_low] <= sr <= CANONICAL_DAN_SR[t_high]:
                lower_tier = t_low
                upper_tier = t_high
                break

        sr_low = CANONICAL_DAN_SR[lower_tier]
        sr_high = CANONICAL_DAN_SR[upper_tier]
        factor = (sr - sr_low) / max(1e-6, sr_high - sr_low)

        p_low = self._get_tier_profile(tech_name, lower_tier)
        p_high = self._get_tier_profile(tech_name, upper_tier)

        f_low = p_low.get("features", {}) if p_low else {}
        f_high = p_high.get("features", {}) if p_high else {}

        all_keys = set(f_low.keys()) | set(f_high.keys())
        interpolated: Dict[str, float] = {}
        for k in all_keys:
            v_low = float(f_low.get(k, 0.0))
            v_high = float(f_high.get(k, 0.0))
            interpolated[k] = (1.0 - factor) * v_low + factor * v_high

        return interpolated
