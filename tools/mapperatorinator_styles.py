"""
Do Mapperatorinator's style conditions move a 7K chart? (ADR-0047, experiment before any slider is built.)

    # what the model knows: its descriptor tags and how many mappers, read from the downloaded tokenizer
    PYTHONPATH=src python tools/mapperatorinator_styles.py vocab --out DIR

    # each condition on each song and seed, one raw model run each (no reuse, no trim), one JSON line per run
    PYTHONPATH=src python tools/mapperatorinator_styles.py run --chart MD5_OR_OSU --chart ... --out DIR

    # per condition, how far each engine pool's stars and the key statistics move from the plain run
    PYTHONPATH=src python tools/mapperatorinator_styles.py summary --out DIR

Every run of a song borrows its reference chart's timing (online id stripped, as the generator does), asks for
the reference's engine stars as the difficulty, and leaves the long-note share to the model so the LN tags can
act. A condition is a list of descriptors and a cfg_scale; "plain" is none at cfg 1, "plain-cfg" none at the
condition's cfg. `run` appends to DIR/runs.jsonl and skips runs already there, so it can be stopped and resumed.
"""

import argparse
import copy
import hashlib
import json
import statistics
import time
from pathlib import Path

from proj7k.engine import SKILLS, evaluate_notes
from proj7k.generator import mapperatorinator as m
from proj7k.generator.chartstats import pattern_stats, rows_of_chart
from proj7k.generator.cli import resolve_reference
from proj7k.parser import NoteType, dominant_timing_point, dump_osu_7k, parse_osu_7k

#: The mania tags the guess in ADR-0047 maps onto the engine's pools, and a few more that read as 7K styles.
TAGS = (
    "style/chordjack", "skillset/speedjack", "skillset/wristjack", "style/longjack",
    "style/jumpstream", "style/handstream", "style/quadstream", "style/chordstream",
    "skillset/tech", "tech/technical hybrid", "tap/stamina",
    "style/LN density", "style/LN coordination", "style/LN release", "gimmick/LN inverse",
)
CFG = 2.0
STAT_KEYS = ("roll", "trill", "jack_half", "rows_per_s", "notes_per_s", "quarter", "triplet")


def conditions(tags):
    yield "plain", [], 1.0
    yield "plain-cfg", [], CFG
    for t in tags:
        yield t, [t], CFG


def find_tokenizer(hub: Path):
    """The JSON files under the model's Hugging Face cache that carry its mapper and descriptor tables."""
    found = {}
    for p in hub.rglob("*.json"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if '"descriptor_idx"' in text or '"mapper_idx"' in text:
            found[p] = json.loads(text)
    return found


def hub_dir() -> Path:
    import os

    base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return base / "hub"


def vocab(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    tables = find_tokenizer(hub_dir())
    if not tables:
        raise SystemExit(f"no tokenizer tables under {hub_dir()}: run the generator once so the weights download")
    descriptors, mappers = {}, {}
    for path, data in tables.items():
        descriptors.update(data.get("descriptor_idx") or {})
        mappers.update({int(k): v for k, v in (data.get("mapper_idx") or {}).items()})
        print(f"{path}: {len(data.get('descriptor_idx') or {})} descriptors, {len(data.get('mapper_idx') or {})} mappers")
    (out / "vocab.json").write_text(json.dumps({"descriptors": sorted(descriptors), "mapper_ids": sorted(mappers)},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    missing = [t for t in TAGS if t not in descriptors]
    print(f"{len(descriptors)} descriptors, {len(mappers)} mappers -> {out / 'vocab.json'}")
    print("TAGS the model doesn't know:", missing or "none")


def measure(chart):
    notes = [(o.column, o.time / 1000.0, o.end_time / 1000.0 if o.note_type == NoteType.LN else None)
             for o in chart.hit_objects]
    prof = evaluate_notes(notes)
    tp = dominant_timing_point(chart)
    stats = pattern_stats(rows_of_chart(chart.hit_objects), tp.beat_length if tp else 0.0)
    return {
        "stars": round(float(prof.total_stars), 3),
        "skills": {k: round(float(v.stars), 3) for k, v in prof.skills.items()},
        "ln_share": round(sum(o.note_type == NoteType.LN for o in chart.hit_objects) / max(1, len(chart.hit_objects)), 3),
        **{k: round(stats[k], 3) for k in STAT_KEYS},
    }


def run(args):
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    log = out / "runs.jsonl"
    done = set()
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["song"], r["condition"], r["seed"]))
    known = None
    if (out / "vocab.json").is_file():
        known = set(json.loads((out / "vocab.json").read_text(encoding="utf-8"))["descriptors"])
    tags = [t for t in TAGS if known is None or t in known]
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
        difficulty = min(max(human["stars"], m.MIN_DIFFICULTY), m.MAX_DIFFICULTY)
        for name, descriptors, cfg in conditions(tags):
            for seed in range(args.seeds):
                if (song, name, seed) in done:
                    continue
                extra = m.overrides(audio, work / "out", difficulty, seed, timing_from=timing, fast=args.fast)
                extra.append(f"cfg_scale={cfg:g}")
                if descriptors:
                    extra.append("descriptors=[" + ",".join(m.quote(d) for d in descriptors) + "]")
                started = time.perf_counter()
                try:
                    osu = m.run_model(audio, work / "out", difficulty, seed, extra)
                    chart = parse_osu_7k(Path(osu).read_text(encoding="utf-8", errors="replace"))
                    chart.hit_objects, _ = m.legal.sanitize(chart.hit_objects, chart.timing_points)
                    row = {"song": song, "condition": name, "seed": seed, "cfg": cfg, "difficulty": difficulty,
                           "wall_s": round(time.perf_counter() - started, 1), "human": human, **measure(chart)}
                except Exception as e:  # one failed run must not stop the night
                    row = {"song": song, "condition": name, "seed": seed, "error": str(e)[-400:]}
                with log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(json.dumps({k: row.get(k) for k in ("song", "condition", "seed", "stars", "wall_s", "error")},
                                 ensure_ascii=False), flush=True)


def summary(args):
    rows = [json.loads(l) for l in (args.out / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if "error" not in r]
    base = {}
    for r in rows:
        if r["condition"] in ("plain", "plain-cfg"):
            base[(r["song"], r["condition"], r["seed"])] = r
    names = list(dict.fromkeys(r["condition"] for r in rows))
    skills = [k for k in SKILLS if any(k in r["skills"] for r in rows)]
    lines = ["| condition | n | total | " + " | ".join(skills) + " | LN | " + " | ".join(STAT_KEYS) + " |",
             "|---" * (3 + len(skills) + 1 + len(STAT_KEYS)) + "|"]
    for name in names:
        ref_name = "plain" if name == "plain-cfg" else ("plain" if name == "plain" else "plain-cfg")
        pairs = [(r, base.get((r["song"], ref_name, r["seed"]))) for r in rows if r["condition"] == name]
        pairs = [(r, b) for r, b in pairs if b is not None]
        if not pairs:
            continue

        def shift(get):
            return statistics.median(get(r) - get(b) for r, b in pairs)
        cells = [f"{shift(lambda x: x['stars']):+.2f}"]
        cells += [f"{shift(lambda x, k=k: x['skills'].get(k, 0.0)):+.2f}" for k in skills]
        cells.append(f"{shift(lambda x: x['ln_share']):+.2f}")
        cells += [f"{shift(lambda x, k=k: x[k]):+.2f}" for k in STAT_KEYS]
        lines.append(f"| {name} | {len(pairs)} | " + " | ".join(cells) + " |")
    text = ("Median shift of each run against the plain run of the same song and seed "
            "(plain-cfg against plain; every tag against plain-cfg).\n\n" + "\n".join(lines) + "\n")
    (args.out / "summary.md").write_text(text, encoding="utf-8")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("vocab")
    v.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--chart", action="append", required=True, help="reference chart: .osu, or MD5 / id in osu!lazer")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--seeds", type=int, default=2)
    r.add_argument("--fast", action="store_true", help="the generator's fast mode (batched chains)")
    s = sub.add_parser("summary")
    s.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    {"vocab": lambda: vocab(a.out), "run": lambda: run(a), "summary": lambda: summary(a)}[a.cmd]()


if __name__ == "__main__":
    main()
