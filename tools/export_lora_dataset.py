"""
Export the 7K charts of the osu!lazer library as a Mapperatorinator "mmrs" dataset, to fine-tune the model with a
LoRA (ADR-0048).

    PYTHONPATH=src python tools/export_lora_dataset.py --out ~/.cache/proj7k/lora/dataset [--catalog DB] \
        [--hold-out MD5 ...] [--min-notes 200]

Layout (what Mapperatorinator's `MmrsDataset` reads):

    DIR/data/<set>/audio.<ext>        the song, hard-linked from lazer's files (copied when it can't link)
    DIR/data/<set>/<md5>.osu          each 7K difficulty of the set, as lazer stores it
    DIR/metadata.jsonl                one row per difficulty; `tools/train_lora.py prepare` turns it into
                                      metadata.parquet with the model's own Python (pandas)
    DIR/summary.json                  counts, the train / test split, star and long-note spread

Left out: charts proj7k or Mapperatorinator wrote (generated, practice and downscaled maps), charts under
`--min-notes`, and `--hold-out` charts (the ones the generator is measured on). The difficulty each chart is trained
under is osu!'s official star rating, as the model was: the catalog's (by checksum) when it has one, else lazer's
own from before proj7k wrote its stars into the library (the backup), else lazer's current one.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from proj7k.lazer.backup import DEFAULT_BACKUP_STATE_FILE
from proj7k.lazer.bridge import DEFAULT_REALM_PATH, RealmBridgeClient
from proj7k.parser import NoteType, parse_osu_7k

#: Text in a chart's tags, creator or difficulty name that marks it as written by proj7k or the model.
GENERATED = ("proj7k", "mapperatorinator", "practice")
#: lazer's BeatmapOnlineStatus as osu!'s ranked status (-2 graveyard … 4 loved); locally modified and unknown -> wip.
STATUS = {-4: -1, -3: -1, -2: -2, -1: -1, 0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
CATALOG_STATUS = {"graveyard": -2, "wip": -1, "pending": 0, "ranked": 1, "approved": 2, "qualified": 3, "loved": 4}
#: The speeds Mapperatorinator's metadata carries a star rating for; mania's grows about in step with the rate.
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
TEST_SHARE = 0.05


def stored(files_dir: Path, h: str) -> Path:
    return files_dir / h[:1] / h[:2] / h


def small_id(text: str) -> int:
    """A stable negative id for a chart or set osu! has not numbered."""
    return -(int(hashlib.md5(text.encode("utf-8")).hexdigest()[:12], 16) % (2 ** 31 - 1)) - 1


def catalog_rows(path):
    """checksum -> (official star rating, status, ranked date, mapper's user id) from the online catalog."""
    if not path or not Path(path).is_file():
        return {}
    # immutable: the live database runs in WAL mode and a plain read-only open fails with "disk I/O error"
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?immutable=1", uri=True)
    users = {name.casefold(): uid for uid, name in con.execute("SELECT id, username FROM users")}
    out = {}
    for checksum, sr, status, ranked, creator in con.execute(
            "SELECT b.checksum, b.official_sr, s.status, s.ranked_date, s.creator "
            "FROM beatmaps b JOIN beatmapsets s ON s.id = b.beatmapset_id"):
        out[checksum] = (sr, CATALOG_STATUS.get(status), ranked, users.get((creator or "").casefold(), -1))
    con.close()
    return out


def original_stars(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("beatmaps", data) if isinstance(data, dict) else {}
    out = {}
    for key, row in (rows.items() if isinstance(rows, dict) else []):
        if isinstance(row, dict) and float(row.get("original_star_rating", -1)) > 0:
            out[str(row.get("id", key))] = float(row["original_star_rating"])
    return out


def link(src: Path, dst: Path):
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def export(args):
    realm = Path(args.realm).expanduser() if args.realm else DEFAULT_REALM_PATH
    files_dir = Path(args.files_dir).expanduser() if args.files_dir else realm.parent / "files"
    out = Path(args.out).expanduser()
    (out / "data").mkdir(parents=True, exist_ok=True)
    library = RealmBridgeClient(default_realm_path=realm).dump_player_data(realm_path=realm)["beatmaps"]
    catalog = catalog_rows(args.catalog)
    backup = original_stars(Path(args.backup).expanduser())
    hold_out = {h.lower() for h in args.hold_out}
    skipped = Counter()
    rows, seen, set_audio = [], set(), {}
    for b in library:
        md5 = (b.get("md5_hash") or "").lower()
        text_fields = " ".join(str(b.get(k, "")) for k in ("tags", "creator", "difficulty_name")).lower()
        if not md5 or md5 in seen:
            skipped["duplicate"] += 1
            continue
        if md5 in hold_out:
            skipped["held out"] += 1
            continue
        if any(g in text_fields for g in GENERATED):
            skipped["generated"] += 1
            continue
        osu, audio = stored(files_dir, b.get("file_hash") or ""), stored(files_dir, b.get("audio_hash") or "")
        if not b.get("file_hash") or not b.get("audio_hash") or not osu.is_file() or not audio.is_file():
            skipped["missing file"] += 1
            continue
        text = osu.read_text(encoding="utf-8", errors="replace")
        try:
            chart = parse_osu_7k(text)
        except Exception:
            skipped["unreadable"] += 1
            continue
        if any(g in f"{chart.tags} {chart.creator} {chart.version}".lower() for g in GENERATED):
            skipped["generated"] += 1
            continue
        if len(chart.hit_objects) < args.min_notes:
            skipped["too few notes"] += 1
            continue
        cat = catalog.get(md5)
        sr = (cat[0] if cat and cat[0] else None) or backup.get(str(b.get("id"))) or float(b.get("star_rating") or 0)
        if sr <= 0:
            skipped["no star rating"] += 1
            continue
        seen.add(md5)
        set_online = int(b.get("set_online_id") or -1)
        set_id = set_online if set_online > 0 else small_id(b["audio_hash"])
        # Mapperatorinator loads one audio per set: a difficulty on other audio than the set's first (packs, cuts)
        # becomes a set of its own
        if set_audio.setdefault(set_id, b["audio_hash"]) != b["audio_hash"]:
            set_id = small_id(f"{set_id}/{b['audio_hash']}")
            set_audio.setdefault(set_id, b["audio_hash"])
        folder = str(set_id)
        suffix = Path(chart.audio_filename or "audio.mp3").suffix.lower() or ".mp3"
        (out / "data" / folder).mkdir(exist_ok=True)
        link(audio, out / "data" / folder / f"audio{suffix}")
        (out / "data" / folder / f"{md5}.osu").write_text(text, encoding="utf-8")
        online = int(b.get("online_id") or -1)
        date = (cat[2] if cat and cat[2] else None) or b.get("date_added") or "2024-01-01"
        ln = sum(o.note_type == NoteType.LN for o in chart.hit_objects) / len(chart.hit_objects)
        rows.append({
            "BeatmapSetId": set_id, "Id": online if online > 0 else small_id(md5),
            "BeatmapSetFolder": folder, "AudioFile": f"audio{suffix}", "BeatmapFile": f"{md5}.osu",
            "ModeInt": 3, "Ranked": (cat[1] if cat and cat[1] is not None else STATUS.get(int(b.get("status") or 0), -1)),
            "SubmittedDate": str(date)[:19], "RankedDate": str(date)[:19],
            "StarRating": [round(sr * s, 4) for s in SPEEDS], "DifficultyRating": round(sr, 4),
            "UserId": int(cat[3]) if cat else -1, "Md5": md5, "LnShare": round(ln, 3),
        })
    with (out / "metadata.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    sets = sorted({r["BeatmapSetId"] for r in rows})
    n_test = max(1, round(len(sets) * TEST_SHARE)) if len(sets) > 1 else 0
    bands = Counter(min(int(r["DifficultyRating"]), 10) for r in rows)
    summary = {
        "charts": len(rows), "sets": len(sets), "train_sets": len(sets) - n_test, "test_sets": n_test,
        "skipped": dict(skipped),
        "stars": {f"{k}-{k + 1}" if k < 10 else "10+": bands[k] for k in sorted(bands)},
        "ln_heavy": sum(r["LnShare"] >= 0.5 for r in rows), "rice": sum(r["LnShare"] < 0.1 for r in rows),
        "status": dict(Counter(r["Ranked"] for r in rows)),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--realm", help=f"osu!lazer client.realm (default {DEFAULT_REALM_PATH})")
    p.add_argument("--files-dir", help="lazer's files folder (default: next to the realm)")
    p.add_argument("--catalog", default=str(Path.home() / ".cache" / "proj7k" / "catalog.sqlite3"),
                   help="the online catalog's database, for official star ratings and mappers (optional)")
    p.add_argument("--backup", default=str(DEFAULT_BACKUP_STATE_FILE),
                   help="proj7k's lazer backup, for the stars lazer had before proj7k wrote its own")
    p.add_argument("--hold-out", nargs="*", default=[], metavar="MD5", help="charts kept out of the dataset")
    p.add_argument("--min-notes", type=int, default=200)
    export(p.parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
