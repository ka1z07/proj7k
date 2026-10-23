"""The catalog website and JSON API (ADR-0026)."""

import hashlib
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
    status, headers, body = _get(f"{site}/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b"Catalog.homePage()" in body and b'href="/beatmapsets"' in body


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
    """The site's own code is self-contained; only osu!'s cover images are fetched from outside.
    Plain links may also point at proj7k's own homes (its repository, the author's blog)."""
    for path in STATIC_DIR.iterdir():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"<(script|link)[^>]+(src|href)=[\"'](https?:)?//", text), path.name
        assert "@import" not in text, path.name
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg|assets\.ppy\.sh/|osu\.ppy\.sh/|a\.ppy\.sh/|github\.com/ka1z07/proj7k|kaiz\.dev/)", text), path.name


def test_pages_link_scripts_and_styles_by_content_version(site):
    """A deploy must not pair a new page with a script or stylesheet cached from before it."""
    body = _get(f"{site}/")[2].decode("utf-8")
    links = re.findall(r'"(/static/catalog\.(?:js|css)[^"]*)"', body)
    assert len(links) == 2 and all(re.search(r"\?v=[0-9a-f]{10}$", link) for link in links)
    for link in links:
        status, _, asset = _get(f"{site}{link}")
        assert status == 200 and hashlib.sha256(asset).hexdigest()[:10] == link.rsplit("=", 1)[1]


def test_answers_may_be_cached_briefly_but_errors_not(site):
    assert _get(f"{site}/api/stats")[1]["Cache-Control"] == "public, max-age=60"
    assert _get(f"{site}/static/catalog.js")[1]["Cache-Control"] == "public, max-age=300"
    assert _get(f"{site}/api/beatmapsets/42")[1]["Cache-Control"] == "no-cache"


def test_a_random_page_without_a_seed_is_never_cached(site):
    _, headers, body = _get(f"{site}/api/beatmapsets/search?sort=random")
    assert headers["Cache-Control"] == "no-store" and isinstance(json.loads(body)["seed"], int)
    _, headers, body = _get(f"{site}/api/beatmapsets/search?sort=random&seed=3")
    assert headers["Cache-Control"] == "public, max-age=60" and json.loads(body)["seed"] == 3


def test_a_set_page_carries_its_own_title_description_and_summary(site):
    """Search engines and link previews read the HTML, not what the script draws (ADR-0042)."""
    status, _, body = _get(f"{site}/beatmapsets/1877617")
    page = body.decode("utf-8")
    assert status == 200 and page.count("<title>") == 1
    title = re.search(r"<title>(.*?)</title>", page).group(1)
    assert "osu!mania 7K" in title and title != "谱面 · proj7k 7K 谱面库"
    assert '<link rel="canonical" href="https://7k.kaiz.dev/beatmapsets/1877617">' in page
    assert re.search(r'<meta name="description" content="[^"]*官方星级 4\.20★', page)
    assert 'property="og:image" content="https://assets.ppy.sh/beatmaps/1877617/covers/cover@2x.jpg"' in page
    summary = re.search(r'<section class="ssr container" id="ssr">(.*?)</section>', page, re.S).group(1)
    assert summary.count("<tr>") == 3 and "<h1>" in summary
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page).group(1))
    assert ld["@type"] == "BreadcrumbList" and len(ld["itemListElement"]) == 2


def test_a_missing_set_or_mapper_is_a_real_404_kept_out_of_the_index(site):
    for path in ("/beatmapsets/42", "/mappers/999999999"):
        status, headers, body = _get(f"{site}{path}")
        assert status == 404 and headers["Content-Type"].startswith("text/html"), path
        assert b'<meta name="robots" content="noindex">' in body and b'id="ssr"' not in body


def test_a_mapper_page_carries_its_own_title(site):
    key = json.loads(_get(f"{site}/api/mappers")[2])["mappers"][0]["key"]
    status, _, body = _get(f"{site}/mappers/{quote(key, safe='@')}")
    page = body.decode("utf-8")
    assert status == 200 and "osu!mania 7K 谱师" in re.search(r"<title>(.*?)</title>", page).group(1)
    assert f'href="https://7k.kaiz.dev/mappers/{quote(key, safe='@')}"' in page and 'id="ssr"' in page


def test_fixed_pages_have_a_description_and_canonical(site):
    for path in ("/", "/beatmapsets", "/mappers"):
        page = _get(f"{site}{path}?q=x")[2].decode("utf-8")
        assert page.count('<meta name="description"') == 1, path
        assert f'<link rel="canonical" href="https://7k.kaiz.dev{path}">' in page, path


def test_robots_and_sitemaps(site):
    status, headers, body = _get(f"{site}/robots.txt")
    assert status == 200 and headers["Content-Type"].startswith("text/plain")
    assert "Sitemap: https://7k.kaiz.dev/sitemap.xml" in body.decode() and "Disallow: /api/" not in body.decode()
    index = _get(f"{site}/sitemap.xml")[2].decode()
    locs = re.findall(r"<loc>https://7k\.kaiz\.dev(/sitemaps/[\w-]+\.xml)</loc>", index)
    assert locs == ["/sitemaps/pages.xml", "/sitemaps/mappers.xml", "/sitemaps/sets-1.xml"]
    urls = {}
    for loc in locs:
        status, headers, body = _get(f"{site}{loc}")
        assert status == 200 and headers["Content-Type"].startswith("application/xml"), loc
        urls[loc] = re.findall(r"<loc>https://7k\.kaiz\.dev([^<]*)</loc>", body.decode())
    assert len(urls["/sitemaps/sets-1.xml"]) == 2
    assert "/" in urls["/sitemaps/pages.xml"] and urls["/sitemaps/mappers.xml"]
    for path in urls["/sitemaps/sets-1.xml"] + urls["/sitemaps/mappers.xml"]:
        assert _get(f"{site}{path}")[0] == 200, path
    assert _get(f"{site}/sitemaps/sets-2.xml")[0] == 404
    assert _get(f"{site}/favicon.ico")[0] == 200
