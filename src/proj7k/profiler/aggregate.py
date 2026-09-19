"""
Rolling Time-Window Aggregator and Macro Profile Engine (ADR-0012).

Provides Recent Rolling Form (e.g. 30 days) and All-time Peak Profile calculations,
extracts fatal peak strains from failed runs, filters aborted noise, computes
stability baseline UR exclusively from cleared runs, and generates historical trend
comparisons.
"""

from dataclasses import dataclass, field
import json
import time
from typing import Any, Dict, List, Optional

from proj7k.dan import CANONICAL_DAN_TIERS, estimate_canonical_dan
from proj7k.profiler.storage import MatchSnapshot, ProfilerStorage
from proj7k.radar import TECHNIQUE_NAMES
from proj7k.rating import aggregate_p_norm, apply_tanh_soft_cap
from proj7k.strain import compute_raw_strain_star_rating


@dataclass(frozen=True)
class DimensionMacroMetric:
    """
    Macro skill summary for a single technique dimension within a time window.
    """
    dimension: str
    effective_capacity: float  # Effective strain capacity demonstrated in window (CONTEXT.md)
    star_rating: float         # Continuous Star Rating (tanh soft capped)
    dan_tier: str              # Canonical Jinjin Dan tier
    match_count: int           # Number of tested runs in window
    has_inflection: bool       # Whether an inflection was observed

    @property
    def peak_capacity(self) -> float:
        """Alias for backward compatibility with peak_capacity."""
        return self.effective_capacity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "effective_capacity": round(self.effective_capacity, 2),
            "peak_capacity": round(self.effective_capacity, 2),
            "star_rating": round(self.star_rating, 2),
            "dan_tier": self.dan_tier,
            "match_count": self.match_count,
            "has_inflection": self.has_inflection,
        }


@dataclass
class MacroProfile:
    """
    Aggregated player performance profile across a designated temporal window.
    """
    player_name: str
    window_mode: str                           # "recent_rolling" or "all_time"
    horizon_days: Optional[float]              # Window size in days (None if all_time)
    start_timestamp: Optional[float]           # Window start (epoch seconds)
    end_timestamp: Optional[float]             # Window end (epoch seconds)
    total_matches: int                         # Total valid matches in window
    cleared_matches: int                       # Cleared / full runs
    failed_matches: int                        # Failed runs preserved for peak strain
    average_ur: Optional[float]                # Stability baseline UR (from cleared runs only)
    overall_dan: str
    overall_star_rating: float
    dominant_technique: str
    bottleneck_technique: str
    dimensions: Dict[str, DimensionMacroMetric]
    trend_comparison: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_name": self.player_name,
            "window_mode": self.window_mode,
            "horizon_days": self.horizon_days,
            "start_timestamp": round(self.start_timestamp, 2) if self.start_timestamp is not None else None,
            "end_timestamp": round(self.end_timestamp, 2) if self.end_timestamp is not None else None,
            "total_matches": self.total_matches,
            "cleared_matches": self.cleared_matches,
            "failed_matches": self.failed_matches,
            "average_ur": round(self.average_ur, 2) if self.average_ur is not None else None,
            "overall_dan": self.overall_dan,
            "overall_star_rating": round(self.overall_star_rating, 2),
            "dominant_technique": self.dominant_technique,
            "bottleneck_technique": self.bottleneck_technique,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "trend_comparison": self.trend_comparison,
        }


def aggregate_macro_profile(
    storage: ProfilerStorage,
    player_name: str,
    horizon_days: Optional[float] = 30.0,
    reference_timestamp: Optional[float] = None,
    compare_against_all_time: bool = False,
) -> MacroProfile:
    """
    Aggregates player snapshots into a structured MacroProfile.

    - If horizon_days is set (e.g. 30), selects matches in [ref - horizon_days, ref].
    - If horizon_days is None, selects all historical matches.
    - Stability baseline UR is calculated strictly from Cleared matches (ADR-0012).
    - Failed matches contribute fatal peak strains and capacities.
    """
    all_snapshots = storage.get_snapshots(player_name)

    if not all_snapshots:
        # Return empty profile
        empty_dims = {
            dim: DimensionMacroMetric(
                dimension=dim,
                effective_capacity=0.0,
                star_rating=0.0,
                dan_tier="0th Dan",
                match_count=0,
                has_inflection=False,
            )
            for dim in TECHNIQUE_NAMES
        }
        return MacroProfile(
            player_name=player_name,
            window_mode="all_time" if horizon_days is None else "recent_rolling",
            horizon_days=horizon_days,
            start_timestamp=None,
            end_timestamp=None,
            total_matches=0,
            cleared_matches=0,
            failed_matches=0,
            average_ur=None,
            overall_dan="0th Dan",
            overall_star_rating=0.0,
            dominant_technique="stream",
            bottleneck_technique="stream",
            dimensions=empty_dims,
            trend_comparison=None,
        )

    # 1. Determine temporal boundaries
    if horizon_days is None:
        window_mode = "all_time"
        snapshots = all_snapshots
        start_t = min(s.timestamp for s in snapshots)
        end_t = max(s.timestamp for s in snapshots)
    else:
        window_mode = "recent_rolling"
        ref_t = reference_timestamp if reference_timestamp is not None else all_snapshots[0].timestamp
        start_t = ref_t - (horizon_days * 86400.0)
        end_t = ref_t
        snapshots = [s for s in all_snapshots if start_t <= s.timestamp <= end_t]

    total_matches = len(snapshots)
    cleared_matches = sum(1 for s in snapshots if not s.is_failed)
    failed_matches = sum(1 for s in snapshots if s.is_failed)

    # 2. Stability baseline UR (ADR-0012: from cleared runs only)
    cleared_urs = [
        s.overall_ur for s in snapshots
        if (not s.is_failed) and (s.overall_ur is not None) and (s.overall_ur > 0)
    ]
    average_ur = (sum(cleared_urs) / len(cleared_urs)) if cleared_urs else None

    # 3. 8-Dimension Capacities Aggregation
    dimensions: Dict[str, DimensionMacroMetric] = {}

    observed_dims = set(TECHNIQUE_NAMES)
    for s in snapshots:
        observed_dims.update(s.capacities.keys())
        observed_dims.update(s.fatal_peak_strains.keys())
    all_dims = list(TECHNIQUE_NAMES) + [d for d in sorted(observed_dims) if d not in TECHNIQUE_NAMES]

    for dim in all_dims:
        dim_capacities: List[float] = []
        dim_star_ratings: List[float] = []
        tested_count = 0
        has_any_inflection = False

        for s in snapshots:
            # Check capacities recorded from strain-error analysis
            if dim in s.capacities:
                d_info = s.capacities[dim]
                if d_info.get("tested"):
                    tested_count += 1
                    cap = float(d_info.get("effective_capacity", 0.0))
                    sr_val = float(d_info.get("star_rating", 0.0))
                    if cap > 0:
                        dim_capacities.append(cap)
                    if sr_val > 0:
                        dim_star_ratings.append(sr_val)
                    if d_info.get("has_inflection"):
                        has_any_inflection = True

            # If failed run with fatal peak strain for this dimension, preserve it!
            if s.is_failed and dim in s.fatal_peak_strains:
                f_strain = float(s.fatal_peak_strains[dim])
                if f_strain > 0:
                    dim_capacities.append(f_strain)
                    f_sr = apply_tanh_soft_cap(compute_raw_strain_star_rating(f_strain))
                    dim_star_ratings.append(f_sr)

        if dim_star_ratings or dim_capacities:
            peak_cap = max(dim_capacities) if dim_capacities else 0.0
            if dim_star_ratings:
                sr = max(dim_star_ratings)
            else:
                raw_sr = compute_raw_strain_star_rating(peak_cap)
                sr = apply_tanh_soft_cap(raw_sr)
            dan = estimate_canonical_dan(sr)
        else:
            peak_cap = 0.0
            sr = 0.0
            dan = "0th Dan"

        dimensions[dim] = DimensionMacroMetric(
            dimension=dim,
            effective_capacity=peak_cap,
            star_rating=sr,
            dan_tier=dan,
            match_count=tested_count,
            has_inflection=has_any_inflection,
        )

    # 4. Overall Star Rating & Dan via extremum-dominant p-norm (p=4)
    tested_metrics = [m for m in dimensions.values() if m.star_rating > 0]
    if tested_metrics:
        dominant_tech = max(tested_metrics, key=lambda m: m.star_rating).dimension
        bottleneck_tech = min(tested_metrics, key=lambda m: m.star_rating).dimension
        tested_sr_dict = {m.dimension: m.star_rating for m in tested_metrics}
        overall_sr = aggregate_p_norm(tested_sr_dict, p=4.0)
        overall_dan = estimate_canonical_dan(overall_sr)
    else:
        dominant_tech = "stream"
        bottleneck_tech = "stream"
        overall_sr = 0.0
        overall_dan = "0th Dan"

    # 5. Historical Trend Comparison
    trend_comparison = None
    if compare_against_all_time and window_mode != "all_time":
        all_time_profile = aggregate_macro_profile(
            storage=storage,
            player_name=player_name,
            horizon_days=None,
            reference_timestamp=reference_timestamp,
            compare_against_all_time=False,
        )

        dim_deltas = {}
        for dim in all_dims:
            recent_sr = dimensions[dim].star_rating
            all_time_dim = all_time_profile.dimensions.get(
                dim, DimensionMacroMetric(dim, 0.0, 0.0, "0th Dan", 0, False)
            )
            all_time_sr = all_time_dim.star_rating
            delta = recent_sr - all_time_sr
            if recent_sr > 0 and delta >= 0.0:
                status = "Peak Form"
            elif delta >= -0.3:
                status = "In Range"
            else:
                status = "Slump"

            dim_deltas[dim] = {
                "recent_sr": round(recent_sr, 2),
                "all_time_sr": round(all_time_sr, 2),
                "delta_sr": round(delta, 2),
                "recent_dan": dimensions[dim].dan_tier,
                "all_time_dan": all_time_dim.dan_tier,
                "status": status,
            }

        trend_comparison = {
            "all_time_overall_dan": all_time_profile.overall_dan,
            "all_time_overall_sr": round(all_time_profile.overall_star_rating, 2),
            "all_time_average_ur": round(all_time_profile.average_ur, 2) if all_time_profile.average_ur else None,
            "all_time_dominant": all_time_profile.dominant_technique,
            "all_time_bottleneck": all_time_profile.bottleneck_technique,
            "dimension_deltas": dim_deltas,
        }

    return MacroProfile(
        player_name=player_name,
        window_mode=window_mode,
        horizon_days=horizon_days,
        start_timestamp=start_t if total_matches > 0 else None,
        end_timestamp=end_t if total_matches > 0 else None,
        total_matches=total_matches,
        cleared_matches=cleared_matches,
        failed_matches=failed_matches,
        average_ur=average_ur,
        overall_dan=overall_dan,
        overall_star_rating=overall_sr,
        dominant_technique=dominant_tech,
        bottleneck_technique=bottleneck_tech,
        dimensions=dimensions,
        trend_comparison=trend_comparison,
    )
