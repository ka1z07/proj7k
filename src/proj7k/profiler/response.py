"""
Skill-level response of a player to a chart, on the spec-v0.2 engine's difficulty field (ADR-0020).

The engine defines a chart's difficulty in skill k as the level `theta` at which the weighted expected
loss reaches a tolerance. A replay says how much was actually lost, so the same equation read the
other way gives the level the player showed in that skill: `level_for_loss`. There is no curve to fit
and no threshold to detect; the player's level in a skill is the engine's own number for what they
lost, in the engine's own unit, on the engine's own star scale and dan table.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from proj7k.dan import estimate_canonical_dan
from proj7k.engine.params import DEFAULT, Params
from proj7k.engine.scale import stars_of
from proj7k.engine.skills import SKILL_TECH_KEY, SKILLS
from proj7k.engine.solver import loss
from proj7k.field import TECH_KEYS, ChartField, level_for_loss, trace_beatmap
from proj7k.parser import Beatmap7K, NoteType
from proj7k.profiler.aggregate import player_overall_star
from proj7k.profiler.matcher import AlignedHit, HitAlignmentResult, HitJudgment

#: What an osu!mania judgment costs in accuracy, as a share of a perfect hit (the score's 300/200/100/50/0 of 300).
JUDGMENT_LOSS: Dict[HitJudgment, float] = {
    HitJudgment.MAX: 0.0,
    HitJudgment.PERFECT: 0.0,
    HitJudgment.GREAT: 1.0 / 3.0,
    HitJudgment.GOOD: 2.0 / 3.0,
    HitJudgment.MEH: 5.0 / 6.0,
    HitJudgment.MISS: 1.0,
}

#: The engine name of a skill against the short key the profiler stores and reports (`field.TECH_KEYS`).
KEY_OF = SKILL_TECH_KEY


@dataclass(frozen=True)
class DemandBin:
    """Events of one skill whose demand reading falls in [d_min, d_max): what the engine predicted against what was lost."""
    bin_index: int
    d_min: float
    d_max: float
    events: float          # membership-weighted event count
    observed_loss: float   # mean accuracy loss of the bin's events
    predicted_loss: float  # mean p_i at the player's level for that skill

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bin_index": self.bin_index,
            "d_min": round(self.d_min, 2),
            "d_max": round(self.d_max, 2),
            "events": round(self.events, 1),
            "observed_loss": round(self.observed_loss, 4),
            "predicted_loss": round(self.predicted_loss, 4),
        }


@dataclass(frozen=True)
class DimensionCapacityResult:
    """The player's level in one skill, on the engine's scale."""
    dimension: str                  # the short key (`jack` ... `ln_release`)
    effective_capacity: float       # the level Theta_k, in the engine's unit (equivalent Hz)
    star_rating: float              # stars_of(Theta_k)
    dan_tier: str
    broke_down: bool                # lost more than the skill's tolerance: Theta_k is below the chart's D_k
    chart_level: float              # the chart's own D_k for this skill
    tested: bool                    # the chart has something to fail at in this skill, and some of it was played
    events: float                   # membership-weighted events played in this skill
    bins: List[DemandBin]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "effective_capacity": round(self.effective_capacity, 2),
            "star_rating": round(self.star_rating, 2),
            "dan_tier": self.dan_tier,
            "broke_down": self.broke_down,
            "chart_level": round(self.chart_level, 2),
            "tested": self.tested,
            "events": round(self.events, 1),
            "bins": [b.to_dict() for b in self.bins],
        }


@dataclass(frozen=True)
class SkillRadarReport:
    """The eight-skill radar of a play and its dan tier."""
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
    num_bins: int = 8
    #: A skill is tested only if this much membership weight was played; below it the level is the prior's, not the player's.
    min_events: float = 8.0


@dataclass(frozen=True)
class PlayOutcome:
    """What a play lost, per event of the field."""
    loss: np.ndarray       # [n] accuracy loss in [0, 1]
    played: np.ndarray     # [n] bool: the event was reached (an event after the fail is not a loss)


def play_outcome(field: ChartField, alignment: HitAlignmentResult, played_until_s: Optional[float] = None) -> PlayOutcome:
    """
    Lay a causal alignment on the field's events. A note's press takes its judgment's loss; the release of
    an LN takes its tail judgment's. A note nobody hit is lost whole, but an event after `played_until_s`
    (the replay's last input) is `played = False`: a play that failed out is not held to the song it did
    not reach.
    """
    n = field.events.n
    lost = np.zeros(n)
    played = np.zeros(n, dtype=bool)
    for hit in alignment.aligned_hits:
        i = field.press_index(hit.column, hit.target_time / 1000.0)
        if i is None:
            continue
        # the point-to-point reading: the demand this note came under, in each skill it belongs to
        hit.strains = {KEY_OF[name]: float(field.d[i] * field.w[i, k]) for k, name in enumerate(SKILLS)}
        lost[i] = JUDGMENT_LOSS[hit.judgment]
        played[i] = True
        r = field.release_index(i)
        if r is not None and hit.note_type == NoteType.LN:
            tail = hit.tail_judgment if hit.tail_judgment is not None else HitJudgment.MISS
            lost[r] = JUDGMENT_LOSS[tail]
            played[r] = True
    if played_until_s is not None:
        played &= field.t <= played_until_s
    return PlayOutcome(loss=lost, played=played)


def _untested(dim: str, chart_level: float, events: float) -> DimensionCapacityResult:
    return DimensionCapacityResult(
        dimension=dim, effective_capacity=0.0, star_rating=0.0, dan_tier="0th",
        broke_down=False, chart_level=chart_level, tested=False, events=events, bins=[],
    )


def _bins(d: np.ndarray, w: np.ndarray, lost: np.ndarray, p_at_level: np.ndarray, n_bins: int) -> List[DemandBin]:
    live = w > 0
    if not live.any():
        return []
    edges = np.linspace(0.0, float(d[live].max()), n_bins + 1)
    which = np.clip(np.searchsorted(edges, d, side="right") - 1, 0, n_bins - 1)
    out: List[DemandBin] = []
    for b in range(n_bins):
        sel = live & (which == b)
        wb = w[sel].sum()
        if wb <= 0:
            continue
        out.append(DemandBin(
            bin_index=b, d_min=float(edges[b]), d_max=float(edges[b + 1]), events=float(wb),
            observed_loss=float(np.dot(w[sel], lost[sel]) / wb), predicted_loss=float(np.dot(w[sel], p_at_level[sel]) / wb),
        ))
    return out


def analyze_strain_response(
    alignment: HitAlignmentResult,
    beatmap: Beatmap7K,
    field: Optional[ChartField] = None,
    options: Optional[StrainResponseOptions] = None,
    params: Params = DEFAULT,
    played_until_s: Optional[float] = None,
) -> SkillRadarReport:
    """
    The player's level in each of the eight skills, read off the field of `beatmap` (in physical time,
    the same time the alignment's target times are in) by inverting the engine's loss equation.
    `played_until_s` is where the play ended, if it ended early.
    """
    opts = options or StrainResponseOptions()
    if field is None:
        field = trace_beatmap(beatmap, params)
    outcome = play_outcome(field, alignment, played_until_s)

    results: Dict[str, DimensionCapacityResult] = {}
    for k, name in enumerate(SKILLS):
        key = KEY_OF[name]
        D_k = field.profile.skills[name].D
        w = field.w[:, k] * outcome.played
        events = float(w.sum())
        if D_k <= 0.0 or events < opts.min_events:
            results[key] = _untested(key, D_k, events)
            continue
        eps = params.eps_rc if name.startswith("rc") else params.eps_ln
        observed = float(np.dot(w, outcome.loss))
        level = level_for_loss(field.d, w, observed, eps, params)
        stars = stars_of(level)
        results[key] = DimensionCapacityResult(
            dimension=key,
            effective_capacity=level,
            star_rating=stars,
            dan_tier=estimate_canonical_dan(stars),
            broke_down=observed > eps * events,
            chart_level=D_k,
            tested=True,
            events=events,
            bins=_bins(field.d, w, outcome.loss, loss(field.d, level, params), opts.num_bins),
        )

    # keep the radar in the profiler's key order
    results = {k: results[k] for k in TECH_KEYS}
    tested = [r for r in results.values() if r.tested]
    if tested:
        dominant = max(tested, key=lambda r: r.star_rating).dimension
        bottleneck = min(tested, key=lambda r: r.star_rating).dimension
        overall_dan = estimate_canonical_dan(player_overall_star({r.dimension: r.star_rating for r in tested}))
    else:
        dominant = bottleneck = "None"
        overall_dan = "0th"
    return SkillRadarReport(
        dimensions=results, overall_dan=overall_dan, dominant_technique=dominant, bottleneck_technique=bottleneck,
    )
