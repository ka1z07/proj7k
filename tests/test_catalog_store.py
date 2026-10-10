"""The catalog database: rows, search semantics, and re-evaluation after an engine change (ADR-0026)."""

from dataclasses import replace

import pytest

from proj7k.catalog import store as store_mod
from proj7k.catalog.charts import chart_row
from proj7k.catalog.ingest import manifest_official_sr
from proj7k.catalog.store import CatalogStore

# Two charts from each of three pools (three beatmapsets).
IDS = ("3864745", "3864746", "3864809", "3864810", "3888155", "3888156")


@pytest.fixture(scope="module")
def rows(benchmark_corpus, benchmark_manifest_path):
    official = manifest_official_sr(benchmark_manifest_path)
    corpus = {str(k): v for k, v in benchmark_corpus.items()}
    ids = [i for i in IDS if i in corpus]
    assert len(ids) == len(IDS)
    return [chart_row(corpus[i], official[i], "manifest") for i in ids]


@pytest.fixture
def store(rows):
    s = CatalogStore()
    for r in rows:
        s.upsert(r)
    yield s
    s.close()


def test_chart_row_reads_osu_metadata_and_the_engine(rows):
    r = rows[0]
    assert r.beatmap_id == 3864745 and r.beatmapset_id > 0
    assert r.official_sr == pytest.approx(3.83649)
    assert r.engine_sr > 0 and r.dan and r.dominant_skill in r.skills
    assert len(r.density) == 100 and sum(r.density) == r.note_count
    assert r.bpm > 0 and r.length_s > 0


def test_chart_row_rejects_non_7k():
    with pytest.raises(ValueError):
        chart_row("osu file format v14\n\n[General]\nMode: 3\n\n[Difficulty]\nCircleSize:4\n\n[HitObjects]\n64,192,1000,1,0,0:0:0:0:\n")


def test_stats_and_listing_group_difficulties_into_sets(store, rows):
    stats = store.stats()
    assert stats["beatmaps"] == len(rows) and stats["with_official_sr"] == len(rows) and stats["stale"] == 0
    result = store.search()
    assert result.total == stats["beatmapsets"] == len({r.beatmapset_id for r in rows})
    assert sum(len(s["beatmaps"]) for s in result.beatmapsets) == len(rows)
    for s in result.beatmapsets:
        b = s["beatmaps"][0]
        assert {"official_sr", "engine_sr", "dan", "dominant_skill", "matched"} <= b.keys()


def test_a_set_is_listed_when_one_difficulty_matches_and_that_one_is_marked(store, rows):
    top = max(rows, key=lambda r: r.engine_sr)
    result = store.search(f"engine>={top.engine_sr - 1e-4:.6f}")
    assert result.total == 1
    (s,) = result.beatmapsets
    assert [b["id"] for b in s["beatmaps"] if b["matched"]] == [top.beatmap_id]
    assert len(s["beatmaps"]) > 1   # the set's other difficulties are still shown


def test_free_words_match_set_text_or_difficulty_name(store, rows):
    word = rows[0].version.split()[-1]
    assert store.search(word).total >= 1
    assert store.search("no-such-chart-anywhere").total == 0


def test_sorting_uses_the_matching_extreme_and_puts_missing_values_last(store, rows):
    desc = store.search(sort="engine_desc").beatmapsets
    tops = [max(b["engine_sr"] for b in s["beatmaps"]) for s in desc]
    assert tops == sorted(tops, reverse=True)
    asc = store.search(sort="engine_asc").beatmapsets
    lows = [min(b["engine_sr"] for b in s["beatmaps"]) for s in asc]
    assert lows == sorted(lows)

    # Sets without an official rating sort after the others, in either direction.
    s = CatalogStore()
    try:
        r0, r1 = rows[0], rows[2]
        s.upsert(r0)
        s.upsert(replace(r1, official_sr=None, official_sr_source=None))
        order = [x["id"] for x in s.search(sort="official_desc").beatmapsets]
        assert order == [r0.beatmapset_id, r1.beatmapset_id]
        order = [x["id"] for x in s.search(sort="official_asc").beatmapsets]
        assert order == [r0.beatmapset_id, r1.beatmapset_id]
    finally:
        s.close()


def test_status_and_skill_buttons_filter(store, rows):
    assert store.search(status="ranked").total == 0
    store.set_status(rows[0].beatmapset_id, "ranked", "2020-01-01T00:00:00Z")
    assert store.search(status="ranked").total == 1
    skill = rows[0].dominant_skill
    assert all(any(b["matched"] and b["dominant_skill"] == skill for b in s["beatmaps"])
               for s in store.search(skill=skill).beatmapsets)


def test_paging(store):
    first = store.search(page_size=1, page=1)
    assert first.pages == first.total and len(first.beatmapsets) == 1
    second = store.search(page_size=1, page=2)
    assert second.beatmapsets[0]["id"] != first.beatmapsets[0]["id"]
    assert store.search(page_size=1, page=999).page == first.pages


def test_a_source_without_an_official_rating_keeps_the_known_one(store, rows):
    store.upsert(replace(rows[0], official_sr=None, official_sr_source=None, status="unknown"))
    s = store.beatmapset(rows[0].beatmapset_id)
    b = next(b for b in s["beatmaps"] if b["id"] == rows[0].beatmap_id)
    assert b["official_sr"] == pytest.approx(rows[0].official_sr) and b["official_sr_source"] == "manifest"


def test_the_same_chart_under_a_new_id_replaces_the_old_row(store, rows):
    store.upsert(replace(rows[0], beatmap_id=-42))
    assert store.stats()["beatmaps"] == len(rows)
    assert store.beatmapset_of(rows[0].beatmap_id) is None and store.beatmapset_of(-42) is not None


def test_beatmapset_page_data_is_full(store, rows):
    s = store.beatmapset(rows[0].beatmapset_id)
    b = s["beatmaps"][0]
    assert len(b["density"]) == 100 and set(b["skills"][b["dominant_skill"]]) == {"stars", "dominance", "coverage"}
    assert b["engine_version"] and b["checksum"]
    assert store.beatmapset(123456789) is None


def test_reevaluate_stale_reruns_the_engine_from_the_stored_osu(store, rows, monkeypatch):
    assert store.osu_content(rows[0].beatmap_id) == rows[0].osu_content
    monkeypatch.setattr(store_mod, "engine_version", lambda: "deadbeef")
    assert len(store.stale()) == len(rows)
    import proj7k.catalog.charts as charts_mod
    monkeypatch.setattr(charts_mod, "engine_version", lambda: "deadbeef")
    assert store.reevaluate_stale() == len(rows)
    assert store.stale() == []
    s = store.beatmapset(rows[0].beatmapset_id)
    b = next(b for b in s["beatmaps"] if b["id"] == rows[0].beatmap_id)
    assert b["engine_version"] == "deadbeef"
    assert b["official_sr"] == pytest.approx(rows[0].official_sr)
    assert b["engine_sr"] == pytest.approx(rows[0].engine_sr)
