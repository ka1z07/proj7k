"""
Strain-Error Response Engine & 8-Dimension Dan Radar for 7K Player Replay Profiler.

Aligns discrete hit deviations against continuous 8-dimensional strain curves S_d(t)
computed by proj7k.strain, detects critical performance inflection thresholds
(Effective Strain Capacity) via Sigmoid/tanh response fitting, and maps capacities
to the 15-level Jinjin Dan progression hierarchy and continuous star rating (ADR-0012).
"""

from dataclasses import dataclass
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import curve_fit

from proj7k.dan import CANONICAL_DAN_TIERS, estimate_canonical_dan
from proj7k.parser import Beatmap7K, NoteType
from proj7k.profiler.matcher import AlignedHit, HitAlignmentResult, HitJudgment
from proj7k.radar import TECHNIQUE_NAMES
from proj7k.rating import aggregate_p_norm, apply_tanh_soft_cap
from proj7k.strain import (
    TechniqueStrainTimeseries,
    compute_8d_strain_timeseries,
    compute_raw_strain_star_rating,
)


@dataclass(frozen=True)
class StrainBin:
    """
    Hit error variance and miss rate statistics within a discrete strain slice [min, max).
    """
    bin_index: int
    strain_min: float
    strain_max: float
    strain_center: float
    total_notes: int
    hit_count: int
    miss_count: int
    miss_rate: float
    mean_offset_ms: float
    std_offset_ms: float
    ur: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bin_index": self.bin_index,
            "strain_min": round(self.strain_min, 2),
            "strain_max": round(self.strain_max, 2),
            "strain_center": round(self.strain_center, 2),
            "total_notes": self.total_notes,
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "miss_rate": round(self.miss_rate, 4),
            "mean_offset_ms": round(self.mean_offset_ms, 2),
            "std_offset_ms": round(self.std_offset_ms, 2),
            "ur": round(self.ur, 2),
        }


@dataclass(frozen=True)
class DimensionCapacityResult:
    """
    Player's effective strain capacity and Dan mapping for a single technique dimension.
    """
    dimension: str
    effective_capacity: float       # Inflection point S_eff in strain units
    star_rating: float              # Continuous star rating SR (with tanh soft cap)
    dan_tier: str                   # 15-level Jinjin Dan tier (0th .. Stellium)
    has_inflection: bool            # True if breakdown inflection was detected
    peak_chart_strain: float        # Maximum strain observed in the chart for this dimension
    tested: bool                    # True if the chart had significant strain in this dimension
    bins: List[StrainBin]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "effective_capacity": round(self.effective_capacity, 2),
            "star_rating": round(self.star_rating, 2),
            "dan_tier": self.dan_tier,
            "has_inflection": self.has_inflection,
            "peak_chart_strain": round(self.peak_chart_strain, 2),
            "tested": self.tested,
            "bins": [b.to_dict() for b in self.bins],
        }


@dataclass(frozen=True)
class SkillRadarReport:
    """
    8-dimensional player capability radar and canonical Jinjin Dan tier breakdown.
    """
    dimensions: Dict[str, DimensionCapacityResult]
    overall_dan: str
    dominant_technique: str
    bottleneck_technique: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_dan": self.overall_dan,
            "dominant_technique": self.dominant_technique,
            "bottleneck_technique": self.bottleneck_technique,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
        }


@dataclass(frozen=True)
class StrainResponseOptions:
    """Configuration options for response engine binning and fitting."""
    min_testable_strain: float = 5.0
    num_bins: int = 10
    min_samples_per_bin: int = 2
    breakdown_std_delta_ms: float = 12.0
    breakdown_miss_rate: float = 0.10


def _sigmoid_response(x: np.ndarray, y_min: float, delta_y: float, x0: float, k: float) -> np.ndarray:
    """Sigmoid degradation response curve."""
    z = np.clip(k * (x - x0), -30.0, 30.0)
    return y_min + delta_y / (1.0 + np.exp(-z))


def _tanh_response(x: np.ndarray, y_min: float, delta_y: float, x0: float, k: float) -> np.ndarray:
    """Hyperbolic tangent (tanh) threshold degradation curve."""
    z = np.clip(0.5 * k * (x - x0), -15.0, 15.0)
    return y_min + delta_y * 0.5 * (1.0 + np.tanh(z))


def _fit_inflection_threshold(
    strains: np.ndarray,
    scores: np.ndarray,
    s_min: float,
    s_max: float,
) -> float:
    """
    Fits Sigmoid / tanh threshold function to find the inflection point s0
    where performance degradation is steepest. Falls back to numerical midpoint crossing.
    """
    if len(strains) < 3:
        return float(np.median(strains))

    y_min_obs = float(np.min(scores))
    y_max_obs = float(np.max(scores))
    delta_y_obs = max(1.0, y_max_obs - y_min_obs)
    x0_init = float(np.median(strains))

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # Try both tanh and sigmoid parameterizations
        for fit_fn in (_tanh_response, _sigmoid_response):
            try:
                popt, _ = curve_fit(
                    fit_fn,
                    strains,
                    scores,
                    p0=[y_min_obs, delta_y_obs, x0_init, 0.1],
                    bounds=(
                        [0.0, 0.0, s_min, 0.005],
                        [y_max_obs * 1.5 + 10.0, delta_y_obs * 2.5 + 50.0, s_max, 2.0],
                    ),
                    maxfev=2000,
                )
                fitted_s0 = float(popt[2])
                if s_min <= fitted_s0 <= s_max:
                    return fitted_s0
            except Exception:
                continue

    # Robust numerical fallback: find strain where score crosses 50% midpoint
    target_mid = y_min_obs + 0.5 * delta_y_obs
    for i in range(len(strains) - 1):
        s1, s2 = strains[i], strains[i + 1]
        y1, y2 = scores[i], scores[i + 1]
        if (y1 <= target_mid <= y2) or (y2 <= target_mid <= y1):
            if abs(y2 - y1) > 1e-6:
                frac = (target_mid - y1) / (y2 - y1)
                return float(s1 + frac * (s2 - s1))
            return float(0.5 * (s1 + s2))

    return float(x0_init)


def _make_untested_result(dim: str, peak_strain: float) -> DimensionCapacityResult:
    """Helper to create an untested dimension result."""
    return DimensionCapacityResult(
        dimension=dim,
        effective_capacity=0.0,
        star_rating=0.0,
        dan_tier="0th",
        has_inflection=False,
        peak_chart_strain=peak_strain,
        tested=False,
        bins=[],
    )


def analyze_strain_response(
    alignment: HitAlignmentResult,
    beatmap: Beatmap7K,
    strain_timeseries: Optional[TechniqueStrainTimeseries] = None,
    options: Optional[StrainResponseOptions] = None,
) -> SkillRadarReport:
    """
    Analyzes player hit errors and misses against 8-dimensional continuous strain curves,
    detects critical inflection thresholds, and maps capabilities to the canonical Dan hierarchy.
    """
    opts = options or StrainResponseOptions()

    if strain_timeseries is None:
        strain_timeseries = compute_8d_strain_timeseries(beatmap)

    # 1. Point-to-point alignment of hits with instantaneous 8D strain values
    # Samples strain at the player's actual hit timestamp (or target time for misses)
    for hit in alignment.aligned_hits:
        t_sec = (hit.hit_time if hit.hit_time is not None else hit.target_time) / 1000.0
        hit.strains = strain_timeseries.get_strains_at(t_sec)

    results: Dict[str, DimensionCapacityResult] = {}

    for dim in TECHNIQUE_NAMES:
        curve = getattr(strain_timeseries, dim)
        peak_strain = float(max(curve)) if curve else 0.0

        if peak_strain < opts.min_testable_strain:
            results[dim] = _make_untested_result(dim, peak_strain)
            continue

        # Extract hit deviations and strain values for this dimension
        # For ln_release: decouple LN tail release deviation from head press (ADR-0012)
        note_data: List[Tuple[float, Optional[float], bool]] = []
        for hit in alignment.aligned_hits:
            s_val = hit.strains[dim] if hit.strains else 0.0
            if dim == "ln_release" and hit.note_type == NoteType.LN:
                is_miss = (hit.judgment == HitJudgment.MISS) or (hit.tail_offset_ms is None)
                err_val = hit.tail_offset_ms
            else:
                is_miss = (hit.judgment == HitJudgment.MISS) or (hit.offset_ms is None)
                err_val = hit.offset_ms
            note_data.append((s_val, err_val, is_miss))

        if not note_data:
            results[dim] = _make_untested_result(dim, peak_strain)
            continue

        strains_all = [x[0] for x in note_data]
        s_min = float(min(strains_all))
        s_max = float(max(strains_all))

        if s_max - s_min < 1e-4:
            s_max = s_min + 1.0

        num_bins = opts.num_bins
        bin_edges = np.linspace(0.0, s_max, num_bins + 1)
        bins_list: List[StrainBin] = []

        valid_bin_strains: List[float] = []
        valid_bin_scores: List[float] = []

        for b_idx in range(num_bins):
            b_low = float(bin_edges[b_idx])
            b_high = float(bin_edges[b_idx + 1])
            b_center = 0.5 * (b_low + b_high)

            # Filter notes in this bin
            bin_notes = [
                n for n in note_data
                if (b_low <= n[0] < b_high) or (b_idx == num_bins - 1 and b_low <= n[0] <= b_high)
            ]

            if not bin_notes:
                continue

            tot = len(bin_notes)
            hits_in_bin = [n[1] for n in bin_notes if not n[2] and n[1] is not None]
            hit_cnt = len(hits_in_bin)
            miss_cnt = tot - hit_cnt
            miss_rate = miss_cnt / tot if tot > 0 else 0.0

            if hit_cnt >= opts.min_samples_per_bin:
                mean_err = float(np.mean(hits_in_bin))
                std_err = float(np.std(hits_in_bin, ddof=1))
            elif hit_cnt == 1:
                mean_err = float(hits_in_bin[0])
                std_err = max(5.0, abs(hits_in_bin[0]))  # Meaningful fallback instead of artificial 0.0
            else:
                mean_err = 0.0
                std_err = 60.0  # High default error when all notes missed

            ur = std_err * 10.0

            bins_list.append(
                StrainBin(
                    bin_index=b_idx,
                    strain_min=b_low,
                    strain_max=b_high,
                    strain_center=b_center,
                    total_notes=tot,
                    hit_count=hit_cnt,
                    miss_count=miss_cnt,
                    miss_rate=miss_rate,
                    mean_offset_ms=mean_err,
                    std_offset_ms=std_err,
                    ur=ur,
                )
            )

            # Combined degradation metric: timing std + miss rate penalty
            combined_score = std_err + 200.0 * miss_rate
            valid_bin_strains.append(b_center)
            valid_bin_scores.append(combined_score)

        if not valid_bin_scores:
            raw_sr = compute_raw_strain_star_rating(peak_strain)
            sr = apply_tanh_soft_cap(raw_sr)
            results[dim] = DimensionCapacityResult(
                dimension=dim,
                effective_capacity=peak_strain,
                star_rating=sr,
                dan_tier=estimate_canonical_dan(sr),
                has_inflection=False,
                peak_chart_strain=peak_strain,
                tested=True,
                bins=bins_list,
            )
            continue

        # Check for degradation breakdown
        min_score = min(valid_bin_scores)
        max_score = max(valid_bin_scores)
        max_miss_rate = max(b.miss_rate for b in bins_list) if bins_list else 0.0

        is_breakdown = (
            (max_score - min_score >= opts.breakdown_std_delta_ms)
            or (max_miss_rate >= opts.breakdown_miss_rate)
        )

        if not is_breakdown:
            effective_cap = peak_strain
            has_inflection = False
        else:
            fitted_inflection = _fit_inflection_threshold(
                np.array(valid_bin_strains),
                np.array(valid_bin_scores),
                s_min=0.0,
                s_max=peak_strain,
            )
            effective_cap = float(np.clip(fitted_inflection, 0.0, peak_strain))
            has_inflection = True

            # Competence Gating: capacity cannot exceed the highest strain where the player
            # demonstrated controlled execution (miss rate <= 15% and UR <= 350)
            clean_bins = [
                b for b in bins_list
                if b.miss_rate <= 0.15 and b.ur <= 350.0 and b.total_notes >= opts.min_samples_per_bin
            ]
            if clean_bins:
                max_clean_strain = max(b.strain_max for b in clean_bins)
                effective_cap = min(effective_cap, max_clean_strain)
            elif bins_list and any(b.total_notes >= 5 for b in bins_list):
                # No clean bins exist: player failed across all strain levels
                effective_cap = 0.0

        raw_sr = compute_raw_strain_star_rating(effective_cap)
        sr = apply_tanh_soft_cap(raw_sr)
        dan = estimate_canonical_dan(sr)

        results[dim] = DimensionCapacityResult(
            dimension=dim,
            effective_capacity=effective_cap,
            star_rating=sr,
            dan_tier=dan,
            has_inflection=has_inflection,
            peak_chart_strain=peak_strain,
            tested=True,
            bins=bins_list,
        )

    # Calculate macro radar attributes using extremum-dominant p-norm (p=4)
    tested_results = [r for r in results.values() if r.tested]

    if tested_results:
        dominant_tech = max(tested_results, key=lambda r: r.star_rating).dimension
        bottleneck_technique = min(tested_results, key=lambda r: r.star_rating).dimension
        tested_dict = {r.dimension: r.star_rating for r in tested_results}
        overall_sr = aggregate_p_norm(tested_dict, p=4.0)
        overall_dan = estimate_canonical_dan(overall_sr)
    else:
        dominant_tech = "None"
        bottleneck_technique = "None"
        overall_dan = "0th"

    return SkillRadarReport(
        dimensions=results,
        overall_dan=overall_dan,
        dominant_technique=dominant_tech,
        bottleneck_technique=bottleneck_technique,
    )
