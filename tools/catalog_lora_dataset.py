"""
More long-note charts for the LoRA (ADR-0048): pick them from the online catalog, then download their sets from a
beatmap mirror into the dataset tools/export_lora_dataset.py wrote. The library has 188 long-note charts; the
catalog knows ~80,000 7K difficulties but holds no audio.

    # where the catalog lives (meow): the sets to fetch, with the difficulties wanted from each
    PYTHONPATH=src python tools/catalog_lora_dataset.py pick --catalog ~/.cache/proj7k/catalog.sqlite3 \
        --exclude DATASET/metadata.jsonl --hold-out MD5 ... --out picks.json [--min-ln-share 0.3 --min-engine 5]
    # on the GPU machine: download, keep the picked difficulties and the audio, add them to DATASET
    PYTHONPATH=src python tools/catalog_lora_dataset.py fetch --picks picks.json --dataset DATASET

`fetch` appends to DATASET/metadata.jsonl (osu!'s official star rating as the difficulty, the difficulty's own
mapper as the style) and rewrites DATASET/summary.json; `tools/train_lora.py subset` / `prepare` then work as
before. It resumes: sets already in DATASET are skipped. It asks a mirror for one set at a time.
"""

import argparse
import hashlib
import io
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

#: Mirrors that serve a set's .osz by its id, without the video; tried in order.
MIRRORS = (
    "https://catboy.best/d/{id}n",
    "https://api.nerinyan.moe/d/{id}?nv=1",
    "https://osu.direct/api/d/{id}?noVideo=1",
)
CATALOG_STATUS = {"graveyard": -2, "wip": -1, "pending": 0, "ranked": 1, "approved": 2, "qualified": 3, "loved": 4}
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
TEST_SHARE = 0.05
#: Seconds between two downloads, to be a polite client.
PAUSE = 1.0


def pick(args):
    exclude = set(h.lower() for h in args.hold_out)
    sets_have = set()
    if args.exclude and Path(args.exclude).is_file():
        for line in Path(args.exclude).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            exclude.add(row["Md5"].lower())
            sets_have.add(int(row["BeatmapSetId"]))
    con = sqlite3.connect(f"file:{Path(args.catalog).expanduser().as_posix()}?immutable=1", uri=True)
    rows = con.execute(
        "SELECT b.beatmapset_id, b.id, b.checksum, b.official_sr, b.engine_sr, b.note_count, b.ln_count, "
        "COALESCE(b.mapper_id, s.creator_id, -1), s.status, COALESCE(s.ranked_date, s.submitted_date, s.updated_at) "
        "FROM beatmaps b JOIN beatmapsets s ON s.id = b.beatmapset_id "
        "WHERE b.beatmapset_id > 0 AND b.official_sr IS NOT NULL AND b.note_count >= ? AND b.engine_sr >= ? "
        "AND b.ln_count >= ? * b.note_count",
        (args.min_notes, args.min_engine, args.min_ln_share)).fetchall()
    con.close()
    sets = {}
    for set_id, bid, md5, sr, engine, notes, lns, mapper, status, date in rows:
        if md5.lower() in exclude or set_id in sets_have:
            continue
        entry = sets.setdefault(set_id, {"id": set_id, "status": CATALOG_STATUS.get(status, -1),
                                         "date": (date or "2024-01-01")[:19], "best": 0.0, "diffs": []})
        entry["diffs"].append({"id": bid, "md5": md5.lower(), "sr": sr, "engine": engine, "mapper": int(mapper or -1),
                               "ln_share": round(lns / notes, 3)})
        entry["best"] = max(entry["best"], engine)
    chosen = sorted(sets.values(), key=lambda s: -s["best"])[:args.sets]
    out = {"criteria": {k: getattr(args, k) for k in ("min_ln_share", "min_engine", "min_notes", "sets")},
           "sets": chosen}
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    diffs = sum(len(s["diffs"]) for s in chosen)
    bands = Counter(min(int(d["engine"]), 12) for s in chosen for d in s["diffs"])
    print(json.dumps({"sets": len(chosen), "diffs": diffs, "of_sets": len(sets),
                      "engine_stars": {str(k): bands[k] for k in sorted(bands)}}))


def download(set_id: int, log) -> bytes:
    last = None
    for url in MIRRORS:
        req = urllib.request.Request(url.format(id=set_id), headers={"User-Agent": "proj7k-lora/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if data[:2] == b"PK":
                return data
            last = f"{url}: not a zip"
        except (urllib.error.URLError, OSError) as e:
            last = f"{url}: {e}"
    raise RuntimeError(last)


def add_set(entry, data: bytes, dataset: Path):
    """The picked difficulties of one .osz and their audio, as dataset rows (none if the files don't match)."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    wanted = {d["md5"]: d for d in entry["diffs"]}
    found = {}
    for name in zf.namelist():
        if name.lower().endswith(".osu"):
            raw = zf.read(name)
            md5 = hashlib.md5(raw).hexdigest()
            if md5 in wanted:
                found[md5] = raw
    rows = []
    folder = dataset / "data" / str(entry["id"])
    audio_names = {}
    for md5, raw in found.items():
        text = raw.decode("utf-8", errors="replace")
        audio = next((line.split(":", 1)[1].strip() for line in text.splitlines()
                      if line.startswith("AudioFilename:")), "")
        member = next((n for n in zf.namelist() if n.lower() == audio.lower()), None)
        if not member:
            continue
        suffix = Path(audio).suffix.lower() or ".mp3"
        # one audio per set folder (Mapperatorinator reads one); a difficulty on other audio gets its own folder
        key = audio_names.setdefault(member, len(audio_names))
        sub = folder if key == 0 else dataset / "data" / f"{entry['id']}-{key}"
        sub.mkdir(parents=True, exist_ok=True)
        if not (sub / f"audio{suffix}").exists():
            (sub / f"audio{suffix}").write_bytes(zf.read(member))
        (sub / f"{md5}.osu").write_bytes(raw)
        d = wanted[md5]
        set_id = entry["id"] if key == 0 else -(entry["id"] * 10 + key)
        rows.append({
            "BeatmapSetId": set_id, "Id": int(d["id"]),
            "BeatmapSetFolder": sub.name, "AudioFile": f"audio{suffix}", "BeatmapFile": f"{md5}.osu",
            "ModeInt": 3, "Ranked": entry["status"], "SubmittedDate": entry["date"], "RankedDate": entry["date"],
            "StarRating": [round(d["sr"] * s, 4) for s in SPEEDS], "DifficultyRating": round(d["sr"], 4),
            "UserId": d["mapper"], "Md5": md5, "LnShare": d["ln_share"], "Source": "catalog",
        })
    return rows


def summarize(dataset: Path):
    rows = [json.loads(l) for l in (dataset / "metadata.jsonl").read_text(encoding="utf-8").splitlines()]
    sets = sorted({r["BeatmapSetId"] for r in rows})
    n_test = max(1, round(len(sets) * TEST_SHARE)) if len(sets) > 1 else 0
    path = dataset / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    summary.update({"charts": len(rows), "sets": len(sets), "train_sets": len(sets) - n_test, "test_sets": n_test,
                    "ln_heavy": sum(r["LnShare"] >= 0.5 for r in rows), "rice": sum(r["LnShare"] < 0.1 for r in rows),
                    "catalog": sum(r.get("Source") == "catalog" for r in rows)})
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def fetch(args, fetcher=download):
    dataset = Path(args.dataset).expanduser()
    picks = json.loads(Path(args.picks).read_text(encoding="utf-8"))["sets"]
    meta = dataset / "metadata.jsonl"
    have = {json.loads(l)["Md5"] for l in meta.read_text(encoding="utf-8").splitlines()} if meta.is_file() else set()
    failed = []
    added = 0
    for i, entry in enumerate(picks):
        if all(d["md5"] in have for d in entry["diffs"]):
            continue
        try:
            rows = add_set(entry, fetcher(entry["id"], print), dataset)
        except Exception as e:  # one set must not stop the rest
            failed.append({"id": entry["id"], "error": str(e)[-300:]})
            print(f"[{i + 1}/{len(picks)}] set {entry['id']}: {e}", flush=True)
            continue
        rows = [r for r in rows if r["Md5"] not in have]
        with meta.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        have.update(r["Md5"] for r in rows)
        added += len(rows)
        print(f"[{i + 1}/{len(picks)}] set {entry['id']}: {len(rows)}/{len(entry['diffs'])} difficulties", flush=True)
        if args.pause:
            time.sleep(args.pause)
    (dataset / "fetch_failed.json").write_text(json.dumps(failed, indent=1), encoding="utf-8")
    summary = summarize(dataset)
    print(json.dumps({"added": added, "failed": len(failed), **summary}))
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("pick")
    a.add_argument("--catalog", default=str(Path.home() / ".cache" / "proj7k" / "catalog.sqlite3"))
    a.add_argument("--exclude", help="a dataset's metadata.jsonl: its charts and sets are not picked again")
    a.add_argument("--hold-out", nargs="*", default=[], metavar="MD5")
    a.add_argument("--out", required=True)
    a.add_argument("--min-ln-share", type=float, default=0.3)
    a.add_argument("--min-engine", type=float, default=5.0, help="engine stars")
    a.add_argument("--min-notes", type=int, default=300)
    a.add_argument("--sets", type=int, default=1000, help="at most this many sets, hardest first")
    f = sub.add_parser("fetch")
    f.add_argument("--picks", required=True)
    f.add_argument("--dataset", required=True)
    f.add_argument("--pause", type=float, default=PAUSE)
    args = p.parse_args(argv)
    (pick if args.cmd == "pick" else fetch)(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
