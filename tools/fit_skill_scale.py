"""
Refit the eight skill star scales (ADR-0056) on the 120 benchmark charts and print `scale.SKILL_SCALE`.

    PYTHONPATH=src python3 tools/fit_skill_scale.py

Each skill's line goes through its own pool's tier medians of D_k against the family ladder on the total
scale: the RC ladder is the four Regular pools' tier median of total stars, the LN ladder the four LN pools'
tier geometric mean. Run it after any change to a skill's cost or to the total's scale, and paste the output.
"""

import gzip
import json
import sys
from pathlib import Path
from statistics import geometric_mean, median

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proj7k.engine import evaluate_osu  # noqa: E402
from proj7k.engine.scale import fit_skill_scale  # noqa: E402
from proj7k.engine.skills import BENCHMARK_POOL_SKILL, SKILLS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TIERS = ["0th", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "Gamma", "Azimuth", "Zenith", "Stellium"]


def family_ladders(profiles):
    rc = [p for p, k in BENCHMARK_POOL_SKILL.items() if k.startswith("rc")]
    ln = [p for p, k in BENCHMARK_POOL_SKILL.items() if k.startswith("ln")]
    return {
        "rc": {t: median(profiles[(p, t)].total_stars for p in rc) for t in TIERS},
        "ln": {t: geometric_mean([profiles[(p, t)].total_stars for p in ln]) for t in TIERS},
    }


def fit_all(profiles):
    ladders = family_ladders(profiles)
    out = {}
    for pool, skill in BENCHMARK_POOL_SKILL.items():
        tier_D = {t: profiles[(pool, t)].skills[skill].D for t in TIERS}
        out[skill] = fit_skill_scale(tier_D, ladders[skill[:2]])
    return out


def main():
    manifest = json.loads((ROOT / "docs/research/structured_index.json").read_text(encoding="utf-8"))
    corpus = json.load(gzip.open(ROOT / "tests/fixtures/benchmark_corpus.json.gz"))
    profiles = {(pool, t): evaluate_osu(corpus[str(manifest[pool][t]["id"])]) for pool in BENCHMARK_POOL_SKILL for t in TIERS}
    fit = fit_all(profiles)
    print("SKILL_SCALE: Dict[str, Tuple[float, float]] = {")
    for k in SKILLS:
        print(f'    "{k}": ({fit[k][0]!r}, {fit[k][1]!r}),')
    print("}")


if __name__ == "__main__":
    main()
