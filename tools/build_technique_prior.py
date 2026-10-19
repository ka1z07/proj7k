"""
Build the technique prior (ADR-0033 decision 8) from `tools/export_rhythm_data.py` output: how people's
charts spread their techniques over a song's sections.

Each section's primary technique is the engine skill with the most stars over the section's notes. Per
star band of the chart (`BANDS`) the prior records how many techniques a chart uses (each covering at
least `MIN_SHARE` of its measures), how often each technique is a section's primary (by measures), which
techniques appear together, which ones the quiet, middle and loud thirds of a song get, and the charts'
share of long notes.

    PYTHONPATH=src python3 tools/build_technique_prior.py --data ~/.cache/proj7k/technique-data.npz
"""

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np

from proj7k.engine import SKILLS
from proj7k.generator.brief import PRIOR_PATH

BANDS = (("low", 0.0, 4.0), ("mid", 4.0, 7.0), ("high", 7.0, 99.0))
ENERGY = (("quiet", 0.0, 1 / 3), ("middle", 1 / 3, 2 / 3), ("loud", 2 / 3, 1.01))
MIN_SHARE = 0.15
MAX_UNMATCHED = 0.2


def build(d) -> dict:
    good = np.asarray(d["unmatched"]) <= MAX_UNMATCHED
    chart_stars = np.asarray(d["chart_stars"])
    sec_chart = np.asarray(d["sec_chart"])
    sec_bars = np.asarray(d["sec_bars"])
    width = sec_bars[:, 1] - sec_bars[:, 0] + 1
    primary = np.asarray(d["sec_stars"]).argmax(axis=1)
    energy = np.asarray(d["sec_energy"])
    out = {"charts": int(good.sum()), "bands": {}}
    for name, lo, hi in BANDS:
        charts = [c for c in np.flatnonzero(good) if lo <= chart_stars[c] < hi]
        n_used = collections.Counter()
        share = np.zeros(len(SKILLS))
        together = np.zeros((len(SKILLS), len(SKILLS)))
        used_count = np.zeros(len(SKILLS))
        by_energy = {e: np.zeros(len(SKILLS)) for e, _, _ in ENERGY}
        for c in charts:
            m = sec_chart == c
            if not m.any():
                continue
            bars_by = np.bincount(primary[m], weights=width[m], minlength=len(SKILLS))
            share += bars_by
            used = bars_by / bars_by.sum() >= MIN_SHARE
            n_used[int(used.sum())] += 1
            used_count += used
            together += np.outer(used, used)
            for e, a, b in ENERGY:
                k = m & (energy >= a) & (energy < b)
                by_energy[e] += np.bincount(primary[k], weights=width[k], minlength=len(SKILLS))
        if not charts:
            continue
        total = max(share.sum(), 1e-9)
        out["bands"][name] = {
            "stars": [lo, hi],
            "charts": len(charts),
            "techniques_per_chart": {str(k): round(v / max(sum(n_used.values()), 1), 3) for k, v in sorted(n_used.items())},
            "primary_share": {s: round(float(v / total), 3) for s, v in zip(SKILLS, share)},
            "used_with": {a: {b: round(float(together[i, j] / used_count[i]), 3) for j, b in enumerate(SKILLS) if j != i and together[i, j] > 0}
                          for i, a in enumerate(SKILLS) if used_count[i] > 0},
            "by_energy": {e: {s: round(float(v / max(w.sum(), 1e-9)), 3) for s, v in zip(SKILLS, w) if v > 0}
                          for e, w in by_energy.items()},
            "ln_share_quartiles": [round(float(q), 3) for q in np.percentile(np.asarray(d["ln_share"])[charts], [25, 50, 75])],
        }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data", required=True)
    p.add_argument("--output", default=str(PRIOR_PATH))
    args = p.parse_args(argv)
    prior = build(np.load(args.data))
    Path(args.output).write_text(json.dumps(prior, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: {kk: v[kk] for kk in ("charts", "techniques_per_chart", "primary_share")}
                      for k, v in prior["bands"].items()}, ensure_ascii=False, indent=1))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
