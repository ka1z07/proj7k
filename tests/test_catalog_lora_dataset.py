"""
Tests for picking long-note charts from the catalog and adding their downloaded sets to the LoRA dataset (ADR-0048).
"""

import hashlib
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import catalog_lora_dataset as C  # noqa: E402


def osu(version, audio="song.mp3"):
    return (f"osu file format v14\n\n[General]\nAudioFilename: {audio}\nMode: 3\n\n[Metadata]\nVersion:{version}\n\n"
            "[HitObjects]\n36,192,1000,128,0,1400:0:0:0:0:\n").encode("utf-8")


def test_pick_then_fetch_adds_the_picked_difficulties(tmp_path):
    fln, mixed, rice, other = osu("FLN"), osu("Mixed"), osu("Rice"), osu("Cut", audio="cut.mp3")
    md5 = {k: hashlib.md5(v).hexdigest() for k, v in (("fln", fln), ("mixed", mixed), ("rice", rice), ("other", other))}
    db = tmp_path / "catalog.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE beatmapsets (id INTEGER, status TEXT, ranked_date TEXT, submitted_date TEXT, updated_at TEXT,"
        " creator_id INTEGER);"
        "CREATE TABLE beatmaps (id INTEGER, beatmapset_id INTEGER, checksum TEXT, official_sr REAL, engine_sr REAL,"
        " note_count INTEGER, ln_count INTEGER, mapper_id INTEGER);"
        "INSERT INTO beatmapsets VALUES (10, 'loved', '2022-01-02', NULL, '2022', 5), (20, 'graveyard', NULL,"
        " '2021-03-04', '2021', 6), (30, 'ranked', '2020', NULL, '2020', 7);")
    con.executemany("INSERT INTO beatmaps VALUES (?,?,?,?,?,?,?,?)", [
        (101, 10, md5["fln"], 6.1, 9.0, 1000, 990, 55), (102, 10, md5["rice"], 5.0, 8.0, 1000, 0, 55),
        (103, 10, md5["other"], 6.0, 8.5, 800, 600, None), (201, 20, md5["mixed"], 4.0, 6.0, 900, 400, None),
        (301, 30, "already", 6.0, 9.5, 900, 900, 1)])
    con.commit()
    con.close()
    have = tmp_path / "have.jsonl"
    have.write_text(json.dumps({"Md5": "already", "BeatmapSetId": 30}) + "\n", encoding="utf-8")
    picks = tmp_path / "picks.json"
    C.main(["pick", "--catalog", str(db), "--exclude", str(have), "--out", str(picks), "--min-ln-share", "0.3"])
    chosen = json.loads(picks.read_text(encoding="utf-8"))["sets"]
    assert sorted(s["id"] for s in chosen) == [10, 20]
    assert {d["md5"] for s in chosen if s["id"] == 10 for d in s["diffs"]} == {md5["fln"], md5["other"]}
    # a held-out chart keeps its whole set out
    held = tmp_path / "held.json"
    C.main(["pick", "--catalog", str(db), "--exclude", str(have), "--out", str(held), "--hold-out", md5["rice"]])
    assert [s["id"] for s in json.loads(held.read_text(encoding="utf-8"))["sets"]] == [20]

    def fake(set_id, log):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            if set_id == 10:
                for name, data in (("a.osu", fln), ("b.osu", rice), ("c.osu", other), ("song.mp3", b"S"), ("cut.mp3", b"C")):
                    z.writestr(name, data)
            else:
                raise RuntimeError("mirror down")
        return buf.getvalue()

    ds = tmp_path / "ds"
    (ds / "data").mkdir(parents=True)
    (ds / "metadata.jsonl").write_text("", encoding="utf-8")
    summary = C.fetch(C.argparse.Namespace(picks=str(picks), dataset=str(ds), pause=0), fetcher=fake)
    rows = [json.loads(l) for l in (ds / "metadata.jsonl").read_text(encoding="utf-8").splitlines()]
    by = {r["Md5"]: r for r in rows}
    assert set(by) == {md5["fln"], md5["other"]}
    assert by[md5["fln"]]["BeatmapSetId"] == 10 and by[md5["fln"]]["UserId"] == 55 and by[md5["fln"]]["Ranked"] == 4
    assert by[md5["fln"]]["DifficultyRating"] == 6.1 and by[md5["fln"]]["Id"] == 101
    assert (ds / "data" / "10" / "audio.mp3").read_bytes() == b"S"
    other_row = by[md5["other"]]
    assert other_row["BeatmapSetId"] != 10 and (ds / "data" / other_row["BeatmapSetFolder"] / "audio.mp3").read_bytes() == b"C"
    assert summary["catalog"] == 2 and json.loads((ds / "fetch_failed.json").read_text())[0]["id"] == 20
    # resumes: nothing is downloaded twice
    calls = []
    C.fetch(C.argparse.Namespace(picks=str(picks), dataset=str(ds), pause=0),
            fetcher=lambda i, log: calls.append(i) or fake(i, log))
    assert calls == [20]


def test_pick_spreads_the_sets_over_the_star_bands(tmp_path):
    db = tmp_path / "c.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE beatmapsets (id INTEGER, status TEXT, ranked_date TEXT, submitted_date TEXT,"
                      " updated_at TEXT, creator_id INTEGER); CREATE TABLE beatmaps (id INTEGER, beatmapset_id INTEGER,"
                      " checksum TEXT, official_sr REAL, engine_sr REAL, note_count INTEGER, ln_count INTEGER,"
                      " mapper_id INTEGER);")
    stars = [11.5, 11.2, 11.1, 10.9, 6.5, 6.2]
    for i, e in enumerate(stars, 1):
        con.execute("INSERT INTO beatmapsets VALUES (?, 'graveyard', NULL, NULL, '2020', 1)", (i,))
        con.execute("INSERT INTO beatmaps VALUES (?, ?, ?, 5.0, ?, 900, 900, 1)", (i * 10, i, f"m{i}", e))
    con.commit()
    con.close()
    out = tmp_path / "p.json"
    C.main(["pick", "--catalog", str(db), "--out", str(out), "--sets", "3"])
    assert sorted(s["best"] for s in json.loads(out.read_text(encoding="utf-8"))["sets"]) == [6.5, 10.9, 11.5]
