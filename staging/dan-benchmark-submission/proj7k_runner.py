"""
Benchmark runner for proj7k on VSRG-DanEstimation-Benchmark (follows docs/runner.md).

Usage (from a checkout of https://github.com/ka1z07/proj7k, Python 3.13+, numpy + scipy):
    PYTHONPATH=src python3 proj7k_runner.py <benchmark repo root> [star_to_dan_isotonic.json]

It reads samples/data.csv, finds samples/{pattern}/{name}.osu (exact -> normalised -> "Name (...)"),
runs the proj7k engine at 1.0x with the 4K columns 0,1,2,3 placed on the engine's 7K columns 1,2,4,5
(left middle, left index | right index, right middle), maps the total stars to the benchmark's numeric
difficulty through the isotonic knot table, and writes results/proj7k.csv.
"""
import csv
import json
import math
import os
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from proj7k.engine.evaluate import evaluate_notes

COL_4K_TO_7K = {0: 1, 1: 2, 2: 4, 3: 5}
HEADER = ["bid", "name", "pattern", "subPattern", "expected", "got", "delta", "deltaAbs"]


def parse_osu(path):
    """(key count, [(column, head_s, tail_s or None)]) of a mania .osu file."""
    section, keys, notes = "", None, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section == "Difficulty" and line.startswith("CircleSize"):
                keys = int(float(line.split(":")[1]))
            elif section == "HitObjects":
                p = line.split(",")
                col = min(keys - 1, max(0, math.floor(int(float(p[0])) * keys / 512)))
                tail = int(float(p[5].split(":")[0])) / 1000.0 if int(p[3]) & 128 else None
                notes.append((col, int(float(p[2])) / 1000.0, tail))
    return keys, notes


def stars(path):
    try:
        keys, notes = parse_osu(path)
        if keys != 4:
            return "Invalid"
        return evaluate_notes([(COL_4K_TO_7K[c], h, t) for c, h, t in notes]).total_stars
    except Exception:
        return "Failed"


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def find_chart(root, pattern, name):
    d = os.path.join(root, "samples", pattern)
    if not os.path.isdir(d):
        return None
    stems = {}
    for r, _, files in os.walk(d):
        for fn in files:
            if fn.endswith(".osu"):
                stems[fn[:-4]] = os.path.join(r, fn)
    if name in stems:
        return stems[name]
    for k, p in stems.items():
        if _norm(k) == _norm(name):
            return p
    spaced = lambda s: _norm(re.sub(r"\s*\(", " (", s))
    for k, p in stems.items():
        if spaced(k) == spaced(name):
            return p
    return None


def main():
    root = sys.argv[1]
    table = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "star_to_dan_isotonic.json")
    knots = np.array(json.load(open(table))["knots"])
    rows = list(csv.DictReader(open(os.path.join(root, "samples", "data.csv"), encoding="utf-8")))
    paths = [find_chart(root, r["pattern"], r["name"]) for r in rows]
    with ProcessPoolExecutor() as ex:
        got = dict(zip([p for p in paths if p], ex.map(stars, [p for p in paths if p], chunksize=4)))
    status = Counter()
    out = os.path.join(root, "results", "proj7k.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADER)
        for r, p in zip(rows, paths):
            s = got.get(p, "Missing") if p else "Missing"
            if isinstance(s, str):
                status[s] += 1
                w.writerow([r["bid"], r["name"], r["pattern"], r["subPattern"], r["expected"], s, "", ""])
                continue
            status["OK"] += 1
            g = round(float(np.interp(s, knots[:, 0], knots[:, 1])), 2)
            try:
                d = round(float(r["expected"]) - g, 2)
                w.writerow([r["bid"], r["name"], r["pattern"], r["subPattern"], r["expected"], f"{g:.2f}", f"{d:.2f}", f"{abs(d):.2f}"])
            except ValueError:
                w.writerow([r["bid"], r["name"], r["pattern"], r["subPattern"], r["expected"], f"{g:.2f}", "", ""])
    print(f"proj7k: ok={status['OK']} invalid={status['Invalid']} missing={status['Missing']} failed={status['Failed']} total={len(rows)}")


if __name__ == "__main__":
    main()
