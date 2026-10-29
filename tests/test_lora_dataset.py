"""
Tests for the LoRA dataset export (ADR-0048): 7K charts of the lazer library into Mapperatorinator's mmrs layout,
with generated and held-out charts left out and osu!'s official star rating from the catalog when it has one.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import export_lora_dataset as E  # noqa: E402
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, dump_osu_7k  # noqa: E402


def chart(version, creator="someone", tags="", n=300, ln=False):
    objs = [HitObject(column=i % 7, time=1000 + 100 * i, note_type=NoteType.LN if ln else NoteType.RICE,
                      end_time=1000 + 100 * i + 80 if ln else None) for i in range(n)]
    return Beatmap7K(title="T", artist="A", creator=creator, version=version, tags=tags, audio_filename="song.ogg",
                     timing_points=[TimingPoint(time=1000.0, beat_length=400.0, uninherited=True)], hit_objects=objs)


@pytest.fixture()
def library(tmp_path, monkeypatch):
    files = tmp_path / "files"
    entries = []

    def store(data: bytes) -> str:
        h = hashlib.sha256(data).hexdigest()
        p = E.stored(files, h)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return h

    audio = store(b"audio bytes")
    for i, (bm, set_id, online) in enumerate([
        (chart("Hard"), 100, 1001),
        (chart("FLN", ln=True), 100, 1002),
        (chart("Gen", creator="proj7k"), -1, -1),
        (chart("Mapper", tags="proj7k mapperatorinator-v32"), -1, -1),
        (chart("Short", n=50), 100, 1003),
        (chart("Local"), -1, -1),
        (chart("Held"), 200, 2001),
    ]):
        text = dump_osu_7k(bm).encode("utf-8")
        md5 = hashlib.md5(text).hexdigest()
        entries.append({"id": f"uuid-{i}", "md5_hash": md5, "file_hash": store(text), "audio_hash": audio,
                        "online_id": online, "set_online_id": set_id, "star_rating": 3.0 + i, "status": 1,
                        "difficulty_name": bm.version, "creator": bm.creator, "tags": bm.tags,
                        "date_added": "2025-02-03T04:05:06Z"})
    monkeypatch.setattr(E.RealmBridgeClient, "dump_player_data", lambda self, realm_path=None: {"beatmaps": entries})
    realm = tmp_path / "client.realm"
    realm.write_bytes(b"")
    return tmp_path, entries


def test_export_writes_the_mmrs_layout_and_leaves_out_what_it_should(library, tmp_path):
    root, entries = library
    db = root / "catalog.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE users (id INTEGER, username TEXT, updated_at TEXT);"
                      "CREATE TABLE beatmapsets (id INTEGER, status TEXT, ranked_date TEXT, creator TEXT);"
                      "CREATE TABLE beatmaps (id INTEGER, beatmapset_id INTEGER, checksum TEXT, official_sr REAL);"
                      "INSERT INTO users VALUES (77, 'Someone', '');"
                      "INSERT INTO beatmapsets VALUES (100, 'loved', '2021-05-06 07:08:09', 'someone');")
    con.execute("INSERT INTO beatmaps VALUES (1001, 100, ?, 4.25)", (entries[0]["md5_hash"],))
    con.commit()
    con.close()
    out = tmp_path / "ds"
    summary = E.export(E.argparse.Namespace(
        out=str(out), realm=str(root / "client.realm"), files_dir=str(root / "files"), catalog=str(db),
        backup=str(root / "none.json"), hold_out=[entries[6]["md5_hash"]], min_notes=200))
    rows = [json.loads(l) for l in (out / "metadata.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["BeatmapFile"][:-4] for r in rows] == [entries[i]["md5_hash"] for i in (0, 1, 5)]
    assert summary["skipped"] == {"generated": 2, "too few notes": 1, "held out": 1}
    hard, fln, local = rows
    assert hard["DifficultyRating"] == 4.25 and hard["Ranked"] == 4 and hard["UserId"] == 77
    assert hard["StarRating"][2] == 4.25 and hard["SubmittedDate"].startswith("2021-05-06")
    assert fln["DifficultyRating"] == 4.0 and fln["LnShare"] == 1.0 and fln["UserId"] == -1
    assert local["BeatmapSetId"] < 0 and local["Id"] < 0 and local["Ranked"] == 1
    for r in rows:
        folder = out / "data" / r["BeatmapSetFolder"]
        assert (folder / r["AudioFile"]).read_bytes() == b"audio bytes" and r["AudioFile"] == "audio.ogg"
        assert (folder / r["BeatmapFile"]).is_file()
    assert summary["sets"] == 2 and summary["train_sets"] + summary["test_sets"] == 2
