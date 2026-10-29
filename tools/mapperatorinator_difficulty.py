"""
Why does Mapperatorinator write high-star long-note charts far too easy? (ADR-0047 phase 2: asked for 9.55★ on
Exterminate [FLN], it wrote 2.1–3.6★ at 0.9–0.97 long notes.)

    # each difficulty × long-note share × seed on each reference's timing, one raw model run each
    PYTHONPATH=src python tools/mapperatorinator_difficulty.py run --chart MD5_OR_OSU ... --out DIR \
        --difficulty 4 6 8 10 12 --hold none 1.0 --seeds 1 [--fast]

    # one row per run next to the reference: stars, pools, density and how the holds are written
    PYTHONPATH=src python tools/mapperatorinator_difficulty.py summary --out DIR

Every run borrows the reference's timing (online id stripped) as the generator does. Each run is measured raw and
after `legal.sanitize`, so a loss in our own clean-up shows up apart from what the model wrote. `run` appends to
DIR/runs.jsonl and skips runs already there.
"""

import argparse
import copy
import hashlib
import json
import time
from pathlib import Path

from proj7k.engine import evaluate_notes
from proj7k.generator import mapperatorinator as m
from proj7k.generator.chartstats import pattern_stats, rows_of_chart
from proj7k.generator.cli import resolve_reference
from proj7k.parser import NoteType, dominant_timing_point, dump_osu_7k, parse_osu_7k

POOLS = ("rc_jack", "rc_tech", "rc_speed", "rc_stamina", "ln_general", "ln_tech", "ln_inverse", "ln_release")


def holds(chart):
    """How the long notes are written: mean length in beats, columns held on average while the chart plays,
    releases per second."""
    tp = dominant_timing_point(chart)
    beat = tp.beat_length if tp else 500.0
    lns = [o for o in chart.hit_objects if o.note_type == NoteType.LN]
    if not chart.hit_objects:
        return {"hold_beats": 0.0, "held_cols": 0.0, "releases_per_s": 0.0}
    t0 = min(o.time for o in chart.hit_objects)
    t1 = max((o.end_time if o.note_type == NoteType.LN else o.time) for o in chart.hit_objects)
    span = max(1.0, t1 - t0)
    held = sum(o.end_time - o.time for o in lns)
    return {
        "hold_beats": round(held / len(lns) / beat, 2) if lns else 0.0,
        "held_cols": round(held / span, 2),
        "releases_per_s": round(len(lns) / (span / 1000.0), 2),
    }


def measure(chart):
    notes = [(o.column, o.time / 1000.0, o.end_time / 1000.0 if o.note_type == NoteType.LN else None)
             for o in chart.hit_objects]
    prof = evaluate_notes(notes)
    tp = dominant_timing_point(chart)
    stats = pattern_stats(rows_of_chart(chart.hit_objects), tp.beat_length if tp else 0.0)
    return {
        "stars": round(float(prof.total_stars), 2),
        "skills": {k: round(float(v.stars), 2) for k, v in prof.skills.items()},
        "notes": len(chart.hit_objects),
        "ln_share": round(sum(o.note_type == NoteType.LN for o in chart.hit_objects) / max(1, len(chart.hit_objects)), 3),
        "notes_per_s": round(stats["notes_per_s"], 2),
        "rows_per_s": round(stats["rows_per_s"], 2),
        **holds(chart),
    }


def run(args):
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    log = out / "runs.jsonl"
    done = set()
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["song"], r["difficulty"], r["hold"], r["seed"]))
    m.install(print)
    for spec in args.chart:
        ref = resolve_reference(spec)
        song = f"{ref.beatmap.artist} - {ref.beatmap.title} [{ref.beatmap.version}]"
        work = out / "work" / hashlib.md5(spec.encode("utf-8")).hexdigest()[:10]
        work.mkdir(parents=True, exist_ok=True)
        anonymous = copy.deepcopy(ref.beatmap)
        anonymous.extra_sections["Metadata"] = {**anonymous.extra_sections.get("Metadata", {}),
                                                "BeatmapID": "0", "BeatmapSetID": "-1"}
        timing = work / "reference.osu"
        timing.write_text(dump_osu_7k(anonymous), encoding="utf-8")
        audio = m.model_audio(Path(ref.audio_path), work, ref.beatmap.audio_filename or "")
        human = measure(ref.beatmap)
        print(json.dumps({"song": song, "human": human}, ensure_ascii=False), flush=True)
        for difficulty in args.difficulty:
            for hold in args.hold:
                ratio = None if hold == "none" else float(hold)
                for seed in range(args.seeds):
                    if (song, difficulty, hold, seed) in done:
                        continue
                    extra = m.overrides(audio, work / "out", difficulty, seed, hold_note_ratio=ratio,
                                        timing_from=timing, fast=args.fast)
                    started = time.perf_counter()
                    try:
                        osu = m.run_model(audio, work / "out", difficulty, seed, extra)
                        chart = parse_osu_7k(Path(osu).read_text(encoding="utf-8", errors="replace"))
                        raw = measure(chart)
                        chart.hit_objects, fixed = m.legal.sanitize(chart.hit_objects, chart.timing_points)
                        row = {"song": song, "difficulty": difficulty, "hold": hold, "seed": seed,
                               "wall_s": round(time.perf_counter() - started, 1), "human": human, "raw": raw,
                               "fixed": fixed, **measure(chart)}
                    except Exception as e:  # one failed run must not stop the rest
                        row = {"song": song, "difficulty": difficulty, "hold": hold, "seed": seed,
                               "error": str(e)[-400:]}
                    with log.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    print(json.dumps({k: row.get(k) for k in ("song", "difficulty", "hold", "seed", "stars",
                                                              "notes_per_s", "hold_beats", "fixed", "error")},
                                     ensure_ascii=False), flush=True)


def summary(args):
    rows = [json.loads(l) for l in (args.out / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    cols = ("stars", "notes", "ln_share", "notes_per_s", "rows_per_s", "hold_beats", "held_cols", "releases_per_s")
    head = "| run | " + " | ".join(cols) + " | raw stars | fixed | " + " | ".join(POOLS) + " |"
    lines = []
    for song in dict.fromkeys(r["song"] for r in rows):
        mine = [r for r in rows if r["song"] == song]
        human = next((r["human"] for r in mine if "human" in r), None)
        lines += [f"\n### {song}\n", head, "|---" * (len(cols) + 3 + len(POOLS)) + "|"]

        def cells(x):
            return [str(x.get(c, "")) for c in cols]

        if human:
            lines.append("| human | " + " | ".join(cells(human)) + " | | | "
                         + " | ".join(str(human["skills"].get(p, "")) for p in POOLS) + " |")
        for r in mine:
            name = f"d{r['difficulty']:g} h{r['hold']} s{r['seed']}"
            if "error" in r:
                lines.append(f"| {name} | error: {r['error'][-120:]} |")
                continue
            lines.append(f"| {name} | " + " | ".join(cells(r)) + f" | {r['raw']['stars']} | {r['fixed']} | "
                         + " | ".join(str(r["skills"].get(p, "")) for p in POOLS) + " |")
    text = "\n".join(lines) + "\n"
    (args.out / "summary.md").write_text(text, encoding="utf-8")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--chart", action="append", required=True, help="reference chart: .osu, or MD5 / id in osu!lazer")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--difficulty", type=float, nargs="+", default=[4.0, 6.0, 8.0, 10.0, 12.0])
    r.add_argument("--hold", nargs="+", default=["none", "1.0"], help="long-note share asked for, or none")
    r.add_argument("--seeds", type=int, default=1)
    r.add_argument("--fast", action="store_true", help="the generator's fast mode (batched chains)")
    s = sub.add_parser("summary")
    s.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    {"run": lambda: run(a), "summary": lambda: summary(a)}[a.cmd]()


if __name__ == "__main__":
    main()
