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

    def _retrying(self, call: Callable[[], bytes], retries: int = 4) -> bytes:
        """A long crawl must outlive a dropped connection or a rate-limit answer: back off and try again."""
        for attempt in range(retries + 1):
            self._throttle()
            try:
                return call()
            except Exception as e:
                if attempt == retries or getattr(e, "code", None) in (400, 401, 403, 404):
                    raise
                logger.warning("osu! request failed (%s); retrying", e)
                time.sleep(self.min_interval_s * 5 * 2 ** attempt)
        raise AssertionError("unreachable")

    def get(self, path: str, params: Optional[Any] = None) -> Any:
        url = f"{self.base}/api/v2/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        return json.loads(self._retrying(lambda: self.fetch(
            url, {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"}, None)))

    def users(self, ids: List[int]) -> Dict[int, str]:
        """Usernames of osu! users, 50 per request."""
        names: Dict[int, str] = {}
        for i in range(0, len(ids), 50):
            data = self.get("users", [("ids[]", str(u)) for u in ids[i:i + 50]])
            names.update({int(u["id"]): u["username"] for u in data.get("users", [])})
        return names

    def search_7k(self, status: str = "ranked", max_pages: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Every mania beatmapset of `status` that has a 7K difficulty (osu!'s own `keys=7` filter)."""
        cursor: Optional[str] = None
        pages = 0
        while True:
            # Unranked sets have no ranked date to sort by; any stable order works with the cursor.
            sort = "ranked_desc" if status in ("ranked", "loved", "qualified", "approved") else "updated_desc"
            params = {"m": "3", "q": "keys=7", "s": status, "sort": sort}
            if cursor:
                params["cursor_string"] = cursor
            data = self.get("beatmapsets/search", params)
            yield from data.get("beatmapsets", [])
            cursor, pages = data.get("cursor_string"), pages + 1
            if not cursor or (max_pages is not None and pages >= max_pages):
                return

    def osu_file(self, beatmap_id: int) -> str:
        return self._retrying(lambda: self.fetch(f"{self.base}/osu/{beatmap_id}", {}, None)) \
            .decode("utf-8", errors="replace")


#: Every listing status osu!'s search knows for a set that can carry a 7K difficulty.
ALL_STATUSES = ("ranked", "loved", "qualified", "pending", "wip", "graveyard")


def _owner(bm: Dict[str, Any], bms: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """Who wrote a difficulty: its first owner (guest difficulties), else its `user_id`, else the set's creator."""
    owners = bm.get("owners") or []
    if owners and owners[0].get("id"):
        return int(owners[0]["id"]), owners[0].get("username")
    user_id = bm.get("user_id") or bms.get("user_id")
    if not user_id:
        return None, None
    return int(user_id), bms.get("creator") if user_id == bms.get("user_id") else None


def ingest_osu_api(store: CatalogStore, client: OsuApiClient, statuses: Iterable[str] = ("ranked", "loved"),
                   max_pages: Optional[int] = None) -> IngestSummary:
    """Crawl osu!'s 7K mania sets. A difficulty already in the catalog with the same MD5 is not downloaded
    again; its official rating, status and mapper are refreshed from the API. Guest mappers the listing
    names only by id are looked up at the end."""
    summary = IngestSummary()
    known = set(store.checksums())
    names: Dict[int, str] = {}
    for status in statuses:
        for bms in client.search_7k(status, max_pages=max_pages):
            set_status = bms.get("status", status)
            for bm in bms.get("beatmaps", []):
                if bm.get("mode") != "mania" or round(float(bm.get("cs", 0))) != 7:
                    continue
                sr = float(bm["difficulty_rating"])
                mapper_id, mapper_name = _owner(bm, bms)
                if mapper_id and mapper_name:
                    names[mapper_id] = mapper_name
                if bm.get("checksum") in known:
                    store.set_official(int(bm["id"]), sr, "osu-api")
                    store.set_status(int(bms["id"]), set_status, bms.get("ranked_date"))
                    if mapper_id:
                        store.set_mapper(int(bm["id"]), mapper_id, mapper_name)
                    summary.skipped += 1
                    continue
                try:
                    content = client.osu_file(int(bm["id"]))
                except Exception as e:
                    summary.failed.append((str(bm["id"]), f"download: {e}"))
                    continue
                _add(store, summary, str(bm["id"]), content, official_sr=sr, official_sr_source="osu-api",
                     status=set_status, ranked_date=bms.get("ranked_date"), submitted_date=bms.get("submitted_date"),
                     creator_id=bms.get("user_id"), mapper_id=mapper_id, mapper_name=mapper_name)
                known.add(bm.get("checksum"))
    store.set_users(names)
    unnamed = store.unnamed_mappers()
    if unnamed:
        try:
            store.set_users(client.users(unnamed))
        except Exception as e:
            logger.warning("could not look up %d mapper names: %s", len(unnamed), e)
    return summary
