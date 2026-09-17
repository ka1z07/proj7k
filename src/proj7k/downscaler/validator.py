"""
DualGateValidator - Dual-gate technique preservation verification engine
(ADR-0011, SPEC-P5.1-03).

Enforces strict preservation of macro-contour and microscopic technique signatures
between original and downscaled beatmaps:
- Gate 1: Macro Contour & Dominant Invariant:
  cos(R_downscaled, R_orig) >= 0.80 and 1st-rank dominant technique conserved.
- Gate 2: Microscopic Centroid Confidence Band:
  Protects core physiological metrics (e.g. hold_pct, density ratios)
  against degeneration into an unrelated chart category or drift outside target bounds.
"""

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple, Union

from proj7k.features import BeatmapFeatures, extract_beatmap_features
from proj7k.parser import Beatmap7K
from proj7k.radar import TECHNIQUE_NAMES, TechniqueRadar, compute_technique_radar
from proj7k.downscaler.mapper import DanTarget


def compute_radar_cosine_similarity(
    radar1: TechniqueRadar,
    radar2: TechniqueRadar,
) -> float:
    """
    Computes cosine similarity between two 8D technique radar score vectors:
    cos(R1, R2) = (R1 . R2) / (||R1|| * ||R2||).
    """
    v1 = radar1.to_vector()
    v2 = radar2.to_vector()

    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))

    if norm1 <= 1e-9 and norm2 <= 1e-9:
        return 1.0
    if norm1 <= 1e-9 or norm2 <= 1e-9:
        return 0.0

    sim = dot / (norm1 * norm2)
    return max(-1.0, min(1.0, sim))


@dataclass(frozen=True)
class ValidationResult:
    """Validation report returned by DualGateValidator."""
    passed: bool
    cosine_similarity: float
    dominant_conserved: bool
    dominant_technique_orig: str
    dominant_technique_downscaled: str
    gate1_passed: bool
    gate2_passed: bool
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def dominant_technique_nerfed(self) -> str:
        """Alias for backward compatibility."""
        return self.dominant_technique_downscaled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "cosine_similarity": round(self.cosine_similarity, 4),
            "dominant_conserved": self.dominant_conserved,
            "dominant_technique_orig": self.dominant_technique_orig,
            "dominant_technique_downscaled": self.dominant_technique_downscaled,
            "gate1_passed": self.gate1_passed,
            "gate2_passed": self.gate2_passed,
            "details": self.details,
        }


class DualGateValidator:
    """
    Dual-Gate Technique Preservation Validator (ADR-0011, SPEC-P5.1-03):
    Verifies that the downscaled beatmap satisfies:
    1. Gate 1: Macro-contour cosine similarity >= min_cosine_similarity (default 0.80)
       and dominant technique remains identical to original.
    2. Gate 2: Microscopic centroid confidence bounds (e.g. hold_pct bounds,
       jack/stream relative ordering, not collapsing into degenerate silence,
       and aligning within confidence bounds of target Dan).
    """

    def __init__(
        self,
        min_cosine_similarity: float = 0.80,
    ):
        self.min_cosine_similarity = min_cosine_similarity

    def validate(
        self,
        orig_beatmap_or_radar: Union[Beatmap7K, TechniqueRadar],
        downscaled_beatmap: Beatmap7K,
        target: Optional[DanTarget] = None,
    ) -> ValidationResult:
        """
        Validates technique preservation between original and downscaled beatmap.
        """
        if isinstance(orig_beatmap_or_radar, TechniqueRadar):
            radar_orig = orig_beatmap_or_radar
            feat_orig = None
        else:
            radar_orig = compute_technique_radar(orig_beatmap_or_radar)
            feat_orig = extract_beatmap_features(orig_beatmap_or_radar)

        radar_downscaled = compute_technique_radar(downscaled_beatmap)
        feat_downscaled = extract_beatmap_features(downscaled_beatmap)

        # Gate 1: Macro contour and dominant conservation
        cosine_sim = compute_radar_cosine_similarity(radar_orig, radar_downscaled)
        dominant_orig = radar_orig.dominant_technique
        dominant_downscaled = radar_downscaled.dominant_technique

        dominant_conserved = (dominant_orig == dominant_downscaled)
        gate1_passed = (cosine_sim >= self.min_cosine_similarity) and dominant_conserved

        # Gate 2: Microscopic centroid confidence band
        gate2_passed = True
        gate2_violations: List[str] = []

        # 2a. Rice vs LN mode collapse check:
        if feat_orig and feat_orig.hold_pct < 0.15:
            if feat_downscaled.hold_pct > 0.25:
                gate2_passed = False
                gate2_violations.append(
                    f"Rice chart mutated into LN: hold_pct jumped from {feat_orig.hold_pct:.3f} to {feat_downscaled.hold_pct:.3f}"
                )
        if feat_orig and feat_orig.hold_pct >= 0.40:
            if feat_downscaled.hold_pct < 0.15:
                gate2_passed = False
                gate2_violations.append(
                    f"LN chart lost holds: hold_pct dropped from {feat_orig.hold_pct:.3f} to {feat_downscaled.hold_pct:.3f}"
                )

        # 2b. Minimum note count sanity check (not wiped to empty)
        if feat_downscaled.total_notes < 5:
            gate2_passed = False
            gate2_violations.append("Total notes wiped out (< 5 notes remaining).")

        # 2c. Target Dan Microscopic Centroid Confidence Band Verification
        if target and target.features:
            tgt_hold = target.features.get("hold_pct", 0.0)
            # If target specifies pure Rice chart (hold_pct <= 0.05), downscaled chart must satisfy hold_pct <= 0.20
            if tgt_hold <= 0.05 and feat_downscaled.hold_pct > 0.20:
                gate2_passed = False
                gate2_violations.append(
                    f"Hold percentage {feat_downscaled.hold_pct:.3f} exceeded target Rice confidence ceiling 0.20"
                )
            # If target specifies LN chart (hold_pct >= 0.30), downscaled chart must satisfy hold_pct >= 0.15
            if tgt_hold >= 0.30 and feat_downscaled.hold_pct < 0.15:
                gate2_passed = False
                gate2_violations.append(
                    f"Hold percentage {feat_downscaled.hold_pct:.3f} fell below target LN confidence floor 0.15"
                )

        overall_passed = gate1_passed and gate2_passed

        details = {
            "min_cosine_similarity": self.min_cosine_similarity,
            "gate1_violations": [] if gate1_passed else (
                [f"Cosine similarity {cosine_sim:.4f} < {self.min_cosine_similarity}"] if cosine_sim < self.min_cosine_similarity else []
            ) + ([f"Dominant technique shifted from '{dominant_orig}' to '{dominant_downscaled}'"] if not dominant_conserved else []),
            "gate2_violations": gate2_violations,
            "radar_orig": radar_orig.to_dict(),
            "radar_downscaled": radar_downscaled.to_dict(),
        }

        return ValidationResult(
            passed=overall_passed,
            cosine_similarity=cosine_sim,
            dominant_conserved=dominant_conserved,
            dominant_technique_orig=dominant_orig,
            dominant_technique_downscaled=dominant_downscaled,
            gate1_passed=gate1_passed,
            gate2_passed=gate2_passed,
            details=details,
        )
