"""
Sweep the star-rating calibration over the frozen benchmark ladder.

The engine's star rating is a composition of two very different things: everything upstream
of the star scale (feature extraction, the dual-hand strain accumulator, the 8 raw technique
drivers) and the pure arithmetic that carries those numbers onto the star scale (the eight
technique anchor laws and the tanh soft cap, plus the strain anchor law behind the reported
raw rating). Only the second half reads the calibration constants — so the first half can be
computed once over the frozen corpus and frozen, after which a calibration point costs
arithmetic alone.

The technique anchors are a *table* — one gain and one exponent per axis — so they are not
one-at-a-time swept here; `tools/technique_star_fit.py` is what searches them. This tool
evaluates whatever table the options carry.

That is what makes a sweep cheap: the 120 charts are evaluated in full once, and every
constant after that is answered from the frozen core. A full one-at-a-time sweep of every
`RatingOptions` constant (`RatingOptions.methodology_fingerprint`'s inputs, plus the dan
anchors it folds in) runs in seconds instead of tens of minutes.

The tool never writes to the engine. It answers "what would this calibration do to the
ladder" — whether the 15-tier monotonicity, the anchor-tier medians and the CI gate would
survive — before anyone commits a constant change and re-baselines the star-rating checksum.

The frozen core itself lives in `proj7k.benchmark_core`, shared with
`tools/technique_star_fit.py`: the sandbox evaluates a calibration forwards, the fitter searches
for one backwards, and both start from the same freeze.

The fast path is asserted against the real `evaluate_intrinsic_difficulty` seam on every run:
if the two ever disagree by even 1e-9 of a star, the run aborts rather than reporting numbers
that describe a different engine. See `docs/methodology/calibration-sensitivity-and-sandbox.md`
for what the sweep found on the current calibration.

    PYTHONPATH=src python3 tools/calibration_sandbox.py                  # baseline only
    PYTHONPATH=src python3 tools/calibration_sandbox.py --sweep          # one-at-a-time sweep
    PYTHONPATH=src python3 tools/calibration_sandbox.py --set strain_a=0.242 --set soft_cap_scale=2
"""

import argparse
from dataclasses import fields, replace
import statistics
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from proj7k.benchmark_core import build_core
from proj7k.dan import CANONICAL_DAN_SR_BANDS
from proj7k.guard import (
    CALIBRATED_METRIC_GATES,
    DEFAULT_METRIC_GATE,
    DEFAULT_MIN_ANCHOR_SAMPLES,
)
from proj7k.monotonicity import (
    DRIVER_METRIC_PREFIX,
    TIER_ORDER,
    evaluate_tier_sequence,
)
from proj7k.radar import (
    RadarOptions,
    TechniqueRadar,
    technique_radar_from_drivers,
)
from proj7k.rating import RatingOptions, synthesize_star_rating

#: The sweep's own gate thresholds come from the CI guard rather than being re-typed here, so
#: a recalibrated gate moves both at once.
_GATE = CALIBRATED_METRIC_GATES["star_rating"]


def _radar_from_drivers(record: dict, options: RatingOptions) -> TechniqueRadar:
    """
    The star mapping of `radar.technique_radar_from_drivers`, over a frozen driver vector.

    The mapping itself is the engine's — this used to be a second copy of it, which meant a
    calibration sweep could describe an engine that no longer existed. Only the *inputs* are the
    sandbox's: a frozen driver vector and the calibration of the options point being evaluated.
    """
    return technique_radar_from_drivers(
        record["drivers"],
        options=RadarOptions(),
        calibration=options.calibration,
    )


def ratings(records: Sequence[dict], options: RatingOptions) -> Dict[Tuple[str, str], float]:
    """(technique, tier) -> star rating for one calibration point."""
    out: Dict[Tuple[str, str], float] = {}
    for record in records:
        synthesis = synthesize_star_rating(
            _radar_from_drivers(record, options), p90_strain=record["p90"], options=options
        )
        out[(record["technique"], record["tier"])] = synthesis.star_rating
    return out


def measure(
    records: Sequence[dict],
    options: RatingOptions,
    columns: Sequence[str] = ("star_rating",),
) -> dict:
    """
    Ladder metrics for one calibration point: ordering, anchor medians, and the CI verdict.

    `columns` names what each ladder is measured *on*. `"star_rating"` is the default and the
    one the anchor medians are taken from; any other name is read off the frozen core's raw
    driver vector (`driver_jack`, `driver_ln_release`, ...) or off a metric the core carries,
    which is the point of the generalization: the drivers are what the per-technique gates are
    stated on, and a driver ladder costs arithmetic here because the drivers are already frozen.
    Every column's thresholds come from `guard.CALIBRATED_METRIC_GATES` — never from a number
    retyped in this file — so the sandbox and the guard cannot disagree about what a ladder has
    to clear, and a metric with no registered gate reports the default one it fell back to.
    """
    stars = ratings(records, options)

    ladders: Dict[str, Dict[str, Any]] = {}
    for column in columns:
        per_technique: Dict[str, Any] = {}
        for technique in sorted({r["technique"] for r in records}):
            ordered = sorted(
                (r for r in records if r["technique"] == technique),
                key=lambda r: TIER_ORDER.index(r["tier"]),
            )
            if column == "star_rating":
                values = [stars[(r["technique"], r["tier"])] for r in ordered]
            else:
                name = column[len(DRIVER_METRIC_PREFIX):] if column.startswith(DRIVER_METRIC_PREFIX) else column
                values = [
                    r["drivers"][name]
                    if name in r["drivers"]
                    else float(r.get("features", {}).get(name, 0.0))
                    for r in ordered
                ]
            report = evaluate_tier_sequence(
                [(r["tier"], v) for r, v in zip(ordered, values)], technique=technique, metric=column
            )
            gate = CALIBRATED_METRIC_GATES.get(column, DEFAULT_METRIC_GATE)
            # An axis that reads zero on every tier of a ladder is inert for that technique
            # rather than badly ordered — the same rule the guard applies before it gates a
            # metric — so it is reported as inactive instead of as a failure.
            active = any(v != 0.0 for v in values)
            per_technique[technique] = {
                "active": active,
                "kendall_tau": report.kendall_tau,
                "spearman_rho": report.spearman_rho,
                "inversions": len(report.violations),
                "gate": {
                    "min_kendall_tau": gate.min_kendall_tau,
                    "min_spearman_rho": gate.min_spearman_rho,
                    "max_violations": gate.max_violations,
                },
                "passed": (
                    not active
                    or (
                        report.kendall_tau >= gate.min_kendall_tau
                        and report.spearman_rho >= gate.min_spearman_rho
                        and len(report.violations) <= gate.max_violations
                    )
                ),
            }
        passing = [t for t, m in per_technique.items() if m["passed"] and m["active"]]
        active_techniques = [t for t, m in per_technique.items() if m["active"]]
        ladders[column] = {
            "per_technique": per_technique,
            "passing": passing,
            "failing": sorted(t for t in active_techniques if t not in passing),
        }

    star_ladder = ladders["star_rating"]["per_technique"]
    tau_min = min(m["kendall_tau"] for m in star_ladder.values())
    rho_min = min(m["spearman_rho"] for m in star_ladder.values())
    inversions = sum(m["inversions"] for m in star_ladder.values())
    inv_max = max(m["inversions"] for m in star_ladder.values())
    worst_technique = min(star_ladder, key=lambda t: star_ladder[t]["kendall_tau"])

    medians: Dict[str, Tuple[Optional[float], int]] = {}
    for tier in CANONICAL_DAN_SR_BANDS:
        samples = [v for (_, tr), v in stars.items() if tr == tier]
        medians[tier] = (statistics.median(samples), len(samples)) if samples else (None, 0)

    anchor_ok = {
        tier: (low <= medians[tier][0] <= high) if medians[tier][1] >= DEFAULT_MIN_ANCHOR_SAMPLES else None
        for tier, (low, high) in CANONICAL_DAN_SR_BANDS.items()
    }
    passed = (
        tau_min >= _GATE.min_kendall_tau
        and rho_min >= _GATE.min_spearman_rho
        and inv_max <= _GATE.max_violations
        and all(v for v in anchor_ok.values() if v is not None)
    )

    return {
        "stars": stars,
        "tau_min": tau_min,
        "rho_min": rho_min,
        "inversions": inversions,
        "inv_max": inv_max,
        "worst_technique": worst_technique,
        "medians": medians,
        "anchor_ok": anchor_ok,
        "passed": passed,
        "ladders": ladders,
    }


def format_verdict(m: dict, baseline: Optional[dict] = None) -> str:
    anchors = " ".join(
        f"{tier} {m['medians'][tier][0]:.3f}{'!' if m['anchor_ok'][tier] is False else ' '}"
        for tier in CANONICAL_DAN_SR_BANDS
        if m["medians"][tier][0] is not None
    )
    line = (
        f"τmin {m['tau_min']:.3f} (worst: {m['worst_technique']})  "
        f"ρmin {m['rho_min']:.3f}  inversions ≤{m['inv_max']} ({m['inversions']} total)  "
        f"| {anchors}| {'PASS' if m['passed'] else 'FAIL'}"
    )
    if baseline is not None:
        deltas = [abs(m["stars"][k] - baseline["stars"][k]) for k in baseline["stars"]]
        line += (
            f"\n    star shift: mean {sum(deltas) / len(deltas):.4f}★  max {max(deltas):.4f}★  "
            f"{sum(1 for d in deltas if d > 1e-9)}/120 charts moved"
        )
    return line


def format_margins(m: dict) -> str:
    lines = []
    for tier, (low, high) in CANONICAL_DAN_SR_BANDS.items():
        median, samples = m["medians"][tier]
        if median is None:
            continue
        lines.append(
            f"  {tier:<10} {median:7.3f}  [{low}, {high}]   "
            f"up +{high - median:.3f} ({(high - median) / median * 100:5.1f}%)   "
            f"down +{median - low:.3f} ({(median - low) / median * 100:5.1f}%)   ({samples} charts)"
        )
    return "\n".join(lines)


def format_ladder(m: dict, column: str) -> str:
    """
    One column's ladder verdict, per technique, against its registered gate.

    This is the generalized view the drivers are read through: the row says what the technique
    scored on the column, what its gate is, and whether it cleared it — so "which of the 8
    drivers order their own ladder" is answerable from the sandbox without a bespoke tool.
    """
    ladder = m["ladders"][column]
    lines = [
        f"=== {column} ===",
        f"{'technique':14} {'tau':>7} {'rho':>7} {'inv':>4}   gate (tau/rho/inv)   verdict",
    ]
    for technique, stats in ladder["per_technique"].items():
        gate = stats["gate"]
        verdict = "n/a" if not stats["active"] else ("PASS" if stats["passed"] else "FAIL")
        lines.append(
            f"{technique:14} {stats['kendall_tau']:7.4f} {stats['spearman_rho']:7.4f} "
            f"{stats['inversions']:4d}   {gate['min_kendall_tau']:.2f}/{gate['min_spearman_rho']:.2f}/"
            f"{gate['max_violations']:<3d}      {verdict}"
        )
    lines.append(
        f"passing {len(ladder['passing'])}/{len(ladder['per_technique'])}: "
        f"{', '.join(ladder['passing']) or '-'}"
    )
    lines.append(f"failing: {', '.join(ladder['failing']) or '-'}")
    return "\n".join(lines)


def parse_assignments(pairs: Sequence[str]) -> dict:
    known = {f.name for f in fields(RatingOptions)}
    overrides = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects field=value, got '{pair}'")
        name, _, raw = pair.partition("=")
        name = name.strip()
        if name not in known:
            raise SystemExit(f"'{name}' is not a RatingOptions field. Known: {sorted(known)}")
        overrides[name] = float(raw)
    return overrides


SWEEP_CASES: List[Tuple[str, List[float]]] = [
    ("strain_a", [0.75, 0.9, 1.1, 1.25]),
    ("strain_b", [0.0, 0.5, 2.0, 4.0]),
    ("strain_exp", [0.85, 0.95, 1.05, 1.15]),
    ("soft_cap_threshold", [0.9, 0.97, 1.03, 1.1]),
    ("soft_cap_scale", [0.5, 0.8, 1.5, 2.5]),
]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/calibration_sandbox.py",
        description="Sweep the star-rating calibration over the frozen 120-chart benchmark ladder.",
    )
    parser.add_argument("--sweep", action="store_true", help="Sweep every RatingOptions constant in turn")
    parser.add_argument(
        "--set", action="append", default=[], metavar="FIELD=VALUE", help="Evaluate one calibration point"
    )
    parser.add_argument("--minimal", action="store_true", help="Print one line per evaluation point")
    parser.add_argument("--rebuild", action="store_true", help="Ignore the frozen core and rebuild it")
    parser.add_argument("--json", action="store_true", help="Emit the measurements as JSON")
    parser.add_argument(
        "--ladder",
        action="append",
        default=[],
        metavar="COLUMN",
        help="Print one column's 15-tier ladder against its registered gate. 'star_rating' (the "
        "default column) or a driver name such as 'driver_ln_inverse'.",
    )
    args = parser.parse_args(argv)

    columns = ["star_rating", *args.ladder]
    records = build_core(force=args.rebuild)

    # The fast path is only allowed to answer if it agrees with the real seam on every chart.
    baseline_options = RatingOptions()
    baseline = measure(records, baseline_options, columns=columns)
    worst_miss = max(
        abs(baseline["stars"][(r["technique"], r["tier"])] - r["reference_star"]) for r in records
    )
    if worst_miss > 1e-9:
        sys.stderr.write(
            f"fast path diverges from evaluate_intrinsic_difficulty by {worst_miss}★ — "
            f"refusing to sweep against a different engine\n"
        )
        return 1

    if args.json:
        print(json.dumps({"verdict": {k: v for k, v in baseline.items() if k != "stars"}}, indent=2))
        return 0

    print(f"frozen core: {len(records)} charts  |  self-check max |ΔSR| {worst_miss:.10f}")
    print()
    print("BASELINE (RatingOptions defaults)")
    print(f"  {format_verdict(baseline)}")
    if not args.minimal:
        print(format_margins(baseline))

    for column in args.ladder:
        print()
        print(format_ladder(baseline, column))

    if args.set:
        overrides = parse_assignments(args.set)
        options = replace(baseline_options, **overrides)
        candidate = measure(records, options, columns=columns)
        print()
        print("CANDIDATE " + " ".join(f"{k}={v}" for k, v in overrides.items()))
        print(f"  {format_verdict(candidate, baseline)}")
        if not args.minimal:
            print(format_margins(candidate))
        for column in args.ladder:
            print()
            print(format_ladder(candidate, column))

    if args.sweep:
        print()
        print("=" * 100)
        print("ONE-AT-A-TIME SWEEP")
        print("=" * 100)
        for name, multipliers in SWEEP_CASES:
            current = getattr(baseline_options, name)
            print(f"\n--- {name} (default {current}) ---")
            for multiplier in multipliers:
                options = replace(baseline_options, **{name: current * multiplier})
                m = measure(records, options)
                if args.minimal:
                    print(f"  ×{multiplier:<5} {format_verdict(m)}")
                else:
                    print(f"  ×{multiplier:<5} = {current * multiplier:<12.6f} {format_verdict(m, baseline)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
