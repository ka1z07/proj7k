"""The catalog website and JSON API (ADR-0026)."""

import json
import re
import threading
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest

from proj7k.catalog.charts import chart_row
from proj7k.catalog.server import STATIC_DIR, make_server
from proj7k.catalog.store import CatalogStore


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@pytest.fixture(scope="module")
def site(benchmark_corpus):
    store = CatalogStore()
    for beatmap_id in (3864745, 3864746, 3888155):
        store.upsert(chart_row(benchmark_corpus[beatmap_id], 4.2, "manifest"))
    server = make_server(store, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    store.close()


def _get(url):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_pages_are_served(site):
    status, headers, body = _get(f"{site}/beatmapsets")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b"Catalog.searchPage()" in body
    status, _, body = _get(f"{site}/beatmapsets/1877617")
    assert status == 200 and b"Catalog.setPage()" in body
    assert _get(f"{site}/")[0] == 302


def test_search_api_returns_both_star_ratings(site):
    status, headers, body = _get(f"{site}/api/beatmapsets/search?q={quote('engine>0')}&sort=engine_desc")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    data = json.loads(body)
    assert data["total"] == 2 and data["filters"] == ["engine>0"] and data["sort"] == "engine_desc"
    for s in data["beatmapsets"]:
        for b in s["beatmaps"]:
            assert b["official_sr"] == 4.2 and b["engine_sr"] > 0


def test_beatmap_redirects_to_its_set(site):
    status, headers, _ = _get(f"{site}/beatmaps/3864746")
    assert status == 302 and headers["Location"] == "/beatmapsets/1877617#3864746"
    assert _get(f"{site}/beatmaps/1")[0] == 404


def test_set_api_and_not_found(site):
    status, _, body = _get(f"{site}/api/beatmapsets/1877617")
    assert status == 200 and len(json.loads(body)["beatmaps"]) == 2
    status, _, body = _get(f"{site}/api/beatmapsets/42")
    assert status == 404 and json.loads(body) == {"error": "not found"}
    assert _get(f"{site}/api/stats")[0] == 200


def test_only_static_files_are_served(site):
    assert _get(f"{site}/static/catalog.js")[0] == 200
    assert _get(f"{site}/static/../server.py")[0] == 404
    assert _get(f"{site}/static/%2e%2e/server.py")[0] == 404
    assert _get(f"{site}/static/nope.js")[0] == 404


def test_bad_parameters_do_not_break_the_api(site):
    status, _, body = _get(f"{site}/api/beatmapsets/search?page=abc&size=-5&sort=evil;drop")
    assert status == 200 and json.loads(body)["sort"] == "newest_desc"
    status, _, body = _get(f"{site}/api/beatmapsets/search?q={quote(chr(39) + ' OR 1=1 --')}")
    assert status == 200 and json.loads(body)["total"] == 0


def test_pages_load_no_external_scripts_or_styles():
    """The site's own code is self-contained; only osu!'s cover images are fetched from outside."""
    for path in STATIC_DIR.iterdir():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"<(script|link)[^>]+(src|href)=[\"'](https?:)?//", text), path.name
        assert "@import" not in text, path.name
        assert not re.search(r"https?://(?!assets\.ppy\.sh/|osu\.ppy\.sh/|a\.ppy\.sh/)", text), path.name
