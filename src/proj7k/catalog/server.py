"""
The catalog website and its JSON API, on the standard library's threaded HTTP server.

    GET /beatmapsets                      the listing (search page)
    GET /beatmapsets/<set id>             a set's page; `#<beatmap id>` selects a difficulty
    GET /beatmaps/<beatmap id>            redirects to its set's page
    GET /api/beatmapsets/search?q=&status=&skill=&sort=&page=
    GET /api/beatmapsets/<set id>
    GET /api/stats

Read-only: nothing a client sends reaches the database except as a bound SQL parameter, and only the
files of `static/` are served. In production it sits behind a reverse proxy (TLS, caching, rate limits).
"""

import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from proj7k.catalog.store import CatalogStore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
STATIC_FILES = {p.name for p in STATIC_DIR.iterdir() if p.suffix in STATIC_TYPES} if STATIC_DIR.is_dir() else set()

_SET_PAGE = re.compile(r"^/beatmapsets/(-?\d+)/?$")
_BEATMAP_PAGE = re.compile(r"^/beatmaps/(-?\d+)/?$")
_SET_API = re.compile(r"^/api/beatmapsets/(-?\d+)$")


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


class CatalogHandler(BaseHTTPRequestHandler):
    server_version = "proj7k-catalog"
    store: CatalogStore   # set on the subclass `make_server` builds

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s %s", self.address_string(), fmt % args)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path, query = parts.path, {k: v[-1] for k, v in parse_qs(parts.query).items()}
        try:
            status, headers, body = self.route(path, query)
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
            return self._redirect("/beatmapsets")
        if path == "/beatmapsets":
            return self._static("search.html")
        if _SET_PAGE.match(path):
            return self._static("beatmapset.html")
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
            )
            return self._json(result.to_dict())
        m = _SET_API.match(path)
        if m:
            data = self.store.beatmapset(int(m.group(1)))
            return self._json(data) if data else self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if path == "/api/stats":
            return self._json(self.store.stats())
        if path.startswith("/static/") and path[len("/static/"):] in STATIC_FILES:
            return self._static(path[len("/static/"):])
        return self._not_found()

    @staticmethod
    def _json(data: Any, status: int = HTTPStatus.OK) -> Tuple[int, Dict[str, str], bytes]:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-cache"}, body

    @staticmethod
    def _static(name: str) -> Tuple[int, Dict[str, str], bytes]:
        path = STATIC_DIR / name
        return HTTPStatus.OK, {"Content-Type": STATIC_TYPES[path.suffix], "Cache-Control": "no-cache"}, \
            path.read_bytes()

    @staticmethod
    def _redirect(location: str) -> Tuple[int, Dict[str, str], bytes]:
        return HTTPStatus.FOUND, {"Location": location}, b""

    def _not_found(self) -> Tuple[int, Dict[str, str], bytes]:
        if self.path.startswith("/api/"):
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        return HTTPStatus.NOT_FOUND, {"Content-Type": "text/plain; charset=utf-8"}, b"not found"


def make_server(store: CatalogStore, host: str = "127.0.0.1", port: int = 7780) -> ThreadingHTTPServer:
    handler = type("BoundCatalogHandler", (CatalogHandler,), {"store": store})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
