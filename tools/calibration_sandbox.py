"""
Sweep the star-rating calibration over the frozen benchmark ladder.

The engine's star rating is a composition of two very different things: everything upstream
of the star scale (feature extraction, the dual-hand strain accumulator, the 8 raw technique
drivers) and the pure arithmetic that carries those numbers onto the star scale (the anchor
law, the driver back-pressure exponent, the p-norm aggregation, the tanh soft cap). Only the
second half reads the calibration constants — so the first half can be computed once over the
frozen corpus and frozen, after which a calibration point costs arithmetic alone.

That is what makes a sweep cheap: the 120 charts are evaluated in full once, and every
constant after that is answered from the frozen core. A full one-at-a-time sweep of every
`RatingOptions` constant (`RatingOptions.methodology_fingerprint`'s inputs, plus the dan
anchors it folds in) runs in seconds instead of tens of minutes.

The tool never writes to the engine. It answers "what would this calibration do to the
ladder" — whether the 15-tier monotonicity, the anchor-tier medians and the CI gate would
survive — before anyone commits a constant change and re-baselines the star-rating checksum.

The fast path is asserted against the real `evaluate_intrinsic_difficulty` seam on every run:
if the two ever disagree by even 1e-9 of a star, the run aborts rather than reporting numbers
that describe a different engine. See `docs/methodology/calibration-sensitivity-and-sandbox.md`
for what the sweep found on the current calibration.

    PYTHONPATH=src python3 tools/calibration_sandbox.py                  # baseline only
    PYTHONPATH=src python3 tools/calibration_sandbox.py --sweep          # one-at-a-time sweep
    PYTHONPATH=src python3 tools/calibration_sandbox.py --set strain_a=0.242 --set p_norm=6
"""

import argparse
from dataclasses import fields, replace
import hashlib
import json
import math
from pathlib import Path
import pickle
import statistics
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from proj7k.assets import load_corpus_fixture
from proj7k.dan import CANONICAL_DAN_SR_BANDS
from proj7k.difficulty import evaluate_intrinsic_difficulty
from proj7k.features import extract_beatmap_features
from proj7k.guard import CALIBRATED_METRIC_GATES, DEFAULT_MIN_ANCHOR_SAMPLES
from proj7k.monotonicity import TIER_ORDER, compute_kendall_tau, compute_spearman_rho
from proj7k.parser import parse_osu_7k
from proj7k.radar import (
    TECHNIQUE_NAMES,
    RadarOptions,
    TechniqueRadar,
    compute_raw_technique_drivers,
)
from proj7k.rating import RatingOptions, synthesize_star_rating
from proj7k.strain import compute_dual_hand_strain

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "research" / "structured_index.json"
CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "benchmark_corpus.json.gz"
CACHE_PATH = REPO_ROOT / ".cache" / "proj7k-sandbox" / "core.pkl"

#: The sweep's own gate thresholds come from the CI guard rather than being re-typed here, so
#: a recalibrated gate moves both at once.
_GATE = CALIBRATED_METRIC_GATES["star_rating"]


def _source_digest() -> str:
    """
    Digest of the engine source the frozen core was built from.

    The core is a cache of feature tensors, P90 strain and raw drivers — everything the
    calibration constants do *not* touch. Keying it by the engine's methodology fingerprint
    would be the obvious choice and the wrong one: a sweep exists precisely to move constants
    the fingerprint tracks, but the *upstream* maths has literals the fingerprint does not
    track at all (see the fingerprint-coverage issue). Hashing the source catches both, so a
    core built before an upstream edit is rebuilt instead of silently supplying stale numbers
    — the failure mode the repository's Layer-2 feature cache already lived through.
    """
    digest = hashlib.sha256()
    for path in sorted((REPO_ROOT / "src" / "proj7k").rglob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_core(force: bool = False) -> List[dict]:
    """Evaluates every benchmark chart in full and freezes everything upstream of the stars."""
    digest = _source_digest()
    if CACHE_PATH.exists() and not force:
        cached = pickle.loads(CACHE_PATH.read_bytes())
        if cached.get("digest") == digest:
            return cached["records"]

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus = load_corpus_fixture(CORPUS_PATH)

    records: List[dict] = []
    for technique, tiers in manifest.items():
        for tier, entry in tiers.items():
            content = corpus[int(entry["id"])]
            beatmap = parse_osu_7k(content)
            features = extract_beatmap_features(beatmap)
            strain = compute_dual_hand_strain(beatmap)
            records.append(
                {
                    "technique": technique,
                    "tier": tier,
                    "song": entry["song"],
                    "p90": strain.p90_strain,
                    "drivers": compute_raw_technique_drivers(beatmap, features=features).to_dict(),
                    "reference_star": evaluate_intrinsic_difficulty(content).star_rating,
                }
            )

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_bytes(pickle.dumps({"digest": digest, "records": records}))
    return records


def _radar_from_drivers(record: dict, options: RatingOptions) -> TechniqueRadar:
    """The star mapping of `radar.compute_technique_radar`, over a frozen driver vector."""
    calibration = options.calibration
    sr_base = calibration.star_rating_from_strain(record["p90"])
    drivers = record["drivers"]
    max_raw = max(drivers.values())
    if max_raw <= 1e-6 or sr_base <= 1e-6:
        scores = {name: 0.0 for name in TECHNIQUE_NAMES}
    else:
        ceiling = RadarOptions().score_ceiling
        scores = {
            name: min(
                ceiling, sr_base * math.pow(value / max_raw, calibration.driver_backpressure_exp)
            )
            for name, value in drivers.items()
        }
    dominant = max(drivers, key=lambda k: drivers[k]) if max_raw > 1e-6 else "None"
    return TechniqueRadar(
        **{name: scores[name] for name in TECHNIQUE_NAMES},
        dominant_technique=dominant,
        dominant_score=scores.get(dominant, 0.0),
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


def measure(records: Sequence[dict], options: RatingOptions) -> dict:
    """Ladder metrics for one calibration point: ordering, anchor medians, and the CI verdict."""
    stars = ratings(records, options)

    tau_min, rho_min, inversions, inv_max = 1.0, 1.0, 0, 0
    worst_technique = ""
    for technique in {r["technique"] for r in records}:
        values = [
            stars[(r["technique"], r["tier"])]
            for r in sorted(
                (r for r in records if r["technique"] == technique),
                key=lambda r: TIER_ORDER.index(r["tier"]),
            )
        ]
        tau = compute_kendall_tau(values)
        rho = compute_spearman_rho(values)
        # An adjacency inversion, which is what the guard counts — and per technique, which is
        # the scope its max_violations ceiling applies at.
        inv = sum(1 for a, b in zip(values, values[1:]) if b - a < -1e-4)
        inversions += inv
        inv_max = max(inv_max, inv)
        if tau < tau_min:
            tau_min, worst_technique = tau, technique
        rho_min = min(rho_min, rho)

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
    ("driver_backpressure_exp", [0.7, 0.85, 1.15, 1.3]),
    ("p_norm", [0.5, 0.75, 1.5, 2.0]),
    ("damping_coeff", [0.0, 0.5, 2.0, 4.0]),
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
    args = parser.parse_args(argv)

    records = build_core(force=args.rebuild)

    # The fast path is only allowed to answer if it agrees with the real seam on every chart.
    baseline_options = RatingOptions()
    baseline = measure(records, baseline_options)
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

    if args.set:
        overrides = parse_assignments(args.set)
        options = replace(baseline_options, **overrides)
        candidate = measure(records, options)
        print()
        print("CANDIDATE " + " ".join(f"{k}={v}" for k, v in overrides.items()))
        print(f"  {format_verdict(candidate, baseline)}")
        if not args.minimal:
            print(format_margins(candidate))

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
