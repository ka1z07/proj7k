"""Mappers and their writing style (ADR-0026, mapper pages)."""

import pytest

from proj7k.catalog.charts import chart_row, pattern_features
from proj7k.catalog.mappers import MapperIndex, mapper_key
from proj7k.catalog.store import CatalogStore
from proj7k.parser import parse_osu_7k


def _osu(rows, ln=False):
    """A tiny 7K chart: `rows` is a list of column tuples, one row every 100 ms."""
    objs = []
    for i, cols in enumerate(rows):
        t = 1000 + 100 * i
        for c in cols:
            x = int((c + 0.5) * 512 / 7)
            objs.append(f"{x},192,{t},128,0,{t + 80}:0:0:0:0:" if ln else f"{x},192,{t},1,0,0:0:0:0:")
    return ("osu file format v14\n\n[General]\nMode: 3\n\n[Metadata]\nTitle:t\nArtist:a\nCreator:c\nVersion:v\n\n"
            "[Difficulty]\nCircleSize:7\nOverallDifficulty:8\n\n[TimingPoints]\n0,500,4,1,0,100,1,0\n\n[HitObjects]\n"
            + "\n".join(objs) + "\n")


def test_pattern_features_measure_chords_jacks_and_columns():
    p = pattern_features(parse_osu_7k(_osu([(0, 6), (0,), (3,), (3,)])))
    assert p["chord"] == pytest.approx(5 / 4)
    assert p["jack"] == pytest.approx(2 / 5)       # column 0 then column 3 repeat on the next row
    assert sum(p["columns"]) == pytest.approx(1.0)
    assert p["columns"][0] == pytest.approx(2 / 5) and p["columns"][3] == pytest.approx(2 / 5)
    assert p["nps"] > 0


@pytest.fixture(scope="module")
def store(benchmark_corpus):
    s = CatalogStore()
    ids = sorted(benchmark_corpus)[:24]
    for n, beatmap_id in enumerate(ids):
        # Three mappers: 10 + 10 difficulties with known ids, 4 under a name only.
        if n < 20:
            s.upsert(chart_row(benchmark_corpus[beatmap_id], 4.0, "manifest", mapper_id=100 + n % 2))
        else:
            s.upsert(chart_row(benchmark_corpus[beatmap_id], None, None, mapper_name="Local Person"))
    s.set_users({100: "Alpha", 101: "Beta"})
    yield s
    s.close()


def test_profiles_pool_each_mappers_difficulties(store):
    idx = MapperIndex(store)
    listing = idx.listing(min_diffs=1)
    by_name = {m["name"]: m for m in listing["mappers"]}
    assert by_name["Alpha"]["diffs"] == 10 and by_name["Beta"]["diffs"] == 10
    assert by_name["Local Person"]["diffs"] == 4 and by_name["Local Person"]["user_id"] is None
    assert by_name["Alpha"]["engine_median"] > 0 and 0 <= by_name["Alpha"]["ln_ratio"] <= 1
    assert idx.listing(min_diffs=5)["total"] == 2
    assert [m["name"] for m in idx.listing(q="alp")["mappers"]] == ["Alpha"]


def test_mapper_detail_has_style_analysis(store):
    idx = MapperIndex(store)
    d = idx.get("100")
    assert d["styled"] and d["diffs"] == 10
    assert set(d["skill_mix"]) == {"rc_jack", "rc_tech", "rc_speed", "rc_stamina",
                                   "ln_general", "ln_tech", "ln_inverse", "ln_release"}
    assert sum(d["skill_mix"].values()) == pytest.approx(1.0)
    assert sum(d["dan_hist"].values()) == 10
    assert len(d["columns"]) == 7 and sum(d["columns"]) == pytest.approx(1.0, abs=1e-3)
    assert {"ln_ratio", "chord", "jack", "nps", "mid", "hand", "bpm", "length", "engine", "delta"} <= d["features"].keys()
    assert {s["key"] for s in d["similar"]} == {"@Local Person", "101"}
    for tag in d["tags"]:
        assert tag["label"] and tag["why"]
    assert idx.get(mapper_key(None, "Local Person"))["user_id"] is None
    assert idx.get("999") is None


def test_profiles_rebuild_when_the_catalog_changes(store):
    idx = MapperIndex(store)
    before = idx.get("101")["diffs"]
    some = next(b["id"] for s in store.search("mapperid=100").beatmapsets for b in s["beatmaps"] if b["mapper_id"] == 100)
    store.set_mapper(some, 101)
    assert idx.get("101")["diffs"] == before + 1
    store.set_mapper(some, 100)


def test_search_by_mapper(store):
    assert store.search("mapperid=100").total >= 1
    assert all(any(b["matched"] and b["mapper_name"] == "Beta" for b in s["beatmaps"])
               for s in store.search("mapper=beta").beatmapsets)


def test_refresh_fills_pattern_features_of_old_rows(store):
    with store._lock, store._db:
        store._db.execute("UPDATE beatmaps SET pattern_json = NULL WHERE mapper_id = 101")
    assert len(store.stale()) == 10
    assert store.reevaluate_stale() == 10
    assert store.stale() == []
    names = {b["mapper_name"] for s in store.search("mapperid=101").beatmapsets for b in s["beatmaps"] if b["mapper_id"] == 101}
    assert names == {"Beta"}   # re-evaluation keeps who wrote it


def test_old_database_is_migrated_in_place(tmp_path):
    import sqlite3
    db = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE beatmapsets (id INTEGER PRIMARY KEY, title TEXT NOT NULL, title_unicode TEXT NOT NULL, "
                      "artist TEXT NOT NULL, artist_unicode TEXT NOT NULL, creator TEXT NOT NULL, source TEXT NOT NULL, "
                      "tags TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown', ranked_date TEXT, updated_at TEXT NOT NULL, "
                      "search_text TEXT NOT NULL, title_search TEXT NOT NULL, artist_search TEXT NOT NULL, "
                      "creator_search TEXT NOT NULL, source_search TEXT NOT NULL, tags_search TEXT NOT NULL);")
    con.close()
    s = CatalogStore(db)
    cols = {r[1] for r in s._db.execute("PRAGMA table_info(beatmapsets)")}
    assert {"creator_id", "submitted_date"} <= cols
    s.close()
