"""
Compare Mapperatorinator's writing modes on one song (ADR-0045): wall time and what the chart looks like.

    PYTHONPATH=src python tools/mapperatorinator_modes.py --audio SONG --reference HUMAN.osu --difficulty 9.9 \
        --out DIR seq la0.2 la0.1 chains8

Each mode is one model run, with the reference's timing and no long notes. Modes: `seq` (its sequential mode,
a window every 10% of 16.4 s), `laX` (sequential, skipping only the last X of each window instead of 0.4, so a
window writes more and there are fewer of them), `chainsN` (our batched runs, `parallel_chains=N`). Prints one
JSON line per mode.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from proj7k.generator import mapperatorinator as m
from proj7k.parser import parse_osu_7k

PHRASE_BEATS = 8


def mode_args(name):
    if name == "seq":
        return False, []
    if name.startswith("la"):
        return False, [f"lookahead={float(name[2:]):.2f}"]
    if name.startswith("chains"):
        return False, [f"parallel_chains={int(name[6:])}"]
    if name == "fast":
        return True, []
    raise SystemExit(f"unknown mode {name}")


def rows(chart):
    out = {}
    for o in chart.hit_objects:
        out.setdefault(int(round(o.time)), set()).add(o.column)
    return out


def reused(chart):
    """Share of 8-beat phrases (with notes) that occur again elsewhere note for note or mirrored."""
    tp = next(t for t in chart.timing_points if t.uninherited)
    beat, offset = tp.beat_length, tp.time
    phrases = {}
    for t, cols in rows(chart).items():
        b = (t - offset) / beat
        k = int(b // PHRASE_BEATS)
        phrases.setdefault(k, set()).update((round((b - k * PHRASE_BEATS) * 12), c) for c in cols)
    keys = {k: frozenset(v) for k, v in phrases.items() if v}
    seen = {}
    for k, v in keys.items():
        seen.setdefault(v, []).append(k)
    hit = sum(1 for k, v in keys.items()
              if len(seen[v]) > 1 or frozenset((s, 6 - c) for s, c in v) in seen)
    return hit / max(1, len(keys))


def single_stretches(chart, seconds=4.0, nps=4.0):
    """Stretches of `seconds` or more where every row is a single note, at least `nps` rows a second."""
    out, run = 0, []
    for t, cols in sorted(rows(chart).items()):
        if len(cols) == 1 and (not run or t - run[-1] <= 1000 / nps):
            run.append(t)
            continue
        if run and run[-1] - run[0] >= seconds * 1000:
            out += 1
        run = [t] if len(cols) == 1 else []
    return out + (1 if run and run[-1] - run[0] >= seconds * 1000 else 0)


def describe(chart, reference):
    r = rows(chart)
    sizes = np.array([len(c) for _, c in sorted(r.items())])
    ref_times = sorted(o.time for o in reference.hit_objects)
    end = max(ref_times[-1], max(r))
    bins = np.arange(0, end + 4000, 4000)
    mine = np.histogram([o.time for o in chart.hit_objects], bins)[0]
    ref = np.histogram(ref_times, bins)[0]
    holes = [(a, b) for a, b in m.gaps(chart, 4)
             if sum(1 for t in ref_times if a < t < b) >= (b - a) / 1000 * 2]
    return {
        "notes": len(chart.hit_objects), "rows": len(r),
        "row_size": round(float(sizes.mean()), 2),
        "row_size_jump": round(float(np.abs(np.diff(sizes)).mean()), 2),
        "density_corr": round(float(np.corrcoef(mine, ref)[0, 1]), 2),
        "holes": len(holes), "hole_s": round(sum(b - a for a, b in holes) / 1000, 1),
        "reuse": round(reused(chart), 2),
        "single_stretches": single_stretches(chart),
        "ln_streams": len(m.ln_streams(chart)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--difficulty", type=float, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("modes", nargs="+")
    a = ap.parse_args()
    reference = parse_osu_7k(a.reference.read_text(encoding="utf-8", errors="replace"))
    audio = m.model_audio(a.audio, a.out / "_audio")
    print(json.dumps({"mode": "human", **describe(reference, reference)}), flush=True)
    for name in a.modes:
        fast, extra = mode_args(name)
        out = a.out / name
        args = m.overrides(audio, out, a.difficulty, a.seed, hold_note_ratio=0.0, timing_from=a.reference, fast=fast)
        started = time.perf_counter()
        osu = m.run_model(audio, out, a.difficulty, a.seed, args + extra)
        wall = time.perf_counter() - started
        chart = parse_osu_7k(Path(osu).read_text(encoding="utf-8", errors="replace"))
        print(json.dumps({"mode": name, "wall_s": round(wall, 1), "osu": str(osu), **describe(chart, reference)}),
              flush=True)


if __name__ == "__main__":
    main()
