"""
Where the catalog's charts come from. Each source gives `.osu` content plus whatever it knows about the
official star rating and the ranked status; the engine's rating is always computed here.

- `ingest_corpus`: the frozen benchmark corpus (120 charts) with the manifest's official ratings. Needs
  nothing outside the repository, so it is the demo and test data set.
- `ingest_osu_files`: `.osu` files or folders of them (an osu!stable `Songs` folder works). No official rating.
- `ingest_lazer`: the player's osu!lazer library through the Realm bridge, read-only. osu!lazer stores an
  official rating per difficulty, but `proj7k.sync` overwrites it with the engine's; the original is read
  from sync's backup state, and a rewritten difficulty without a backup is listed without an official rating.
- `ingest_osu_api`: osu!'s API v2 (`OsuApiClient`): every ranked/loved 7K mania set, the official rating
  and status from the API, the `.osu` from osu!'s download endpoint. Needs an OAuth client of the operator.
"""

import gzip
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from proj7k.catalog.charts import chart_row
from proj7k.catalog.store import CatalogStore

logger = logging.getLogger(__name__)


@dataclass
class IngestSummary:
    added: int = 0
    skipped: int = 0          # not 7K mania, or already current
    failed: List[Tuple[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return f"added {self.added}, skipped {self.skipped}, failed {len(self.failed)}"


def _add(store: CatalogStore, summary: IngestSummary, label: str, content: str, **known: Any) -> None:
    try:
        store.upsert(chart_row(content, **known))
        summary.added += 1
    except ValueError as e:   # not a 7K mania chart, or no notes
        summary.skipped += 1
        logger.debug("skip %s: %s", label, e)
    except Exception as e:    # an engine failure on one chart must not stop a crawl
        summary.failed.append((label, f"{type(e).__name__}: {e}"))
        logger.warning("failed %s: %s", label, e)


def manifest_official_sr(manifest_path: Path) -> Dict[str, float]:
    """Beatmap id -> official star rating, from the benchmark manifest (`pool -> tier -> {id, sr}`)."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return {str(entry["id"]): float(entry["sr"]) for tiers in manifest.values() for entry in tiers.values()
            if "sr" in entry}


def ingest_corpus(store: CatalogStore, corpus_path: Path, manifest_path: Optional[Path] = None) -> IngestSummary:
    with gzip.open(corpus_path, "rt", encoding="utf-8") as f:
        corpus: Dict[str, str] = json.load(f)
    official = manifest_official_sr(manifest_path) if manifest_path else {}
    summary = IngestSummary()
    for beatmap_id, content in corpus.items():
        sr = official.get(beatmap_id)
        _add(store, summary, beatmap_id, content, official_sr=sr, official_sr_source="manifest" if sr else None)
    return summary


def _osu_files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        path = Path(path)
        if path.is_dir():
            yield from sorted(path.rglob("*.osu"))
        elif path.is_file():
            yield path


def ingest_osu_files(store: CatalogStore, paths: Iterable[Path]) -> IngestSummary:
    summary = IngestSummary()
    for path in _osu_files(paths):
        _add(store, summary, str(path), path.read_text(encoding="utf-8", errors="replace"))
    return summary


def ingest_lazer(
    store: CatalogStore,
    realm_path: Optional[Path] = None,
    files_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    bridge: Any = None,
) -> IngestSummary:
    from proj7k.lazer.annotator import strip_injected_suffix
    from proj7k.lazer.backup import DEFAULT_CACHE_DIR, LazerBackupManager
    from proj7k.lazer.bridge import DEFAULT_REALM_PATH, RealmBridgeClient
    from proj7k.lazer.daemon import DEFAULT_FILES_DIR, resolve_beatmap_file

    realm_path = Path(realm_path or DEFAULT_REALM_PATH)
    files_dir = Path(files_dir or DEFAULT_FILES_DIR)
    bridge = bridge or RealmBridgeClient(default_realm_path=realm_path)
    originals = LazerBackupManager(realm_path=realm_path, cache_dir=Path(cache_dir or DEFAULT_CACHE_DIR)) \
        .load_backup_states()

    summary = IngestSummary()
    for rec in bridge.dump_7k_beatmaps(realm_path=realm_path):
        if not rec.is_7k_mania:
            summary.skipped += 1
            continue
        path = resolve_beatmap_file(files_dir, rec)
        if path is None:
            summary.failed.append((rec.md5_hash, "file not found in the lazer store"))
            continue
        if rec.id in originals:
            official: Optional[float] = originals[rec.id].original_star_rating
        elif strip_injected_suffix(rec.difficulty_name) != rec.difficulty_name.rstrip():
            official = None   # rewritten by sync and the original is gone
        else:
            official = rec.star_rating
        _add(store, summary, rec.md5_hash, path.read_text(encoding="utf-8", errors="replace"),
             official_sr=official, official_sr_source="lazer" if official is not None else None)
    return summary


# ---- osu! API v2 ---------------------------------------------------------------------------------

OSU_BASE = "https://osu.ppy.sh"
Fetch = Callable[[str, Dict[str, str], Optional[bytes]], bytes]


def _urllib_fetch(url: str, headers: Dict[str, str], body: Optional[bytes]) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


class OsuApiClient:
    """A minimal osu! API v2 client (client-credentials grant, `public` scope)."""

    def __init__(self, client_id: str, client_secret: str, fetch: Fetch = _urllib_fetch,
                 min_interval_s: float = 1.0, base: str = OSU_BASE):
        self.client_id, self.client_secret = client_id, client_secret
        self.fetch, self.base, self.min_interval_s = fetch, base, min_interval_s
        self._token: Optional[str] = None
        self._token_expiry = 0.0
        self._last = 0.0

    def _throttle(self) -> None:
        wait = self._last + self.min_interval_s - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def token(self) -> str:
        if self._token is None or time.time() > self._token_expiry - 60:
            body = urllib.parse.urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                                           "grant_type": "client_credentials", "scope": "public"}).encode()
            data = json.loads(self.fetch(f"{self.base}/oauth/token",
                                         {"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"}, body))
            self._token, self._token_expiry = data["access_token"], time.time() + float(data.get("expires_in", 0))
        return self._token

    def get(self, path: str, params: Optional[Dict[str, str]] = None) -> Any:
        self._throttle()
        url = f"{self.base}/api/v2/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        return json.loads(self.fetch(url, {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"},
                                     None))

    def search_7k(self, status: str = "ranked", max_pages: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Every mania beatmapset of `status` that has a 7K difficulty (osu!'s own `keys=7` filter)."""
        cursor: Optional[str] = None
        pages = 0
        while True:
            params = {"m": "3", "q": "keys=7", "s": status, "sort": "ranked_desc"}
            if cursor:
                params["cursor_string"] = cursor
            data = self.get("beatmapsets/search", params)
            yield from data.get("beatmapsets", [])
            cursor, pages = data.get("cursor_string"), pages + 1
            if not cursor or (max_pages is not None and pages >= max_pages):
                return

    def osu_file(self, beatmap_id: int) -> str:
        self._throttle()
        return self.fetch(f"{self.base}/osu/{beatmap_id}", {}, None).decode("utf-8", errors="replace")


def ingest_osu_api(store: CatalogStore, client: OsuApiClient, statuses: Iterable[str] = ("ranked", "loved"),
                   max_pages: Optional[int] = None) -> IngestSummary:
    """Crawl osu!'s 7K mania sets. A difficulty already in the catalog with the same MD5 is not downloaded
    again; its official rating and status are refreshed from the API."""
    summary = IngestSummary()
    known = set(store.checksums())
    for status in statuses:
        for bms in client.search_7k(status, max_pages=max_pages):
            for bm in bms.get("beatmaps", []):
                if bm.get("mode") != "mania" or round(float(bm.get("cs", 0))) != 7:
                    continue
                sr = float(bm["difficulty_rating"])
                if bm.get("checksum") in known:
                    store.set_official(int(bm["id"]), sr, "osu-api")
                    store.set_status(int(bms["id"]), bms.get("status", status), bms.get("ranked_date"))
                    summary.skipped += 1
                    continue
                try:
                    content = client.osu_file(int(bm["id"]))
                except Exception as e:
                    summary.failed.append((str(bm["id"]), f"download: {e}"))
                    continue
                _add(store, summary, str(bm["id"]), content, official_sr=sr, official_sr_source="osu-api",
                     status=bms.get("status", status), ranked_date=bms.get("ranked_date"))
    return summary
