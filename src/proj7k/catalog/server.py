"""
The catalog website and its JSON API, on the standard library's threaded HTTP server.

    GET /                                 the home page: what proj7k is, the way into the catalog
    GET /beatmapsets                      the listing (search page)
    GET /beatmapsets/<set id>             a set's page; `#<beatmap id>` selects a difficulty
    GET /beatmaps/<beatmap id>            redirects to its set's page
    GET /api/beatmapsets/search?q=&status=&skill=&sort=&page=
    GET /api/beatmapsets/<set id>
    GET /api/stats
    GET /mappers                          mappers (the list), `/mappers/<user id or @name>` one mapper's style
    GET /api/mappers?q=&sort=&min=&page=
    GET /api/mappers/<user id or @name>
    GET /oauth/start, /oauth/callback; POST /oauth/redeem, /oauth/refresh
                                          "sign in with osu!" for the desktop app (`proj7k.catalog.oauth`, ADR-0041)
    GET /robots.txt, /sitemap.xml, /sitemaps/<pages|mappers|sets-N>.xml
                                          for search engines (`proj7k.catalog.seo`, ADR-0042)

Read-only: nothing a client sends reaches the database except as a bound SQL parameter, and only the
files of `static/` are served. In production it sits behind a reverse proxy (TLS, caching, rate limits).
"""

import hashlib
import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from proj7k.catalog import seo
from proj7k.catalog.mappers import MapperIndex
from proj7k.catalog.oauth import OAuthBroker, local_state, parse_state
from proj7k.catalog.store import CatalogStore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
STATIC_FILES = {p.name for p in STATIC_DIR.iterdir() if p.suffix in STATIC_TYPES} if STATIC_DIR.is_dir() else set()
#: The pages link scripts and styles as `/static/<name>?v=<content hash>`, so a deploy never pairs a new page
#: with a script or stylesheet a browser or the proxy kept from before (the proxy may cache them for hours).
_ASSET_VERSIONS = {
    name: hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
    for name in STATIC_FILES if name.endswith((".js", ".css"))
}
_ASSET_LINK = re.compile(r'"/static/([\w.-]+\.(?:js|css))"')

_SET_PAGE = re.compile(r"^/beatmapsets/(-?\d+)/?$")
_BEATMAP_PAGE = re.compile(r"^/beatmaps/(-?\d+)/?$")
_SET_API = re.compile(r"^/api/beatmapsets/(-?\d+)$")
#: How long a browser or a caching proxy may reuse an answer. The catalog changes once a day.
API_MAX_AGE_S = 60
STATIC_MAX_AGE_S = 300
SITEMAP_MAX_AGE_S = 3600

_MAPPER_PAGE = re.compile(r"^/mappers/([^/]+)/?$")
_MAPPER_API = re.compile(r"^/api/mappers/([^/]+)$")
_SITEMAP = re.compile(r"^/sitemaps/(pages|mappers|sets-(\d+))\.xml$")


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


class CatalogHandler(BaseHTTPRequestHandler):
    server_version = "proj7k-catalog"
    store: CatalogStore   # set on the subclass `make_server` builds
    mappers: MapperIndex
    oauth: Optional[OAuthBroker]
    base: str             # the site's public address, for canonical links and the sitemap

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s %s", self.address_string(), fmt % args)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path, query = parts.path, {k: v[-1] for k, v in parse_qs(parts.query).items()}
        self._answer(lambda: self.route(path, query))

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        length = _int(self.headers.get("Content-Length"), 0)
        if not 0 <= length <= 8192:
            self._answer(lambda: self._json({"error": "too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE))
            return
        raw = self.rfile.read(length)
        self._answer(lambda: self.route_post(path, raw))

    def _answer(self, handle: Any) -> None:
        try:
            status, headers, body = handle()
        except Exception:
            logger.exception("error serving %s", self.path)
            status, headers, body = self._json({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ---- routing ---------------------------------------------------------------------------------

    def route(self, path: str, query: Dict[str, str]) -> Tuple[int, Dict[str, str], bytes]:
        if path in ("/", ""):
            return self._page("home.html", "/")
        if path == "/beatmapsets":
            return self._page("search.html", "/beatmapsets")
        m = _SET_PAGE.match(path)
        if m:
            data = self.store.beatmapset(int(m.group(1)))
            if data is None:
                return self._missing_page("beatmapset.html", path)
            return self._page("beatmapset.html", path, *seo.set_page(self.base, data))
        m = _BEATMAP_PAGE.match(path)
        if m:
            set_id = self.store.beatmapset_of(int(m.group(1)))
            if set_id is None:
                return self._not_found()
            return self._redirect(f"/beatmapsets/{set_id}#{m.group(1)}")
        if path == "/api/beatmapsets/search":
            result = self.store.search(
                q=query.get("q", ""), status=query.get("status"), skill=query.get("skill"),
                sort=query.get("sort"), page=_int(query.get("page"), 1), page_size=_int(query.get("size"), 50),
                seed=_int(query.get("seed"), -1) if "seed" in query else None,
            )
            status, headers, body = self._json(result.to_dict())
            if result.seed is not None and "seed" not in query:
                headers["Cache-Control"] = "no-store"   # a fresh draw each time
            return status, headers, body
        m = _SET_API.match(path)
        if m:
            data = self.store.beatmapset(int(m.group(1)))
            return self._json(data) if data else self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if path == "/mappers":
            return self._page("mappers.html", "/mappers")
        m = _MAPPER_PAGE.match(path)
        if m:
            key = unquote(m.group(1))
            data = self.mappers.get(key)
            if data is None:
                return self._missing_page("mapper.html", path)
            return self._page("mapper.html", path, *seo.mapper_page(self.base, key, data))
        if path == "/api/mappers":
            return self._json(self.mappers.listing(
                q=query.get("q", ""), sort=query.get("sort", "diffs_desc"), min_diffs=_int(query.get("min"), 1),
                page=_int(query.get("page"), 1), page_size=_int(query.get("size"), 50)))
        m = _MAPPER_API.match(path)
        if m:
            data = self.mappers.get(unquote(m.group(1)))
            return self._json(data) if data else self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if path.startswith("/oauth/"):
            return self._oauth(path, query)
        if path == "/api/stats":
            return self._json(self.store.stats())
        if path.startswith("/static/") and path[len("/static/"):] in STATIC_FILES:
            return self._static(path[len("/static/"):])
        if path == "/favicon.ico":
            return self._static("favicon.svg")
        if path == "/robots.txt":
            return self._text(seo.robots(self.base), "text/plain")
        if path == "/sitemap.xml":
            sets = self.store.sitemap_sets()
            chunks = max(1, -(-len(sets) // seo.SITEMAP_CHUNK))
            return self._text(seo.sitemap_index(self.base, chunks, max((d for _, d in sets), default=None)))
        m = _SITEMAP.match(path)
        if m:
            return self._sitemap(m.group(1), int(m.group(2) or 0))
        return self._not_found()

    # ---- pages and what search engines read (ADR-0042) -----------------------------------------------

    def _page(self, name: str, path: str, head: Optional[str] = None, summary: str = "",
              status: int = HTTPStatus.OK) -> Tuple[int, Dict[str, str], bytes]:
        _, headers, body = self._static(name)
        page = body.decode("utf-8")
        if head is None:
            head = seo.static_head(self.base, path, page)
        return status, headers, seo.render(page, head, summary).encode("utf-8")

    def _missing_page(self, name: str, path: str) -> Tuple[int, Dict[str, str], bytes]:
        """The page's own "not found" (its script says so), as a 404 search engines drop."""
        head = seo.head(self.base, path, seo.SITE_NAME, "找不到这个页面。", index=False)
        status, headers, body = self._page(name, path, head, status=HTTPStatus.NOT_FOUND)
        headers["Cache-Control"] = "no-cache"
        return status, headers, body

    def _sitemap(self, name: str, chunk: int) -> Tuple[int, Dict[str, str], bytes]:
        if name == "pages":
            return self._text(seo.urlset(self.base, [(p, None) for p in seo.STATIC_PAGES]))
        if name == "mappers":
            keys = sorted(self.mappers.profiles(), key=lambda k: (k.startswith("@"), k))
            return self._text(seo.urlset(self.base, [(f"/mappers/{quote(k, safe='@')}", None) for k in keys]))
        sets = self.store.sitemap_sets()[(chunk - 1) * seo.SITEMAP_CHUNK: chunk * seo.SITEMAP_CHUNK]
        if chunk < 1 or not sets:
            return self._not_found()
        return self._text(seo.urlset(self.base, [(f"/beatmapsets/{i}", d) for i, d in sets]))

    @staticmethod
    def _text(text: str, kind: str = "application/xml") -> Tuple[int, Dict[str, str], bytes]:
        return HTTPStatus.OK, {"Content-Type": f"{kind}; charset=utf-8",
                               "Cache-Control": f"public, max-age={SITEMAP_MAX_AGE_S}"}, text.encode("utf-8")

    # ---- sign in with osu! (ADR-0041) -----------------------------------------------------------------

    def _oauth(self, path: str, query: Dict[str, str]) -> Tuple[int, Dict[str, str], bytes]:
        if self.oauth is None:
            return self._json({"error": "sign-in is not configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
        if path == "/oauth/start":
            state = local_state(query.get("port"), query.get("nonce"))
            if state is None:
                return self._json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)
            return self._redirect(self.oauth.authorize_url(state))
        if path == "/oauth/callback":
            parsed = parse_state(query.get("state", ""))
            if parsed is None:
                return self._json({"error": "bad state"}, HTTPStatus.BAD_REQUEST)
            port, nonce = parsed
            back = {"nonce": nonce}
            if query.get("code"):
                try:
                    back["handoff"] = self.oauth.exchange(query["code"])
                except Exception as e:
                    logger.warning("osu! sign-in exchange failed: %s", e)
                    back["error"] = "exchange_failed"
            else:
                back["error"] = query.get("error") or "denied"
            return self._redirect(f"http://127.0.0.1:{port}/oauth/done?{urlencode(back)}")
        return self._not_found()

    def route_post(self, path: str, raw: bytes) -> Tuple[int, Dict[str, str], bytes]:
        if not path.startswith("/oauth/"):
            return self._not_found()
        if self.oauth is None:
            return self._json({"error": "sign-in is not configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)
        if path == "/oauth/redeem":
            tokens = self.oauth.redeem(body.get("handoff"))
            if tokens is None:
                return self._json({"error": "unknown or expired handoff"}, HTTPStatus.NOT_FOUND)
            return self._json(tokens)
        if path == "/oauth/refresh":
            if not isinstance(body.get("refresh_token"), str):
                return self._json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)
            try:
                return self._json(self.oauth.refresh(body["refresh_token"]))
            except Exception as e:
                logger.info("osu! token refresh failed: %s", e)
                return self._json({"error": "refresh failed"}, HTTPStatus.UNAUTHORIZED)
        return self._not_found()

    @staticmethod
    def _json(data: Any, status: int = HTTPStatus.OK) -> Tuple[int, Dict[str, str], bytes]:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cache = f"public, max-age={API_MAX_AGE_S}" if status == HTTPStatus.OK else "no-cache"
        if not isinstance(data, dict) or "access_token" in data:
            cache = "no-store"
        return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": cache}, body

    @staticmethod
    def _static(name: str) -> Tuple[int, Dict[str, str], bytes]:
        path = STATIC_DIR / name
        body = path.read_bytes()
        if path.suffix == ".html":
            body = _ASSET_LINK.sub(
                lambda m: f'"/static/{m.group(1)}?v={_ASSET_VERSIONS[m.group(1)]}"' if m.group(1) in _ASSET_VERSIONS
                else m.group(0), body.decode("utf-8")).encode("utf-8")
        return HTTPStatus.OK, {"Content-Type": STATIC_TYPES[path.suffix],
                                 "Cache-Control": f"public, max-age={STATIC_MAX_AGE_S}"}, body

    @staticmethod
    def _redirect(location: str) -> Tuple[int, Dict[str, str], bytes]:
        return HTTPStatus.FOUND, {"Location": location, "Cache-Control": "no-store"}, b""

    def _not_found(self) -> Tuple[int, Dict[str, str], bytes]:
        if self.path.startswith("/api/"):
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        return HTTPStatus.NOT_FOUND, {"Content-Type": "text/plain; charset=utf-8"}, b"not found"


def make_server(store: CatalogStore, host: str = "127.0.0.1", port: int = 7780,
                mapper_stale_s: Optional[float] = None, oauth: Optional[OAuthBroker] = None,
                public_url: str = "https://7k.kaiz.dev") -> ThreadingHTTPServer:
    """`mapper_stale_s`: see `MapperIndex`; the command line sets it so a crawl running beside the site
    never makes a visitor wait for the mapper profiles. `oauth`: the osu! client behind "sign in with osu!";
    without one `/oauth/*` answers 503. `public_url`: where the site is reached, for canonical links."""
    mappers = MapperIndex(store, stale_s=mapper_stale_s)
    handler = type("BoundCatalogHandler", (CatalogHandler,),
                   {"store": store, "mappers": mappers, "oauth": oauth, "base": public_url.rstrip("/")})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
