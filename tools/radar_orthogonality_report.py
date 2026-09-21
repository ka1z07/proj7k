"""
Measure how separable the 8 technique drivers are over the frozen benchmark ladder.

The radar is only a radar if its axes mean different things. A driver that mostly restates the
chart's overall density makes every technique look alike: the second-highest dimension sits
just under the highest one, argmax wanders between neighbouring technologies, and the eight
scores collapse into one number plus a shape factor. This tool measures exactly that, on the
120-chart ladder where each technique's 15 tiers are known ground truth.

Four measurements, all on `compute_raw_technique_drivers`:

- **Separation** — second-highest driver / highest driver, per chart. Near 1.0 means the chart
  has no dominant technique; near 0 means one technique carries it.
- **Own-dimension hit rate** — how often a technique group's own axis is the argmax of its
  charts. A technique whose own axis never wins is not being measured at all.
- **Cross-correlation** — Spearman rho between every pair of drivers over the corpus, plus a
  per-technique view of what each driver moves with. Two axes that always move together are
  one axis wearing two names.
- **Argmax distribution** — which axis absorbs the corpus. A single axis taking a third of the
  120 charts means the others are not competing.

The thresholds the ticket commits to are `SEPARATION_MEDIAN_MAX`, `SEPARATION_HIGH_MAX`,
`LN_RELEASE_MIN_HITS` and `SPEED_MIN_HITS` below; `tests/test_radar_orthogonality.py` asserts
them so a regression fails CI rather than waiting to be noticed.

    PYTHONPATH=src python3 tools/radar_orthogonality_report.py            # the report
    PYTHONPATH=src python3 tools/radar_orthogonality_report.py --json     # machine-readable
"""

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from proj7k.assets import load_corpus_fixture
from proj7k.features import extract_beatmap_features
from proj7k.parser import parse_osu_7k
from proj7k.radar import TECHNIQUE_NAMES, compute_raw_technique_drivers

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "research" / "structured_index.json"
DEFAULT_CORPUS = REPO_ROOT / "tests" / "fixtures" / "benchmark_corpus.json.gz"

#: The manifest group each technique's ladder is filed under, and the axis it is supposed to
#: exercise. Keys are the axis names in `TECHNIQUE_NAMES`.
GROUP_FOR_TECHNIQUE: Dict[str, str] = {
    "jack": "Regular Jack",
    "tech": "Regular Tech",
    "speed": "Regular Speed",
    "stream": "Regular Stream",
    "ln_general": "LN General",
    "ln_tech": "LN Tech",
    "ln_inverse": "LN Inverse",
    "ln_release": "LN Release",
}

#: Acceptance thresholds from issue #47.
#:
#: Separation: the ticket's symptom is a median second/highest ratio of 0.72 with 51 of 120
#: charts above 0.8 — three or four axes high at once on most charts. Half the corpus is
#: allowed to sit at 0.5, and only a handful above 0.8.
SEPARATION_MEDIAN_MAX = 0.50
SEPARATION_HIGH_THRESHOLD = 0.8
SEPARATION_HIGH_MAX = 10

#: Own-axis performance. LN Release was 0/15 — its operator's magnitude never cleared the
#: General flux base — and Regular Speed 5/15, losing its own ladder to Tech and Stream. The
#: ticket asks for a majority on LN Release and 12 of 15 on Speed.
LN_RELEASE_MIN_HITS = 8
SPEED_MIN_HITS = 12


def load_ladder(
    manifest_path: Path = DEFAULT_MANIFEST,
    corpus_path: Path = DEFAULT_CORPUS,
) -> List[dict]:
    """The 120 benchmark charts as (group, tier, song, drivers, features) rows."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    corpus = load_corpus_fixture(corpus_path)

    rows: List[dict] = []
    for group, tiers in manifest.items():
        for tier, entry in tiers.items():
            beatmap = parse_osu_7k(corpus[int(entry["id"])])
            features = extract_beatmap_features(beatmap)
            rows.append(
                {
                    "group": group,
                    "tier": tier,
                    "song": entry.get("song", ""),
                    "features": features,
                    "drivers": compute_raw_technique_drivers(beatmap, features=features).to_dict(),
                }
            )
    return rows


def _separation(drivers: Dict[str, float]) -> float:
    """Second-highest / highest driver, or 0.0 when the chart has no driver at all."""
    ordered = sorted(drivers.values(), reverse=True)
    if len(ordered) < 2 or ordered[0] <= 0.0:
        return 0.0
    return ordered[1] / ordered[0]


def _argmax(drivers: Dict[str, float]) -> str:
    """The dominant axis, or 'None' for a chart with no measurable driver."""
    best = max(drivers, key=lambda name: drivers[name])
    return best if drivers[best] > 0.0 else "None"


def _ranks(values: Sequence[float]) -> List[float]:
    """Fractional ranks of `values`, ties sharing the mean of the ranks they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        shared = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[order[k]] = shared
        i = j
    return ranks


def _pairwise_rho(x: Sequence[float], y: Sequence[float]) -> float:
    """
    Spearman rho between two sequences — the pairwise form of
    `monotonicity.compute_spearman_rho`, which correlates one sequence against the tier order
    instead. Drivers are heavy-tailed (a jack driver spans two orders of magnitude across the
    corpus), so ranking first is what keeps one chart from deciding the coefficient.
    """
    rx, ry = _ranks(x), _ranks(y)
    n = len(rx)
    mean_x, mean_y = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    var_x = sum((rx[i] - mean_x) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_y) ** 2 for i in range(n))
    denominator = math.sqrt(var_x * var_y)
    return cov / denominator if denominator else 0.0


def cross_correlation(rows: Sequence[dict]) -> Dict[str, Dict[str, float]]:
    """Spearman rho between every pair of drivers over the corpus, as a nested dict."""
    columns = {name: [row["drivers"][name] for row in rows] for name in TECHNIQUE_NAMES}
    return {
        a: {b: round(_pairwise_rho(columns[a], columns[b]), 4) for b in TECHNIQUE_NAMES}
        for a in TECHNIQUE_NAMES
    }


def summarize(rows: Sequence[dict]) -> dict:
    """Every measurement the ticket's acceptance criteria are stated in terms of."""
    ratios = [_separation(row["drivers"]) for row in rows]
    argmaxes = [_argmax(row["drivers"]) for row in rows]

    by_group: Dict[str, List[dict]] = {}
    for row in rows:
        by_group.setdefault(row["group"], []).append(row)

    per_group = {}
    for group, group_rows in by_group.items():
        own_axis = next(
            (axis for axis, owner in GROUP_FOR_TECHNIQUE.items() if owner == group), None
        )
        group_ratios = [_separation(row["drivers"]) for row in group_rows]
        hits = sum(1 for row in group_rows if _argmax(row["drivers"]) == own_axis)
        per_group[group] = {
            "own_axis": own_axis,
            "charts": len(group_rows),
            "own_axis_hits": hits,
            "median_separation": round(statistics.median(group_ratios), 4),
        }

    argmax_distribution = {name: argmaxes.count(name) for name in TECHNIQUE_NAMES}
    argmax_distribution["None"] = argmaxes.count("None")

    return {
        "charts": len(rows),
        "median_separation": round(statistics.median(ratios), 4),
        "charts_above_high_threshold": sum(
            1 for ratio in ratios if ratio > SEPARATION_HIGH_THRESHOLD
        ),
        "charts_above_half": sum(1 for ratio in ratios if ratio > 0.5),
        "per_group": per_group,
        "argmax_distribution": argmax_distribution,
        "cross_correlation": cross_correlation(rows),
    }


def verdict(summary: dict) -> Tuple[bool, List[str]]:
    """The ticket's acceptance thresholds, each with the number that decided it."""
    failures: List[str] = []

    median = summary["median_separation"]
    if median > SEPARATION_MEDIAN_MAX:
        failures.append(
            f"median separation {median:.4f} > {SEPARATION_MEDIAN_MAX} (axes are still coupled)"
        )

    high = summary["charts_above_high_threshold"]
    if high > SEPARATION_HIGH_MAX:
        failures.append(f"{high} charts above {SEPARATION_HIGH_THRESHOLD} > {SEPARATION_HIGH_MAX}")

    release_hits = summary["per_group"]["LN Release"]["own_axis_hits"]
    if release_hits < LN_RELEASE_MIN_HITS:
        failures.append(f"LN Release own-axis hits {release_hits}/15 < {LN_RELEASE_MIN_HITS}")

    speed_hits = summary["per_group"]["Regular Speed"]["own_axis_hits"]
    if speed_hits < SPEED_MIN_HITS:
        failures.append(f"Regular Speed own-axis hits {speed_hits}/15 < {SPEED_MIN_HITS}")

    return (not failures), failures


def format_report(summary: dict) -> str:
    lines: List[str] = []
    lines.append(f"charts: {summary['charts']}")
    lines.append("")
    lines.append("=== separation (second-highest / highest driver) ===")
    lines.append(
        f"median                    {summary['median_separation']:.4f}   "
        f"(threshold <= {SEPARATION_MEDIAN_MAX})"
    )
    lines.append(
        f"charts > {SEPARATION_HIGH_THRESHOLD}            {summary['charts_above_high_threshold']:3d}   "
        f"(threshold <= {SEPARATION_HIGH_MAX})"
    )
    lines.append(f"charts > 0.5              {summary['charts_above_half']:3d}")
    lines.append("")
    lines.append("=== per group ===")
    lines.append(f"{'group':16} {'own axis':12} {'hits':>6}  {'median sep':>10}")
    for group, stats in summary["per_group"].items():
        lines.append(
            f"{group:16} {stats['own_axis']:12} {stats['own_axis_hits']:3d}/{stats['charts']:<2}"
            f"  {stats['median_separation']:10.4f}"
        )
    lines.append("")
    lines.append("=== argmax distribution ===")
    lines.append(
        "  ".join(f"{axis} {count}" for axis, count in summary["argmax_distribution"].items())
    )
    lines.append("")
    lines.append("=== cross-correlation (Spearman rho over the corpus) ===")
    header = "".join(f"{name[:9]:>11}" for name in TECHNIQUE_NAMES)
    lines.append(f"{'':16}{header}")
    for axis in TECHNIQUE_NAMES:
        row = "".join(f"{summary['cross_correlation'][axis][b]:11.3f}" for b in TECHNIQUE_NAMES)
        lines.append(f"{axis:16}{row}")

    passed, failures = verdict(summary)
    lines.append("")
    lines.append("Radar orthogonality: " + ("PASSED" if passed else "FAILED"))
    for failure in failures:
        lines.append(f"  - {failure}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--json", action="store_true", help="Emit the measurements as JSON")
    args = parser.parse_args(argv)

    summary = summarize(load_ladder(args.manifest, args.corpus))

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(format_report(summary))

    return 0 if verdict(summary)[0] else 1


if __name__ == "__main__":
    raise SystemExit(main())
