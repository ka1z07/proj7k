"""
Rolling Time-Window Aggregator and Macro Profile Engine (ADR-0012).

Provides Recent Rolling Form (e.g. 30 days) and All-time Peak Profile calculations,
extracts the demand failed runs broke under, filters aborted noise, computes
stability baseline UR exclusively from cleared runs, and generates historical trend
comparisons.
"""

from dataclasses import dataclass, field
import json
import math
import statistics
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from proj7k.dan import CANONICAL_DAN_TIERS, estimate_canonical_dan
from proj7k.engine.params import DEFAULT
from proj7k.engine.scale import stars_of
from proj7k.engine.skills import SKILL_TECH_KEY, SKILLS
from proj7k.profiler.storage import SNAPSHOT_ENGINE, MatchSnapshot, ProfilerStorage, snapshot_is_current
from proj7k.field import TECH_KEYS


#: A skill's level is fitted across plays once this many plays in the window tested it; with fewer, it is the
#: median of what each play read.
MIN_FIT_PLAYS: int = 5

_EPS_OF_KEY: Dict[str, float] = {
    SKILL_TECH_KEY[name]: (DEFAULT.eps_rc if name.startswith("rc") else DEFAULT.eps_ln) for name in SKILLS
}


def level_across_plays(points: Sequence[Tuple[float, float, float]], eps: float) -> Optional[float]:
    """
    The level of a player across plays, in the engine's unit (ADR-0028). `points` are `(chart level D, events
    played, accuracy lost over them)`, one per play. The loss rate is fitted against ln D with a logistic
    (each play weighted by the square root of its events); the level is the D at which the fit loses exactly
    `eps`, the tolerance at which the engine says a chart's difficulty is the player's level (ADR-0020). It
    is held to the range of charts played: nothing past the hardest or easiest chart is read off the fit.
    None when the plays are too few, or harder charts did not cost more (the fit's slope is not positive).
    """
    pts = [(math.log(D), lost / events, events) for D, events, lost in points if D > 0 and events > 0]
    if len(pts) < MIN_FIT_PLAYS:
        return None
    x = np.array([p[0] for p in pts])
    y = np.clip(np.array([p[1] for p in pts]), 0.0, 1.0)
    w = np.sqrt(np.array([p[2] for p in pts]))
    if np.ptp(x) <= 1e-9:
        return None

    def nll(ab: np.ndarray) -> float:
        q = np.clip(expit(ab[0] + ab[1] * x), 1e-9, 1.0 - 1e-9)
        return float(-np.sum(w * (y * np.log(q) + (1.0 - y) * np.log(1.0 - q))))

    a, b = minimize(nll, np.array([-5.0, 1.0]), method="BFGS").x
    if not b > 0:
        return None
    ln_level = (math.log(eps / (1.0 - eps)) - a) / b
    return float(math.exp(min(max(ln_level, float(x.min())), float(x.max()))))


@dataclass(frozen=True)
class DimensionMacroMetric:
    """
    Macro skill summary for a single technique dimension within a time window.
    """
    dimension: str
    effective_capacity: float  # Level across the window's plays, in the engine's unit (ADR-0020, ADR-0028)
    star_rating: float         # Star rating on the engine's scale
    dan_tier: str              # Canonical Jinjin Dan tier
    match_count: int           # Number of tested runs in window
    broke_down: bool           # Whether any run in the window lost more than the skill's tolerance
    #: How the level was read: "fit" across plays, "median" of too few plays, or "none".
    method: str = "none"

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
            "broke_down": self.broke_down,
            "method": self.method,
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
    failed_matches: int                        # Failed runs, kept for the demand they broke under
    average_ur: Optional[float]                # Stability baseline UR (from cleared runs only)
    overall_dan: str
    overall_star_rating: float
    dominant_technique: str
    bottleneck_technique: str
    dimensions: Dict[str, DimensionMacroMetric]
    trend_comparison: Optional[Dict[str, Any]] = None
    #: Snapshots in the window that the legacy strain engine wrote; their unit cannot be converted, so they
    #: are counted here and left out of everything above (ADR-0020 decision 4).
    legacy_excluded: int = 0
    #: Snapshots of mod plays read before mods were honoured; re-importing the replays brings them back.
    stale_excluded: int = 0

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
            "legacy_excluded": self.legacy_excluded,
            "stale_excluded": self.stale_excluded,
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
    - A skill's level is fitted across the window's plays (`level_across_plays`, ADR-0028); so is the overall,
      on each chart's total. Failed matches also contribute the demand they broke under, as a lower bound.
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
                broke_down=False,
            )
            for dim in TECH_KEYS
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

    legacy_excluded = sum(1 for s in snapshots if s.summary.get("engine") != SNAPSHOT_ENGINE)
    snapshots = [s for s in snapshots if s.summary.get("engine") == SNAPSHOT_ENGINE]
    stale_excluded = sum(1 for s in snapshots if not snapshot_is_current(s.summary))
    snapshots = [s for s in snapshots if snapshot_is_current(s.summary)]

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

    observed_dims = set(TECH_KEYS)
    for s in snapshots:
        observed_dims.update(s.capacities.keys())
        observed_dims.update(s.fatal_peak_strains.keys())
    all_dims = list(TECH_KEYS) + [d for d in sorted(observed_dims) if d not in TECH_KEYS]

    for dim in all_dims:
        per_play_caps: List[float] = []
        per_play_stars: List[float] = []
        fit_points: List[Tuple[float, float, float]] = []
        fatal_floor = 0.0
        tested_count = 0
        has_any_breakdown = False

        for s in snapshots:
            d_info = s.capacities.get(dim)
            if d_info and d_info.get("tested"):
                tested_count += 1
                if float(d_info.get("effective_capacity", 0.0)) > 0:
                    per_play_caps.append(float(d_info["effective_capacity"]))
                if float(d_info.get("star_rating", 0.0)) > 0:
                    per_play_stars.append(float(d_info["star_rating"]))
                if "events" in d_info and "lost" in d_info:
                    fit_points.append((float(d_info.get("chart_level", 0.0)), float(d_info["events"]), float(d_info["lost"])))
                if d_info.get("broke_down"):
                    has_any_breakdown = True

            # A failed run's fatal demand is a lower bound on the level (ADR-0012, ADR-0020 decision 3)
            if s.is_failed and dim in s.fatal_peak_strains:
                fatal_floor = max(fatal_floor, float(s.fatal_peak_strains[dim]))

        level = level_across_plays(fit_points, _EPS_OF_KEY.get(dim, DEFAULT.eps_rc))
        if level is not None:
            cap, sr, method = level, stars_of(level), "fit"
        elif per_play_stars or per_play_caps:
            cap = statistics.median(per_play_caps) if per_play_caps else 0.0
            sr = statistics.median(per_play_stars) if per_play_stars else stars_of(cap)
            method = "median"
        else:
            cap, sr, method = 0.0, 0.0, "none"
        if fatal_floor > 0 and stars_of(fatal_floor) > sr:
            cap, sr = fatal_floor, stars_of(fatal_floor)
            method = method if method != "none" else "fatal"

        dimensions[dim] = DimensionMacroMetric(
            dimension=dim,
            effective_capacity=cap,
            star_rating=sr,
            dan_tier=estimate_canonical_dan(sr) if sr > 0 else "0th Dan",
            match_count=tested_count,
            broke_down=has_any_breakdown,
            method=method,
        )

    # 4. Overall: the same fit on each chart's total, as the engine reads a chart's total (ADR-0028). Not a blend
    # of the skills: a chart's own skill stars blended that way read well above its total.
    tested_metrics = [m for m in dimensions.values() if m.star_rating > 0]
    if tested_metrics:
        dominant_tech = max(tested_metrics, key=lambda m: m.star_rating).dimension
        bottleneck_tech = min(tested_metrics, key=lambda m: m.star_rating).dimension
        overall_infos = [s.summary["overall"] for s in snapshots if isinstance(s.summary.get("overall"), dict)]
        overall_level = level_across_plays(
            [(float(o["chart_level"]), float(o["events"]), float(o["lost"])) for o in overall_infos],
            statistics.mean(float(o["eps"]) for o in overall_infos) if overall_infos else DEFAULT.eps_rc,
        )
        if overall_level is not None:
            overall_sr = stars_of(overall_level)
        elif overall_infos:
            overall_sr = statistics.median(float(o["star_rating"]) for o in overall_infos)
        else:  # snapshots without an overall reading: a chart's total reads about its strongest skill
            overall_sr = max(m.star_rating for m in tested_metrics)
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
        legacy_excluded=legacy_excluded,
        stale_excluded=stale_excluded,
    )
