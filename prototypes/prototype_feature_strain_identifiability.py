#!/usr/bin/env python3
"""
PROTOTYPE (throwaway) — does the feature vector determine the strain reading?

This answers claim (a) from the difficulty-engine re-architecture thread:

    「给定任何特征向量的数值都可以构造 strain 悬殊的铺面」

Method: mutate **only `column`, never `time`**. Every timing-derived quantity the feature
tensor computes is then provably bit-identical by construction, so any divergence between the
two readouts can only come from topology. That is the strongest available form of the test —
if strain moves while the whole timing half of the feature vector stands still, the feature
vector does not determine strain.

Each variant is read through the live engine (NOT `benchmark_core`, whose whole purpose is to
freeze the *unmutated* corpus and which caches by source digest):

    features → 8 raw drivers → 8 radar stars → synthesized star, alongside P90 strain.

Controls, because a divergence claim needs to show the harness can also read *zero*:

- `identity`        — must reproduce every number exactly. Validates the plumbing.
- `mirror`          — a global L↔R mirror: a near-symmetry of the chart's hand structure.
- `single_note`     — one note's column moved. Validates that the harness resolves the
                      smallest possible topology change, so "delta ≈ 0" elsewhere means
                      something.

Mutation families, in rough order of expected hostility to strain:

- `lane_permutation` — a random bijection on the 7 columns. Preserves the lane histogram
                       multiset, every per-onset note count, and all timing exactly.
- `random(p)`        — each note's column resampled uniformly. The null / upper bound.
- `hand_reassign(p)` — each note moved into the *other* hand's lane set.
- `collapse_center(p)` — each note moved onto the centre lane (S), building jacks.
- `jackify(p)`       — each note moved onto its predecessor's column, building stagnation.

Usage:
  PYTHONPATH=src python3 prototypes/prototype_feature_strain_identifiability.py
  PYTHONPATH=src python3 prototypes/prototype_feature_strain_identifiability.py --charts 2 --seeds 1
"""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proj7k.assets import load_corpus_fixture
from proj7k.benchmark_core import CORPUS_PATH, MANIFEST_PATH
from proj7k.features import extract_beatmap_features
from proj7k.parser import parse_osu_7k
from proj7k.radar import TECHNIQUE_NAMES, compute_raw_technique_drivers, compute_technique_radar
from proj7k.rating import synthesize_star_rating
from proj7k.strain import compute_dual_hand_strain

LEFT = (0, 1, 2)
RIGHT = (4, 5, 6)
CENTRE = 3

#: (technique, tier) pulled from the frozen 120-chart manifest. Spread across both tracks and
#: across the ladder, so a verdict is not an artifact of one idiom or one difficulty band.
CHARTS = [
    ("Regular Jack", "Azimuth"),
    ("Regular Stream", "10th"),
    ("Regular Tech", "Zenith"),
    ("Regular Speed", "Stellium"),
    ("LN General", "Gamma"),
    ("LN Inverse", "8th"),
]

STRENGTHS = (0.1, 0.35, 1.0)


# --------------------------------------------------------------------------- engine read

def evaluate(beatmap):
    """Every readout the two layers produce for one chart, in one dict."""
    features = extract_beatmap_features(beatmap)
    drivers = compute_raw_technique_drivers(beatmap, features=features)
    strain = compute_dual_hand_strain(beatmap)
    radar = compute_technique_radar(beatmap, features=features, drivers=drivers)
    synthesis = synthesize_star_rating(radar=radar, p90_strain=strain.p90_strain)
    return {
        "features": features.to_dict(),
        "drivers": drivers.to_dict(),
        "radar": {name: getattr(radar, name) for name in TECHNIQUE_NAMES},
        "p90": strain.p90_strain,
        "p95": strain.p95_strain,
        "peak": strain.peak_strain,
        "star": synthesis.star_rating,
        "sr_raw": synthesis.raw_strain_rating,
    }


# --------------------------------------------------------------------------- mutation

def _columns(beatmap):
    return [ho.column for ho in sorted(beatmap.hit_objects, key=lambda h: (h.time, h.column))]


def _rebuild(beatmap, columns):
    """
    A copy of the chart with the given columns, times and note types untouched.

    Re-sorted to (time, column) afterwards, so a variant is a pure function of the content —
    the multiset of (time, column, type) — and not of the order the note lines happened to be
    written in. That matters: `features.py` sorts by `time` alone (stable), so intra-chord line
    order survives from the `.osu` file into `spatial_entropy` (see the second probe below).
    The base reading is put through this same path, or `identity` would read the reordering.
    """
    ordered = sorted(beatmap.hit_objects, key=lambda h: (h.time, h.column))
    new_objects = [
        replace(ho, column=int(column)) for ho, column in zip(ordered, columns)
    ]
    new_objects.sort(key=lambda h: (h.time, h.column))
    return replace(beatmap, hit_objects=new_objects)


def _rebuild_shuffled_intrachord(beatmap, rng):
    """
    The musically-inert mutation: reorder the note lines *inside* each chord, changing nothing
    about which lanes sound at which time. Only the file's serialization order changes.
    """
    groups = {}
    for ho in beatmap.hit_objects:
        groups.setdefault(ho.time, []).append(ho)
    out = []
    for time in sorted(groups):
        group = list(groups[time])
        rng.shuffle(group)
        out.extend(group)
    return replace(beatmap, hit_objects=out)


def m_identity(columns, rng, p):
    return list(columns)


def m_mirror(columns, rng, p):
    return [6 - c for c in columns]


def m_single_note(columns, rng, p):
    out = list(columns)
    i = rng.randrange(len(out))
    out[i] = (out[i] + 3) % 7
    return out


def m_lane_permutation(columns, rng, p):
    perm = list(range(7))
    rng.shuffle(perm)
    return [perm[c] for c in columns]


def m_random(columns, rng, p):
    return [rng.randrange(7) if rng.random() < p else c for c in columns]


def m_hand_reassign(columns, rng, p):
    out = []
    for c in columns:
        if rng.random() < p:
            pool = LEFT if c in RIGHT else RIGHT if c in LEFT else tuple(range(7))
            out.append(rng.choice(pool))
        else:
            out.append(c)
    return out


def m_collapse_center(columns, rng, p):
    return [CENTRE if rng.random() < p else c for c in columns]


def m_jackify(columns, rng, p):
    out = list(columns)
    for i in range(1, len(out)):
        if rng.random() < p:
            out[i] = out[i - 1]
    return out


FAMILIES = {
    "identity": m_identity,
    "mirror": m_mirror,
    "single_note": m_single_note,
    "lane_permutation": m_lane_permutation,
    "random": m_random,
    "hand_reassign": m_hand_reassign,
    "collapse_center": m_collapse_center,
    "jackify": m_jackify,
}


# --------------------------------------------------------------------------- distance

def flatten(features):
    """`BeatmapFeatures` as a flat name → float map, `lockout_profile` included."""
    flat = {}
    for key, value in features.items():
        if isinstance(value, dict):
            for sub, sub_value in value.items():
                flat[f"{key}[{sub}]"] = float(sub_value)
        elif isinstance(value, (int, float)):
            flat[key] = float(value)
    return flat


def deltas(base_flat, other_flat):
    """
    Per-field relative difference. A field whose magnitude is below 1 is compared with an
    absolute tolerance instead, so counts and rates are held to a comparable standard.
    """
    out = {}
    for key, x in base_flat.items():
        y = other_flat[key]
        scale = max(abs(x), abs(y), 1.0)
        out[key] = abs(x - y) / scale
    return out


# --------------------------------------------------------------------------- driver

def build_index(manifest, corpus):
    index = {}
    for technique, tiers in manifest.items():
        for tier, entry in tiers.items():
            index[(technique, tier)] = corpus[int(entry["id"])]
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charts", type=int, default=len(CHARTS))
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus = load_corpus_fixture(CORPUS_PATH)
    index = build_index(manifest, corpus)

    all_records = []
    invariance = {}

    for technique, tier in CHARTS[: args.charts]:
        content = index.get((technique, tier))
        if content is None:
            print(f"!! manifest has no {technique} / {tier}", file=sys.stderr)
            continue
        beatmap = parse_osu_7k(content)
        columns = _columns(beatmap)
        canonical = _rebuild(beatmap, columns)
        base = evaluate(canonical)
        base_flat = flatten(base["features"])
        print(
            f"\n=== {technique} / {tier} — {len(columns)} notes, "
            f"base: star {base['star']:.2f}, P90 {base['p90']:.1f}, "
            f"max radar axis {max(base['radar'].values()):.2f} ==="
        )

        variants = [("identity", 0.0, 0), ("mirror", 0.0, 0), ("single_note", 0.0, 0)]
        variants.append(("lane_permutation", 0.0, 0))
        for family in ("random", "hand_reassign", "collapse_center", "jackify"):
            for strength in STRENGTHS:
                for seed in range(args.seeds):
                    variants.append((family, strength, seed))

        rows = []
        for family, strength, seed in variants:
            rng = random.Random(f"{technique}|{tier}|{family}|{strength}|{seed}")
            mutated_columns = FAMILIES[family](columns, rng, strength)
            mutated = _rebuild(canonical, mutated_columns)
            result = evaluate(mutated)
            d = deltas(base_flat, flatten(result["features"]))
            row = {
                "technique": technique,
                "tier": tier,
                "family": family,
                "strength": strength,
                "seed": seed,
                "feature_linf": max(d.values()),
                "feature_mean": sum(d.values()) / len(d),
                "dp90": result["p90"] - base["p90"],
                "dp90_rel": (result["p90"] - base["p90"]) / base["p90"] if base["p90"] else 0.0,
                "dp95_rel": (result["p95"] - base["p95"]) / base["p95"] if base["p95"] else 0.0,
                "dpeak_rel": (result["peak"] - base["peak"]) / base["peak"] if base["peak"] else 0.0,
                "dstar": result["star"] - base["star"],
                "dsr_raw": result["sr_raw"] - base["sr_raw"],
                "changed_fields": sorted(k for k, v in d.items() if v > 0.0),
                "deltas": d,
            }
            rows.append(row)
            all_records.append({k: v for k, v in row.items() if k != "deltas"})

            invariance.setdefault((technique, tier), {})
            for key, value in d.items():
                invariance[(technique, tier)][key] = (
                    invariance[(technique, tier)].get(key, True) and value == 0.0
                )

        identity = next(r for r in rows if r["family"] == "identity")
        if identity["feature_linf"] != 0.0 or identity["dp90_rel"] != 0.0:
            raise SystemExit(
                f"HARNESS BROKEN: identity moved (featL∞={identity['feature_linf']}, "
                f"ΔP90={identity['dp90_rel']}) — the base and the variants are not on the "
                f"same construction path."
            )

        # -- second probe: reorder note lines inside chords. Musically inert; is it?
        probe = evaluate(_rebuild_shuffled_intrachord(canonical, random.Random(0)))
        probe_d = deltas(base_flat, flatten(probe["features"]))
        probe_moved = sorted(k for k, v in probe_d.items() if v > 0.0)
        print(
            f"    [intra-chord line order] featL∞={max(probe_d.values()):.4f}  "
            f"ΔP90={100 * (probe['p90'] - base['p90']) / base['p90']:+.1f}%  "
            f"Δstar={probe['star'] - base['star']:+.2f}  fields moved: {probe_moved}"
        )

        rows.sort(key=lambda r: (r["feature_linf"], -abs(r["dp90_rel"])))
        print(
            f"{'family':<18}{'p':>5}{'s':>3}  {'featL∞':>8}{'featAvg':>9}"
            f"  {'ΔP90%':>8}{'Δstar':>8}{'Δsr_raw':>9}   fields moved"
        )
        for r in rows:
            print(
                f"{r['family']:<18}{r['strength']:>5}{r['seed']:>3}  "
                f"{r['feature_linf']:>8.4f}{r['feature_mean']:>9.4f}  "
                f"{100 * r['dp90_rel']:>8.1f}{r['dstar']:>8.2f}{r['dsr_raw']:>9.2f}"
                f"   {len(r['changed_fields'])}"
            )

    # ---------------------------------------------------------------- verdict tables

    print("\n\n================ INVARIANT UNDER EVERY COLUMN-ONLY MUTATION ================")
    for (technique, tier), fields in invariance.items():
        frozen = sorted(k for k, still in fields.items() if still)
        moved = sorted(k for k, still in fields.items() if not still)
        total = len(fields)
        print(f"\n{technique} / {tier}  — {len(frozen)}/{total} fields bit-identical")
        print(f"  frozen : {', '.join(frozen)}")
        print(f"  moved  : {', '.join(moved)}")

    print("\n\n========================= FRONTIER: strain per feature budget =========================")
    print(
        "For every variant within a feature-vector budget, the largest reading it bought.\n"
        "Two budgets: L∞ (the worst-moved single field) and the mean over all 37 fields. The\n"
        "mean is the fairer one — L∞ is dominated by `spatial_entropy`, which is both the most\n"
        "excursion-prone field and the one that moves with no musical change at all."
    )
    for label, key in (("feat L∞", "feature_linf"), ("feat mean", "feature_mean")):
        print(f"\n  budget on {label}")
        print(
            f"{'ε':>10}{'n':>5}{'max |ΔP90|/P90':>18}{'max |Δstar|':>13}{'max |Δsr_raw|':>16}"
        )
        for eps in (0.0, 0.001, 0.005, 0.01, 0.02, 0.05, 0.10, 0.25, 2.0):
            bucket = [r for r in all_records if r[key] <= eps and r["family"] != "identity"]
            if not bucket:
                continue
            print(
                f"{eps:>10.3f}{len(bucket):>5}"
                f"{100 * max(abs(r['dp90_rel']) for r in bucket):>17.1f}%"
                f"{max(abs(r['dstar']) for r in bucket):>13.2f}"
                f"{max(abs(r['dsr_raw']) for r in bucket):>16.2f}"
            )

    # The single most damning row: smallest feature movement, largest strain movement.
    interesting = [r for r in all_records if r["family"] not in ("identity",)]
    interesting.sort(key=lambda r: (-abs(r["dp90_rel"]), r["feature_linf"]))
    print("\n---------------- largest strain movements, cheapest first ----------------")
    for r in interesting[:12]:
        print(
            f"  {r['technique']:<15}{r['tier']:<9}{r['family']:<18}p={r['strength']:<5}"
            f" featL∞={r['feature_linf']:.4f}  ΔP90={100 * r['dp90_rel']:+.1f}%"
            f"  Δstar={r['dstar']:+.2f}  Δsr_raw={r['dsr_raw']:+.2f}"
        )

    if args.json:
        args.json.write_text(json.dumps(all_records, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
