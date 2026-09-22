"""
Fit each technique's absolute driver-to-star anchor on its own 15-tier ladder.

Issue #50's reverse search. `tools/calibration_sandbox.py` evaluates a calibration *forwards*
("what would these constants do to the ladder"); this tool searches *backwards* for the
constants: for each of the eight axes, which `a` and `exp` put `a * driver ** exp` inside
`CANONICAL_DAN_SR[tier]`'s ±8% band on as many of the axis' own 15 benchmark charts as possible.

Both tools start from the same frozen core (`proj7k.benchmark_core`) and evaluate through the
engine's own mapping (`radar.technique_star_scores`), so a fitted number means what it says. The
acceptance bands and the ladder gates are read from `dan.CANONICAL_DAN_SR` and
`guard.CALIBRATED_METRIC_GATES` — never re-typed here.

    PYTHONPATH=src python3 tools/technique_star_fit.py            # fit and report
    PYTHONPATH=src python3 tools/technique_star_fit.py --emit     # the table, for calibration.py
    PYTHONPATH=src python3 tools/technique_star_fit.py --verify   # engine self-check only

The search is exhaustive rather than local: for a fixed exponent, the in-band count is a step
function of the gain whose breakpoints are where a score crosses a band edge, so the optimum sits
at one of those breakpoints — the tool scores them (and the midpoints between them) on a grid of
exponents and keeps the best. There is no gradient to follow and no seed to choose.
"""

import argparse
from dataclasses import dataclass
import json
import statistics
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from proj7k.benchmark_core import build_core, ladder_records
from proj7k.calibration import DEFAULT_CALIBRATION, TechniqueStarAnchor
from proj7k.dan import CANONICAL_DAN_SR, CANONICAL_DAN_SR_BANDS
from proj7k.guard import (
    CALIBRATED_METRIC_GATES,
    LADDER_MAX_TOTAL_INVERSIONS,
    LADDER_MIN_MEAN_KENDALL_TAU,
    LADDER_MIN_MEAN_SPEARMAN_RHO,
    TECHNIQUE_BAND,
    TECHNIQUE_BAND_MIN_IN_BAND,
)
from proj7k.monotonicity import DRIVER_METRIC_PREFIX, evaluate_tier_sequence
from proj7k.radar import TECHNIQUE_NAMES, RadarOptions, technique_radar_from_drivers
from proj7k.rating import RatingOptions, synthesize_star_rating

#: The benchmark manifest group each axis' ladder is filed under.
LADDER_FOR_AXIS: Dict[str, str] = {
    "jack": "Regular Jack",
    "tech": "Regular Tech",
    "speed": "Regular Speed",
    "stream": "Regular Stream",
    "ln_general": "LN General",
    "ln_tech": "LN Tech",
    "ln_inverse": "LN Inverse",
    "ln_release": "LN Release",
}

#: The band the calibration is judged by, and its per-axis bar — read from `guard`, never
#: retyped here, so the tool and the test that asserts the same numbers cannot disagree.
BAND = TECHNIQUE_BAND
BAND_MIN_IN_BAND = TECHNIQUE_BAND_MIN_IN_BAND

#: Exponents searched. The exponent sets the ladder's *shape* — it is what turns a ratio between
#: two charts' drivers into a ratio between their stars — so the grid is geometric, which is the
#: spacing that distinguishes shapes rather than values.
EXP_GRID: Tuple[float, ...] = tuple(0.02 * (1.03 ** k) for k in range(0, 190))

#: Reported precision of a fitted anchor: the six decimals the repository's other calibration
#: constants are pinned at. Candidates are rounded *before* they are scored, so a reported count
#: is the count the shipped constant produces.
ANCHOR_PRECISION = 6


@dataclass(frozen=True)
class AxisFit:
    """One axis' fitted anchor, what it achieves, and the tiers it misses."""

    technique: str
    anchor: TechniqueStarAnchor
    in_band: int
    worst_relative_error: float
    #: (tier, score, canonical) for every tier outside the band, worst-first.
    out_of_band: List[Tuple[str, float, float]]

    @property
    def clears_band_bar(self) -> bool:
        return self.in_band >= BAND_MIN_IN_BAND


@dataclass(frozen=True)
class TierRow:
    """One chart of an axis' ladder: where it sits and what the fitted anchor reads there."""

    tier: str
    song: str
    driver: float
    canonical: float
    score: float

    @property
    def relative_error(self) -> float:
        return _relative_error(self.score, self.canonical)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "song": self.song,
            "driver": self.driver,
            "canonical": self.canonical,
            "score": round(self.score, 6),
            "relative_error": round(self.relative_error, 6),
        }


def _relative_error(score: float, target: float) -> float:
    return abs(score - target) / target if target else 0.0


def _candidate_gains(
    drivers: Sequence[float], targets: Sequence[float], exp: float
) -> List[float]:
    """
    Every gain worth scoring for one exponent: the band-edge crossings, and the gaps between.

    A chart enters or leaves the band where `a * d ** exp` crosses `c * (1 ± BAND)`, so the
    in-band count is constant between consecutive crossings and its maximum is attained at one
    of them or strictly inside a gap; scoring both finds it.
    """
    edges: List[float] = []
    for driver, target in zip(drivers, targets):
        powered = driver ** exp
        if powered <= 0.0:
            continue
        edges.append(target * (1.0 - BAND) / powered)
        edges.append(target * (1.0 + BAND) / powered)
    if not edges:
        return []
    edges = sorted(set(edges))
    candidates = [edges[0] * 0.5]
    candidates.extend((lower + upper) / 2.0 for lower, upper in zip(edges, edges[1:]))
    candidates.append(edges[-1] * 1.01)
    return candidates


def _rank(scores: Sequence[float], targets: Sequence[float], exp: float) -> Tuple[Tuple[float, ...], int, float]:
    """
    How good one candidate is: in-band count first, then how wrong the worst chart is, then the
    total squared error, then the flatter exponent. Deterministic, and it prefers a fit whose
    worst chart is least wrong over one that presses charts against a band edge.
    """
    errors = [_relative_error(s, c) for s, c in zip(scores, targets)]
    in_band = sum(1 for e in errors if e <= BAND)
    worst = max(errors)
    squared = sum(e * e for e in errors)
    return (in_band, -round(worst, 6), -round(squared, 6), -exp), in_band, worst


def fit_axis(rows: Sequence[Dict[str, Any]], technique: str) -> Tuple[AxisFit, List[TierRow]]:
    """The anchor maximising the in-band count on one axis' own ladder."""
    drivers = [r["drivers"][technique] for r in rows]
    targets = [CANONICAL_DAN_SR[r["tier"]] for r in rows]

    best: Optional[Tuple[Tuple[float, ...], TechniqueStarAnchor]] = None
    for exp in EXP_GRID:
        exp_r = round(exp, ANCHOR_PRECISION)
        for gain in _candidate_gains(drivers, targets, exp_r):
            anchor = TechniqueStarAnchor(
                technique=technique, a=round(gain, ANCHOR_PRECISION), exp=exp_r
            )
            if anchor.a <= 0.0:
                continue
            scores = [anchor.score(d) for d in drivers]
            rank, _, _ = _rank(scores, targets, exp_r)
            if best is None or rank > best[0]:
                best = (rank, anchor)
    assert best is not None, f"no candidate anchor for {technique}"
    anchor = best[1]

    scores = [anchor.score(d) for d in drivers]
    _, in_band, worst = _rank(scores, targets, anchor.exp)
    tier_rows = [
        TierRow(
            tier=r["tier"],
            song=r["song"],
            driver=r["drivers"][technique],
            canonical=CANONICAL_DAN_SR[r["tier"]],
            score=scores[i],
        )
        for i, r in enumerate(rows)
    ]
    out = sorted(
        ((row.tier, row.score, row.canonical) for row in tier_rows if row.relative_error > BAND),
        key=lambda item: -_relative_error(item[1], item[2]),
    )
    return (
        AxisFit(
            technique=technique,
            anchor=anchor,
            in_band=in_band,
            worst_relative_error=worst,
            out_of_band=out,
        ),
        tier_rows,
    )


@dataclass(frozen=True)
class Candidate:
    """One axis' candidate anchor, with the count it achieves on that axis' own ladder."""

    in_band: int
    anchor: TechniqueStarAnchor


#: How many charts worse than an axis' own best a candidate may be and still be eligible for the
#: table. The per-axis objective has a whole ridge of optima (the bands are wide enough that
#: many gains land the same count), and which point on the ridge is taken decides whether the
#: *composed* rating keeps its own acceptance — so the ridge, not just its peak, is the search
#: space.
SELECTION_SLACK = 3

#: Candidates kept per axis, best first.
SELECTION_CANDIDATES = 260

#: The anchor-margin floor the selection will not trade away: the tightest anchor band must sit
#: at least this fraction of its own width inside its edges. A table that passes the bands by
#: 0.05★ is one corpus edit away from a red CI run, which is not a calibration anybody can live
#: with.
MIN_ANCHOR_MARGIN = 0.05


def axis_candidates(
    rows: Sequence[Dict[str, Any]],
    technique: str,
    slack: int = SELECTION_SLACK,
    limit: int = SELECTION_CANDIDATES,
) -> List[Candidate]:
    """Every anchor within `slack` charts of this axis' own best, best first."""
    drivers = [r["drivers"][technique] for r in rows]
    targets = [CANONICAL_DAN_SR[r["tier"]] for r in rows]
    scored: Dict[Tuple[float, float], Candidate] = {}
    for exp in EXP_GRID:
        exp_r = round(exp, ANCHOR_PRECISION)
        for gain in _candidate_gains(drivers, targets, exp_r):
            a = round(gain, ANCHOR_PRECISION)
            if a <= 0.0:
                continue
            scores = [a * d ** exp_r for d in drivers]
            in_band = sum(1 for s, c in zip(scores, targets) if _relative_error(s, c) <= BAND)
            key = (round(exp_r, 3), round(a, 4))
            if key not in scored or in_band > scored[key].in_band:
                scored[key] = Candidate(in_band, TechniqueStarAnchor(technique, a, exp_r))
    best = max(c.in_band for c in scored.values())
    keep = [c for c in scored.values() if c.in_band >= best - slack]
    keep.sort(key=lambda c: (-c.in_band, c.anchor.exp))
    return keep[:limit]


def _select(
    records: Sequence[Dict[str, Any]], candidates: Dict[str, List[Candidate]]
) -> Tuple[Dict[str, TechniqueStarAnchor], Tuple[Any, ...]]:
    """
    The table to ship: coordinate descent over the per-axis candidate ridges.

    The acceptance is lexicographic, and the two CI gates come first because they are not
    negotiable — a table that lands more charts in band but leaves the Stellium anchor below its
    floor is not a table that can be committed. After them the selector maximises the *composed*
    rating's band landing (the ticket's hard criterion) and then the per-axis landing.

    What this stage is not: a licence to move an axis away from its own ladder. Every candidate
    it may pick is within `SELECTION_SLACK` charts of the best that axis' own ladder admits, so
    the calibration's per-axis claim still holds — the choice is only among fits the ladder
    itself cannot tell apart.
    """
    chart_index = {(r["technique"], r["tier"]): i for i, r in enumerate(records)}
    columns = {
        t: [candidates[t][0].anchor.score(records[i]["drivers"][t]) for i in range(len(records))]
        for t in TECHNIQUE_NAMES
    }
    table = {t: candidates[t][0].anchor for t in TECHNIQUE_NAMES}

    def column(anchor: TechniqueStarAnchor) -> List[float]:
        return [anchor.score(r["drivers"][anchor.technique]) for r in records]

    def acceptance(
        table: Dict[str, TechniqueStarAnchor], columns: Dict[str, List[float]]
    ) -> Tuple[Any, ...]:
        from proj7k.rating import apply_tanh_soft_cap

        totals = [
            apply_tanh_soft_cap(max(columns[t][i] for t in TECHNIQUE_NAMES))
            for i in range(len(records))
        ]
        stars = {
            (r["technique"], r["tier"]): totals[i] for i, r in enumerate(records)
        }
        gate = CALIBRATED_METRIC_GATES["star_rating"]
        ladders_ok = 0
        composite_in_band = 0
        ladders: List[Dict[str, Any]] = []
        for technique in TECHNIQUE_NAMES:
            group = LADDER_FOR_AXIS[technique]
            rows = ladder_records(list(records), group)
            tiers = [r["tier"] for r in rows]
            values = [stars[(group, tier)] for tier in tiers]
            mono = evaluate_tier_sequence(
                list(zip(tiers, values)), technique=group, metric="star_rating"
            )
            if (
                mono.kendall_tau >= gate.min_kendall_tau
                and mono.spearman_rho >= gate.min_spearman_rho
                and len(mono.violations) <= gate.max_violations
            ):
                ladders_ok += 1
            composite_in_band += sum(
                1 for tier, value in zip(tiers, values)
                if _relative_error(value, CANONICAL_DAN_SR[tier]) <= BAND
            )
            ladders.append(
                {
                    "kendall_tau": mono.kendall_tau,
                    "spearman_rho": mono.spearman_rho,
                    "inversions": len(mono.violations),
                }
            )
        mean_tau = sum(row["kendall_tau"] for row in ladders) / len(ladders)
        mean_rho = sum(row["spearman_rho"] for row in ladders) / len(ladders)
        total_inversions = sum(row["inversions"] for row in ladders)
        aggregate_ok = (
            mean_rho >= LADDER_MIN_MEAN_SPEARMAN_RHO
            and mean_tau >= LADDER_MIN_MEAN_KENDALL_TAU
            and total_inversions <= LADDER_MAX_TOTAL_INVERSIONS
        )
        anchor_ok = 0
        margins = []
        for tier, (low, high) in CANONICAL_DAN_SR_BANDS.items():
            median = statistics.median([v for (_, t), v in stars.items() if t == tier])
            if low <= median <= high:
                anchor_ok += 1
            margins.append(min(median - low, high - median) / (high - low))
        dim_in_band = 0
        for technique in TECHNIQUE_NAMES:
            group = LADDER_FOR_AXIS[technique]
            for row in ladder_records(list(records), group):
                index = chart_index[(group, row["tier"])]
                if (
                    _relative_error(columns[technique][index], CANONICAL_DAN_SR[row["tier"]])
                    <= BAND
                ):
                    dim_in_band += 1
        # The axes have to stay comparable: a chart on technique k's ladder has to read highest
        # on axis k, or "the eight dimensions are on one scale" is not true of the engine's own
        # vectors. This is the property the alignment diagnosis modules assert chart by chart.
        own_axis_leads = 0
        for technique in TECHNIQUE_NAMES:
            group = LADDER_FOR_AXIS[technique]
            for row in ladder_records(list(records), group):
                index = chart_index[(group, row["tier"])]
                if all(
                    columns[technique][index] >= columns[other][index]
                    for other in TECHNIQUE_NAMES
                ):
                    own_axis_leads += 1
        return (
            anchor_ok,
            ladders_ok,
            min(margins) >= MIN_ANCHOR_MARGIN,
            aggregate_ok,
            own_axis_leads,
            composite_in_band,
            dim_in_band,
        )

    current = acceptance(table, columns)
    for _ in range(8):
        improved = False
        for technique in TECHNIQUE_NAMES:
            best_local = (current, table[technique], columns[technique])
            for candidate in candidates[technique]:
                trial_column = column(candidate.anchor)
                trial_columns = dict(columns)
                trial_columns[technique] = trial_column
                trial_table = dict(table)
                trial_table[technique] = candidate.anchor
                score = acceptance(trial_table, trial_columns)
                if score > best_local[0]:
                    best_local = (score, candidate.anchor, trial_column)
            if best_local[1] is not table[technique]:
                table[technique] = best_local[1]
                columns[technique] = best_local[2]
                current = best_local[0]
                improved = True
        if not improved:
            break
    return table, current


def free_offset_ceiling(
    rows: Sequence[Dict[str, Any]], technique: str
) -> Tuple[int, float, float, float]:
    """
    The count a **free-offset** anchor law could land, for the record: `a * r ** exp + b`.

    The ticket names the offset as part of the family, and the shipped law pins it to zero (a
    zero driver is that axis' zero). This is what the pin costs: for a fixed exponent the count
    is constant between band-edge crossings, so its maximum sits at a vertex of those crossings —
    the tool evaluates every pair of them and reports the best.

    Returns (in-band, a, exp, b).
    """
    drivers = [r["drivers"][technique] for r in rows]
    targets = [CANONICAL_DAN_SR[r["tier"]] for r in rows]
    best: Tuple[int, float, float, float] = (0, 0.0, 0.0, 0.0)
    for exp in EXP_GRID:
        exp_r = round(exp, ANCHOR_PRECISION)
        edges = []
        for driver, target in zip(drivers, targets):
            x = driver ** exp_r
            if x <= 0.0:
                continue
            edges.append((x, target * (1.0 - BAND)))
            edges.append((x, target * (1.0 + BAND)))
        for i in range(len(edges)):
            for j in range(i + 1, len(edges)):
                (x1, y1), (x2, y2) = edges[i], edges[j]
                if abs(x1 - x2) < 1e-15:
                    continue
                a = (y1 - y2) / (x1 - x2)
                if a <= 0.0:
                    continue
                b = y1 - a * x1
                count = sum(
                    1
                    for driver, target in zip(drivers, targets)
                    if _relative_error(max(0.0, a * driver ** exp_r + b), target) <= BAND
                )
                if count > best[0]:
                    best = (count, round(a, ANCHOR_PRECISION), exp_r, round(b, ANCHOR_PRECISION))
    return best


def star_ratings(
    records: Sequence[Dict[str, Any]], anchors: Sequence[TechniqueStarAnchor]
) -> Dict[Tuple[str, str], float]:
    """The engine's own rating per chart for one anchor table, read through the engine's mapping."""
    options = RatingOptions(technique_anchors=tuple(anchors))
    calibration = options.calibration
    radar_options = RadarOptions()
    stars: Dict[Tuple[str, str], float] = {}
    for record in records:
        radar = technique_radar_from_drivers(
            record["drivers"], options=radar_options, calibration=calibration
        )
        synthesis = synthesize_star_rating(radar, p90_strain=record["p90"], options=options)
        stars[(record["technique"], record["tier"])] = synthesis.star_rating
    return stars


def ladder_report(
    records: Sequence[Dict[str, Any]], stars: Dict[Tuple[str, str], float]
) -> Dict[str, Dict[str, Any]]:
    """Per-ladder star-rating metrics: band landing, ordering against the guard's gate, anchors."""
    report: Dict[str, Dict[str, Any]] = {}
    gate = CALIBRATED_METRIC_GATES["star_rating"]
    for technique in TECHNIQUE_NAMES:
        group = LADDER_FOR_AXIS[technique]
        rows = ladder_records(list(records), group)
        tiers = [r["tier"] for r in rows]
        values = [stars[(group, tier)] for tier in tiers]
        mono = evaluate_tier_sequence(
            list(zip(tiers, values)), technique=group, metric="star_rating"
        )
        report[group] = {
            "in_band": sum(
                1
                for tier, value in zip(tiers, values)
                if _relative_error(value, CANONICAL_DAN_SR[tier]) <= BAND
            ),
            "kendall_tau": mono.kendall_tau,
            "spearman_rho": mono.spearman_rho,
            "inversions": len(mono.violations),
            "clears_gate": (
                mono.kendall_tau >= gate.min_kendall_tau
                and mono.spearman_rho >= gate.min_spearman_rho
                and len(mono.violations) <= gate.max_violations
            ),
        }
    for tier, (low, high) in CANONICAL_DAN_SR_BANDS.items():
        samples = [value for (_, t), value in stars.items() if t == tier]
        median = statistics.median(samples)
        report[f"anchor {tier}"] = {
            "median": round(median, 4),
            "band": [low, high],
            "inside": low <= median <= high,
        }
    return report


def driver_report(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Each axis' raw driver ladder, against the gate registered for it in `guard`."""
    report: Dict[str, Dict[str, Any]] = {}
    for technique in TECHNIQUE_NAMES:
        group = LADDER_FOR_AXIS[technique]
        rows = ladder_records(list(records), group)
        mono = evaluate_tier_sequence(
            [(r["tier"], r["drivers"][technique]) for r in rows],
            technique=group,
            metric=DRIVER_METRIC_PREFIX + technique,
        )
        gate = CALIBRATED_METRIC_GATES[DRIVER_METRIC_PREFIX + technique]
        report[technique] = {
            "kendall_tau": mono.kendall_tau,
            "spearman_rho": mono.spearman_rho,
            "inversions": len(mono.violations),
            "clears_gate": (
                mono.kendall_tau >= gate.min_kendall_tau
                and mono.spearman_rho >= gate.min_spearman_rho
                and len(mono.violations) <= gate.max_violations
            ),
        }
    return report


def emit_table(table: Dict[str, TechniqueStarAnchor]) -> str:
    """The selected anchors as the `_TECHNIQUE_ANCHORS` table of `calibration.py`."""
    lines = ["_TECHNIQUE_ANCHORS: Tuple[TechniqueStarAnchor, ...] = ("]
    for technique in TECHNIQUE_NAMES:
        anchor = table[technique]
        lines.append(
            f'    TechniqueStarAnchor(technique="{technique}", a={anchor.a}, exp={anchor.exp}),'
        )
    lines.append(")")
    return "\n".join(lines)


def engine_disagreement(records: Sequence[Dict[str, Any]]) -> float:
    """
    The largest gap between this tool's forward mapping and the engine's own rating.

    The fit is only meaningful if the mapping it optimises is the engine's, so this evaluates the
    *shipped* anchors through the tool's route and compares them with the engine's own rating as
    the frozen core captured it (`reference_star`, recorded by `benchmark_core` when it evaluated
    the chart end to end). A non-zero value means the tool and the engine are not describing the
    same engine, and the run is worthless.
    """
    stars = star_ratings(records, DEFAULT_CALIBRATION.technique_anchors)
    return max(
        abs(stars[(r["technique"], r["tier"])] - r["reference_star"]) for r in records
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/technique_star_fit.py",
        description="Fit the eight absolute technique-star anchors on the frozen benchmark ladder.",
    )
    parser.add_argument("--rebuild", action="store_true", help="Ignore the frozen core and rebuild it")
    parser.add_argument("--json", action="store_true", help="Emit the measurements as JSON")
    parser.add_argument("--emit", action="store_true", help="Print the anchor table for calibration.py")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Only run the self-check of the tool's mapping against the engine's own rating",
    )
    parser.add_argument(
        "--free-offset",
        action="store_true",
        help="Also report what a free-offset anchor law (a * r ** exp + b) could land per axis, "
        "i.e. what pinning the offset to zero costs",
    )
    args = parser.parse_args(argv)

    records = build_core(force=args.rebuild)

    disagreement = engine_disagreement(records)
    if args.verify:
        print(f"shipped anchors: max |ΔSR| against the engine's own rating {disagreement:.10f}")
        return 0 if disagreement <= 1e-9 else 1
    if args.json and disagreement > 1e-9:
        sys.stderr.write(
            f"this tool's mapping diverges from the engine by {disagreement}★ — refusing to "
            f"report a fit against a different engine\n"
        )
        return 1

    fits: Dict[str, AxisFit] = {}
    rows_by_axis: Dict[str, List[TierRow]] = {}
    candidates: Dict[str, List[Candidate]] = {}
    for technique in TECHNIQUE_NAMES:
        rows = ladder_records(records, LADDER_FOR_AXIS[technique])
        fit, tier_rows = fit_axis(rows, technique)
        fits[technique] = fit
        rows_by_axis[technique] = tier_rows
        candidates[technique] = axis_candidates(rows, technique)

    table, acceptance = _select(records, candidates)
    anchors = [table[t] for t in TECHNIQUE_NAMES]
    stars = star_ratings(records, anchors)
    ladders = ladder_report(records, stars)

    per_axis = {
        t: sum(
            1
            for row in rows_by_axis[t]
            if _relative_error(table[t].score(row.driver), row.canonical) <= BAND
        )
        for t in TECHNIQUE_NAMES
    }
    if args.json:
        print(
            json.dumps(
                {
                    "self_check": disagreement,
                    "acceptance": {
                        "anchors_inside": acceptance[0],
                        "ladders_clearing": acceptance[1],
                        "anchor_margin_floor_met": acceptance[2],
                        "aggregate_bar_met": acceptance[3],
                        "own_axis_leads": acceptance[4],
                        "composite_in_band": acceptance[5],
                        "dimension_in_band": acceptance[6],
                    },
                    "anchors": {
                        t: {
                            "a": table[t].a,
                            "exp": table[t].exp,
                            "per_axis_best": {
                                "a": fits[t].anchor.a,
                                "exp": fits[t].anchor.exp,
                                "in_band": fits[t].in_band,
                            },
                            "in_band": per_axis[t],
                            "out_of_band": fits[t].out_of_band,
                            "ladder": [row.to_dict() for row in rows_by_axis[t]],
                        }
                        for t in TECHNIQUE_NAMES
                    },
                    "ladders": ladders,
                    "drivers": driver_report(records),
                },
                indent=2,
            )
        )
        return 0

    print(f"frozen core: {len(records)} charts  |  self-check max |ΔSR| {disagreement:.10f}")
    print()
    print(
        f"=== per-axis best fit (band ±{BAND:.0%} of CANONICAL_DAN_SR[T]; bar ≥{BAND_MIN_IN_BAND}/15) ==="
    )
    print(f"{'axis':12} {'a':>13} {'exp':>9}  {'in band':>8} {'worst':>7}  out of band")
    for technique in TECHNIQUE_NAMES:
        fit = fits[technique]
        out = ", ".join(f"{tier} {score:.2f}≠{canon:.2f}" for tier, score, canon in fit.out_of_band)
        print(
            f"{technique:12} {fit.anchor.a:13.6f} {fit.anchor.exp:9.6f}  "
            f"{fit.in_band:5d}/15 {fit.worst_relative_error:7.1%}  {out}"
        )
    met = [t for t in TECHNIQUE_NAMES if fits[t].clears_band_bar]
    print(f"axes whose own best meets the bar: {len(met)}/8 — {', '.join(met) or '-'}")

    if args.free_offset:
        print()
        print("=== what the zero floor costs (a * r ** exp + b, offset free) ===")
        print(f"{'axis':12} {'shipped (b=0)':>13} {'free offset':>12} {'a':>11} {'exp':>8} {'b':>9}")
        for technique in TECHNIQUE_NAMES:
            rows = ladder_records(records, LADDER_FOR_AXIS[technique])
            count, a, exp, b = free_offset_ceiling(rows, technique)
            print(
                f"{technique:12} {fits[technique].in_band:9d}/15 {count:8d}/15 "
                f"{a:11.6f} {exp:8.6f} {b:9.4f}"
            )

    print()
    print("=== the selected table (within ±%d charts of each axis' best)" % SELECTION_SLACK + " ===")
    print(f"{'axis':12} {'a':>13} {'exp':>9}  {'in band':>8}   per-axis best")
    for technique in TECHNIQUE_NAMES:
        print(
            f"{technique:12} {table[technique].a:13.6f} {table[technique].exp:9.6f}  "
            f"{per_axis[technique]:5d}/15   {fits[technique].in_band:2d}/15"
        )
    print(
        f"anchors inside {acceptance[0]}/4   ladders clearing {acceptance[1]}/8   "
        f"margin floor {'met' if acceptance[2] else 'MISSED'}   "
        f"ladder-level bar {'met' if acceptance[3] else 'MISSED'}\n"
        f"own axis leads {acceptance[4]}/120   composite in band {acceptance[5]}/120   "
        f"dimensions in band {acceptance[6]}/120"
    )

    print()
    print("=== star ladders (the engine's rating, composed from those anchors) ===")
    print(f"{'ladder':15} {'in band':>8} {'tau':>7} {'rho':>7} {'inv':>4}   gate")
    for technique in TECHNIQUE_NAMES:
        group = LADDER_FOR_AXIS[technique]
        row = ladders[group]
        print(
            f"{group:15} {row['in_band']:5d}/15 {row['kendall_tau']:7.4f} {row['spearman_rho']:7.4f} "
            f"{row['inversions']:4d}   {'PASS' if row['clears_gate'] else 'FAIL'}"
        )
    for tier in CANONICAL_DAN_SR_BANDS:
        row = ladders[f"anchor {tier}"]
        print(
            f"anchor {tier:9} median {row['median']:6.3f} band {row['band']} "
            f"{'OK' if row['inside'] else 'FAIL'}"
        )

    print()
    print("=== the driver ladders the anchors are fitted on ===")
    drivers = driver_report(records)
    for technique in TECHNIQUE_NAMES:
        row = drivers[technique]
        print(
            f"{technique:12} tau {row['kendall_tau']:7.4f} rho {row['spearman_rho']:7.4f} "
            f"inv {row['inversions']:2d}   {'PASS' if row['clears_gate'] else 'BELOW GATE'}"
        )

    if args.emit:
        print()
        print(emit_table(table))

    return 0


if __name__ == "__main__":
    sys.exit(main())
