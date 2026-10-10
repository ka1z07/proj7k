"""Catalog sources: the corpus, .osu files, the osu!lazer library and the osu! API (ADR-0026)."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from proj7k.catalog.cli import main as cli_main
from proj7k.catalog.ingest import (
    OsuApiClient,
    ingest_lazer,
    ingest_osu_api,
    ingest_osu_files,
)
from proj7k.catalog.store import CatalogStore
from proj7k.lazer.bridge import LazerBeatmapRecord


def test_osu_files_skip_other_modes(tmp_path, benchmark_corpus):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.osu").write_text(benchmark_corpus[3864745], encoding="utf-8")
    (tmp_path / "a" / "four.osu").write_text(
        "osu file format v14\n\n[General]\nMode: 3\n\n[Difficulty]\nCircleSize:4\n\n[HitObjects]\n64,192,1000,1,0,0:0:0:0:\n",
        encoding="utf-8")
    store = CatalogStore()
    summary = ingest_osu_files(store, [tmp_path])
    assert (summary.added, summary.skipped, summary.failed) == (1, 1, [])
    b = store.search().beatmapsets[0]["beatmaps"][0]
    assert b["official_sr"] is None and b["official_sr_source"] is None


class _FakeBridge:
    def __init__(self, records):
        self.records = records

    def dump_7k_beatmaps(self, realm_path=None, auto_setup=True):
        return self.records


def _record(id_, file_hash, name, sr):
    return LazerBeatmapRecord(id=id_, hash=file_hash, md5_hash=f"md5{id_}", file_hash=file_hash, star_rating=sr,
                              difficulty_name=name, tags="", title="t", artist="a", ruleset_id=3, circle_size=7.0)


def test_lazer_reads_the_official_rating_from_syncs_backup(tmp_path, benchmark_corpus):
    files = tmp_path / "files"
    contents = {"aa11": benchmark_corpus[3864745], "bb22": benchmark_corpus[3864746], "cc33": benchmark_corpus[3864747]}
    for h, content in contents.items():
        (files / h[0] / h[:2]).mkdir(parents=True, exist_ok=True)
        (files / h[0] / h[:2] / h).write_text(content, encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "lazer_backup_state.json").write_text(json.dumps({
        "r1": {"id": "r1", "original_star_rating": 3.83, "original_difficulty_name": "x", "original_tags": "",
               "recorded_at": "2026-10-08T00:00:00Z"}}), encoding="utf-8")
    records = [
        _record("r1", "aa11", "Another (3.29★ 0th Jack v8eb43ebd)", 3.29),   # rewritten, backed up
        _record("r2", "bb22", "SAMBAJACK (7.67★ 9th Jack v8eb43ebd)", 7.67),  # rewritten, no backup
        _record("r3", "cc33", "Hard", 3.75),                                  # untouched
        _record("r4", "dd44", "Missing", 1.0),                                # no file
    ]
    store = CatalogStore()
    summary = ingest_lazer(store, realm_path=tmp_path / "client.realm", files_dir=files, cache_dir=cache,
                           bridge=_FakeBridge(records))
    assert summary.added == 3 and len(summary.failed) == 1
    official = {b["id"]: (b["official_sr"], b["official_sr_source"])
                for s in store.search().beatmapsets for b in s["beatmaps"]}
    assert official[3864745] == (3.83, "lazer")
    assert official[3864746] == (None, None)
    assert official[3864747] == (3.75, "lazer")


class _FakeOsu:
    """osu!'s token, search and download endpoints, with two search pages."""

    def __init__(self, corpus):
        self.corpus = corpus
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append(url)
        parts = urlsplit(url)
        if parts.path == "/oauth/token":
            assert b"grant_type=client_credentials" in body
            return json.dumps({"access_token": "tok", "expires_in": 86400}).encode()
        if parts.path == "/api/v2/beatmapsets/search":
            assert headers["Authorization"] == "Bearer tok"
            q = parse_qs(parts.query)
            assert q["m"] == ["3"] and q["q"] == ["keys=7"]
            if "cursor_string" not in q:
                return json.dumps({"beatmapsets": [{"id": 1877617, "status": "ranked", "ranked_date": "2023-01-01T00:00:00Z",
                    "user_id": 7, "creator": "Host", "submitted_date": "2022-12-01T00:00:00Z",
                    "beatmaps": [{"id": 3864745, "mode": "mania", "cs": 7, "difficulty_rating": 3.83649, "checksum": "x",
                                  "user_id": 7},
                                 {"id": 999, "mode": "mania", "cs": 4, "difficulty_rating": 2.0, "checksum": "y"}]}],
                    "cursor_string": "next"}).encode()
            return json.dumps({"beatmapsets": [{"id": 1877617, "status": "ranked", "user_id": 7, "creator": "Host",
                "beatmaps": [{"id": 3864746, "mode": "mania", "cs": 7.0, "difficulty_rating": 7.04616, "checksum": "z",
                              "user_id": 8}]}],
                "cursor_string": None}).encode()
        if parts.path == "/api/v2/users":
            ids = parse_qs(parts.query)["ids[]"]
            return json.dumps({"users": [{"id": int(i), "username": f"Guest{i}"} for i in ids]}).encode()
        if parts.path.startswith("/osu/"):
            return self.corpus[int(parts.path.rsplit("/", 1)[1])].encode()
        raise AssertionError(url)


def test_osu_api_crawl(benchmark_corpus):
    fake = _FakeOsu(benchmark_corpus)
    client = OsuApiClient("id", "secret", fetch=fake, min_interval_s=0)
    store = CatalogStore()
    summary = ingest_osu_api(store, client, statuses=("ranked",))
    assert summary.added == 2 and not summary.failed
    s = store.beatmapset(1877617)
    assert s["status"] == "ranked" and s["ranked_date"] == "2023-01-01T00:00:00Z"
    assert {b["id"]: b["official_sr"] for b in s["beatmaps"]} == {3864745: 3.83649, 3864746: 7.04616}
    assert all(b["official_sr_source"] == "osu-api" for b in s["beatmaps"])
    # The host's difficulty and a guest difficulty whose owner the listing names only by id.
    assert {b["id"]: (b["mapper_id"], b["mapper_name"]) for b in s["beatmaps"]} == {
        3864745: (7, "Host"), 3864746: (8, "Guest8")}
    assert s["creator_id"] == 7 and s["submitted_date"] == "2022-12-01T00:00:00Z"
    assert sum(1 for u in fake.calls if "/oauth/token" in u) == 1
    assert not any(u.endswith("/osu/999") for u in fake.calls)


def test_osu_api_refreshes_known_charts_without_downloading(benchmark_corpus):
    fake = _FakeOsu(benchmark_corpus)
    store = CatalogStore()
    ingest_osu_api(store, OsuApiClient("id", "secret", fetch=fake, min_interval_s=0), statuses=("ranked",))
    checksum = store.beatmapset(1877617)["beatmaps"][0]["checksum"]

    def fake2(url, headers, body):
        out = fake(url, headers, body)
        if "beatmapsets/search" in url:
            data = json.loads(out)
            for bm in data["beatmapsets"][0]["beatmaps"]:
                bm["checksum"] = checksum if bm["id"] == 3864745 else bm["checksum"]
                bm["difficulty_rating"] = 4.0 if bm["id"] == 3864745 else bm["difficulty_rating"]
            out = json.dumps(data).encode()
        return out

    fake.calls.clear()
    summary = ingest_osu_api(store, OsuApiClient("id", "secret", fetch=fake2, min_interval_s=0), statuses=("ranked",))
    assert summary.skipped >= 1
    assert not any(u.endswith("/osu/3864745") for u in fake.calls)
    b = next(b for b in store.beatmapset(1877617)["beatmaps"] if b["id"] == 3864745)
    assert b["official_sr"] == 4.0


def test_crawl_retries_a_dropped_connection(benchmark_corpus):
    fake = _FakeOsu(benchmark_corpus)
    failures = {"n": 0}

    def flaky(url, headers, body):
        if "/osu/" in url and failures["n"] < 1:
            failures["n"] += 1
            raise ConnectionResetError("reset")
        return fake(url, headers, body)

    store = CatalogStore()
    summary = ingest_osu_api(store, OsuApiClient("id", "secret", fetch=flaky, min_interval_s=0), statuses=("ranked",))
    assert summary.added == 2 and not summary.failed and failures["n"] == 1


def test_unranked_statuses_sort_by_update(benchmark_corpus):
    fake = _FakeOsu(benchmark_corpus)
    client = OsuApiClient("id", "secret", fetch=fake, min_interval_s=0)
    list(client.search_7k("graveyard", max_pages=1))
    assert any("s=graveyard" in u and "sort=updated_desc" in u for u in fake.calls)


def test_cli_build_and_refresh(tmp_path, benchmark_corpus_path, benchmark_manifest_path, capsys):
    db = tmp_path / "c.sqlite3"
    assert cli_main(["--db", str(db), "build", "--corpus", str(benchmark_corpus_path),
                     "--manifest", str(benchmark_manifest_path)]) == 0
    assert "added 120" in capsys.readouterr().out
    with CatalogStore(db) as store:
        assert store.stats()["with_official_sr"] == 120   # the manifest's official ratings
    assert cli_main(["--db", str(db), "refresh"]) == 0
    assert "re-evaluated 0" in capsys.readouterr().out
    assert cli_main(["--db", str(tmp_path / "none.sqlite3"), "serve"]) == 2
