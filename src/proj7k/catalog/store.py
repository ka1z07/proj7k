"""
The catalog database: one SQLite file with beatmapsets and their 7K difficulties.

Every difficulty keeps its `.osu` content (compressed), so when the engine changes (its version token
moves, see `engine.version`) the whole catalog is re-evaluated from the database alone with
`reevaluate_stale`, without downloading anything again.

Search follows osu!'s listing: rows are beatmapsets; a set is listed when one of its difficulties matches
the query; sorting by a per-difficulty value uses the matching difficulties' highest value for a
descending sort and their lowest for an ascending one; charts with no value for the key go last.
"""

import json
import math
import sqlite3
import threading
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from proj7k.catalog.charts import ChartRow, chart_row
from proj7k.catalog.query import STATUSES, SearchQuery, parse_query
from proj7k.dan import CANONICAL_DAN_TIERS
from proj7k.engine import engine_version

SCHEMA = """
CREATE TABLE IF NOT EXISTS beatmapsets (
    id              INTEGER PRIMARY KEY,   -- osu!'s set id; negative for a set osu! has not given one
    title           TEXT NOT NULL,
    title_unicode   TEXT NOT NULL,
    artist          TEXT NOT NULL,
    artist_unicode  TEXT NOT NULL,
    creator         TEXT NOT NULL,
    source          TEXT NOT NULL,
    tags            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'unknown',
    ranked_date     TEXT,
    updated_at      TEXT NOT NULL,
    -- casefolded copies for search
    search_text     TEXT NOT NULL,
    title_search    TEXT NOT NULL,
    artist_search   TEXT NOT NULL,
    creator_search  TEXT NOT NULL,
    source_search   TEXT NOT NULL,
    tags_search     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS beatmaps (
    id                  INTEGER PRIMARY KEY,   -- osu!'s beatmap id; negative when it has none
    beatmapset_id       INTEGER NOT NULL REFERENCES beatmapsets(id) ON DELETE CASCADE,
    checksum            TEXT NOT NULL,
    version             TEXT NOT NULL,
    version_search      TEXT NOT NULL,
    official_sr         REAL,
    official_sr_source  TEXT,                  -- osu-api | manifest | lazer
    engine_sr           REAL NOT NULL,
    engine_version      TEXT NOT NULL,
    dan                 TEXT NOT NULL,
    dan_index           INTEGER NOT NULL,
    dominant_skill      TEXT NOT NULL,
    dominance_margin    REAL NOT NULL,
    skills_json         TEXT NOT NULL,
    bpm                 REAL NOT NULL,
    length_s            REAL NOT NULL,
    note_count          INTEGER NOT NULL,
    ln_count            INTEGER NOT NULL,
    od                  REAL NOT NULL,
    hp                  REAL NOT NULL,
    density_json        TEXT NOT NULL,
    osu_blob            BLOB NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS beatmaps_set ON beatmaps(beatmapset_id);
CREATE UNIQUE INDEX IF NOT EXISTS beatmaps_checksum ON beatmaps(checksum);
CREATE INDEX IF NOT EXISTS beatmaps_engine ON beatmaps(engine_sr);
CREATE INDEX IF NOT EXISTS beatmaps_official ON beatmaps(official_sr);
"""

#: Sort keys of the listing: the SQL value each difficulty contributes.
SORTS: Dict[str, str] = {
    "newest": "s.id",
    "title": "s.title_search",
    "artist": "s.artist_search",
    "ranked": "s.ranked_date",
    "official": "b.official_sr",
    "engine": "b.engine_sr",
    "delta": "(b.engine_sr - b.official_sr)",
    "bpm": "b.bpm",
    "length": "b.length_s",
}
DEFAULT_SORT = "newest_desc"
PAGE_SIZE = 50

_SET_FIELDS = ("id", "title", "title_unicode", "artist", "artist_unicode", "creator", "source", "tags",
               "status", "ranked_date", "updated_at")
_LIST_BEATMAP_FIELDS = ("id", "beatmapset_id", "version", "official_sr", "official_sr_source", "engine_sr",
                        "dan", "dominant_skill", "bpm", "length_s", "note_count", "ln_count", "od", "hp")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fold(text: Optional[str]) -> str:
    return (text or "").casefold()


def parse_sort(sort: Optional[str]) -> Tuple[str, bool]:
    """`engine_desc` -> ("engine", True). Unknown keys fall back to the default."""
    key, _, direction = (sort or DEFAULT_SORT).rpartition("_")
    if key not in SORTS or direction not in ("asc", "desc"):
        key, _, direction = DEFAULT_SORT.rpartition("_")
    return key, direction == "desc"


@dataclass
class SearchResult:
    total: int
    page: int
    pages: int
    sort: str
    filters: List[str]
    beatmapsets: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {"total": self.total, "page": self.page, "pages": self.pages, "sort": self.sort,
                "filters": self.filters, "beatmapsets": self.beatmapsets}


class CatalogStore:
    """The catalog database. Safe to share between threads (one connection, serialised)."""

    def __init__(self, path: Union[str, Path] = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "CatalogStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- writing -------------------------------------------------------------------------------

    def upsert(self, row: ChartRow) -> None:
        """Add or replace a difficulty (and its set's metadata). A missing official rating or status
        never overwrites one already known: sources fill in what they know."""
        now = _now()
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO beatmapsets (id, title, title_unicode, artist, artist_unicode, creator, source, tags,
                       status, ranked_date, updated_at, search_text, title_search, artist_search, creator_search,
                       source_search, tags_search)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       title=excluded.title, title_unicode=excluded.title_unicode, artist=excluded.artist,
                       artist_unicode=excluded.artist_unicode, creator=excluded.creator, source=excluded.source,
                       tags=excluded.tags,
                       status=CASE WHEN excluded.status = 'unknown' THEN status ELSE excluded.status END,
                       ranked_date=COALESCE(excluded.ranked_date, ranked_date),
                       updated_at=excluded.updated_at, search_text=excluded.search_text,
                       title_search=excluded.title_search, artist_search=excluded.artist_search,
                       creator_search=excluded.creator_search, source_search=excluded.source_search,
                       tags_search=excluded.tags_search""",
                (
                    row.beatmapset_id, row.title, row.title_unicode, row.artist, row.artist_unicode, row.creator,
                    row.source, row.tags, row.status if row.status in STATUSES else "unknown", row.ranked_date, now,
                    _fold(" ".join((row.title, row.title_unicode, row.artist, row.artist_unicode, row.creator,
                                    row.source, row.tags))),
                    _fold(f"{row.title} {row.title_unicode}"), _fold(f"{row.artist} {row.artist_unicode}"),
                    _fold(row.creator), _fold(row.source), _fold(row.tags),
                ),
            )
            # The same chart (same MD5) under another id is the same difficulty: keep the newest id.
            self._db.execute("DELETE FROM beatmaps WHERE checksum = ? AND id != ?", (row.checksum, row.beatmap_id))
            self._db.execute(
                """INSERT INTO beatmaps (id, beatmapset_id, checksum, version, version_search, official_sr,
                       official_sr_source, engine_sr, engine_version, dan, dan_index, dominant_skill,
                       dominance_margin, skills_json, bpm, length_s, note_count, ln_count, od, hp, density_json,
                       osu_blob, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       beatmapset_id=excluded.beatmapset_id, checksum=excluded.checksum, version=excluded.version,
                       version_search=excluded.version_search,
                       official_sr=COALESCE(excluded.official_sr, official_sr),
                       official_sr_source=CASE WHEN excluded.official_sr IS NULL THEN official_sr_source
                                               ELSE excluded.official_sr_source END,
                       engine_sr=excluded.engine_sr, engine_version=excluded.engine_version, dan=excluded.dan,
                       dan_index=excluded.dan_index, dominant_skill=excluded.dominant_skill,
                       dominance_margin=excluded.dominance_margin, skills_json=excluded.skills_json,
                       bpm=excluded.bpm, length_s=excluded.length_s, note_count=excluded.note_count,
                       ln_count=excluded.ln_count, od=excluded.od, hp=excluded.hp,
                       density_json=excluded.density_json, osu_blob=excluded.osu_blob,
                       updated_at=excluded.updated_at""",
                (
                    row.beatmap_id, row.beatmapset_id, row.checksum, row.version, _fold(row.version),
                    row.official_sr, row.official_sr_source, row.engine_sr, row.engine_version, row.dan,
                    CANONICAL_DAN_TIERS.index(row.dan), row.dominant_skill, row.dominance_margin,
                    json.dumps(row.skills, separators=(",", ":")), row.bpm, row.length_s, row.note_count,
                    row.ln_count, row.od, row.hp, json.dumps(row.density, separators=(",", ":")),
                    zlib.compress(row.osu_content.encode("utf-8"), 9), now,
                ),
            )
            self._db.execute(
                "DELETE FROM beatmapsets WHERE id NOT IN (SELECT DISTINCT beatmapset_id FROM beatmaps)"
            )

    def set_official(self, beatmap_id: int, official_sr: float, source: str) -> bool:
        """Record a difficulty's official star rating; False when the catalog does not have it."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE beatmaps SET official_sr = ?, official_sr_source = ? WHERE id = ?",
                (official_sr, source, beatmap_id),
            )
            return cur.rowcount > 0

    def set_status(self, set_id: int, status: str, ranked_date: Optional[str] = None) -> None:
        if status not in STATUSES:
            return
        with self._lock, self._db:
            self._db.execute("UPDATE beatmapsets SET status = ?, ranked_date = COALESCE(?, ranked_date) WHERE id = ?",
                             (status, ranked_date, set_id))

    def checksums(self) -> List[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT checksum FROM beatmaps")]

    def stale(self) -> List[int]:
        """Difficulties evaluated by another engine version than the running one."""
        with self._lock:
            return [r[0] for r in self._db.execute(
                "SELECT id FROM beatmaps WHERE engine_version != ?", (engine_version(),))]

    def reevaluate_stale(self, progress: Optional[Callable[[int, int], None]] = None) -> int:
        """Re-run the engine on every stale difficulty from its stored `.osu`. Returns how many."""
        ids = self.stale()
        for n, beatmap_id in enumerate(ids, 1):
            with self._lock:
                rec = self._db.execute(
                    """SELECT b.osu_blob, b.official_sr, b.official_sr_source, s.status, s.ranked_date
                       FROM beatmaps b JOIN beatmapsets s ON s.id = b.beatmapset_id WHERE b.id = ?""",
                    (beatmap_id,),
                ).fetchone()
            row = chart_row(zlib.decompress(rec["osu_blob"]).decode("utf-8"), rec["official_sr"],
                            rec["official_sr_source"], rec["status"], rec["ranked_date"])
            row.beatmap_id = beatmap_id
            self.upsert(row)
            if progress:
                progress(n, len(ids))
        return len(ids)

    # ---- reading -------------------------------------------------------------------------------

    def osu_content(self, beatmap_id: int) -> Optional[str]:
        with self._lock:
            rec = self._db.execute("SELECT osu_blob FROM beatmaps WHERE id = ?", (beatmap_id,)).fetchone()
        return zlib.decompress(rec[0]).decode("utf-8") if rec else None

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            sets = self._db.execute("SELECT COUNT(*) FROM beatmapsets").fetchone()[0]
            maps, with_official = self._db.execute(
                "SELECT COUNT(*), COUNT(official_sr) FROM beatmaps").fetchone()
        return {"beatmapsets": sets, "beatmaps": maps, "with_official_sr": with_official,
                "engine_version": engine_version(), "stale": len(self.stale())}

    def search(
        self,
        q: str = "",
        status: Optional[str] = None,
        skill: Optional[str] = None,
        sort: Optional[str] = None,
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> SearchResult:
        """One page of beatmapsets matching `q` (the search box) plus the listing's own filter buttons."""
        query: SearchQuery = parse_query(q)
        if status and status != "any":
            query = _merge(query, parse_query(f"status={status}"))
        if skill and skill != "any":
            query = _merge(query, parse_query(f"skill={skill}"))
        key, desc = parse_sort(sort)
        expr = SORTS[key]
        where, params = query.where()

        with self._lock:
            rows = self._db.execute(
                f"SELECT b.id, b.beatmapset_id, {expr} AS k FROM beatmaps b "
                f"JOIN beatmapsets s ON s.id = b.beatmapset_id WHERE {where}",
                params,
            ).fetchall()

        matched: Dict[int, List[int]] = {}
        keys: Dict[int, Any] = {}
        for beatmap_id, set_id, k in rows:
            matched.setdefault(set_id, []).append(beatmap_id)
            if k is None:
                continue
            if set_id not in keys or (k > keys[set_id] if desc else k < keys[set_id]):
                keys[set_id] = k

        have = sorted((s for s in matched if s in keys), key=lambda s: (keys[s], s), reverse=desc)
        ordered = have + sorted((s for s in matched if s not in keys), reverse=True)

        page_size = max(1, min(page_size, 200))
        pages = max(1, math.ceil(len(ordered) / page_size))
        page = max(1, min(page, pages))
        chosen = ordered[(page - 1) * page_size: page * page_size]
        sets = self._load_sets(chosen, {b for s in chosen for b in matched[s]})
        return SearchResult(total=len(ordered), page=page, pages=pages, sort=f"{key}_{'desc' if desc else 'asc'}",
                            filters=query.filters, beatmapsets=sets)

    def beatmapset(self, set_id: int) -> Optional[Dict[str, Any]]:
        """A set with every difficulty in full (skills and density outline), for its own page."""
        sets = self._load_sets([set_id], set(), full=True)
        return sets[0] if sets else None

    def beatmapset_of(self, beatmap_id: int) -> Optional[int]:
        with self._lock:
            rec = self._db.execute("SELECT beatmapset_id FROM beatmaps WHERE id = ?", (beatmap_id,)).fetchone()
        return rec[0] if rec else None

    def _load_sets(self, set_ids: List[int], matched: Iterable[int], full: bool = False) -> List[Dict[str, Any]]:
        if not set_ids:
            return []
        matched = set(matched)
        marks = ",".join("?" * len(set_ids))
        fields = _LIST_BEATMAP_FIELDS + (("skills_json", "density_json", "dominance_margin", "engine_version",
                                          "checksum") if full else ("skills_json",))
        with self._lock:
            sets = {r["id"]: {k: r[k] for k in _SET_FIELDS} for r in self._db.execute(
                f"SELECT {','.join(_SET_FIELDS)} FROM beatmapsets WHERE id IN ({marks})", set_ids)}
            maps = self._db.execute(
                f"SELECT {','.join(fields)} FROM beatmaps WHERE beatmapset_id IN ({marks})", set_ids).fetchall()
        for s in sets.values():
            s["beatmaps"] = []
        for r in maps:
            b = {k: r[k] for k in fields if not k.endswith("_json")}
            b["skills"] = json.loads(r["skills_json"])
            if full:
                b["density"] = json.loads(r["density_json"])
            else:
                b["skills"] = {k: round(v["stars"], 2) for k, v in b["skills"].items()}
            b["matched"] = r["id"] in matched
            sets[r["beatmapset_id"]]["beatmaps"].append(b)
        for s in sets.values():
            s["beatmaps"].sort(key=lambda b: (b["official_sr"] if b["official_sr"] is not None else b["engine_sr"],
                                              b["engine_sr"]))
        return [sets[i] for i in set_ids if i in sets]


def _merge(a: SearchQuery, b: SearchQuery) -> SearchQuery:
    return SearchQuery(words=a.words + b.words, conditions=a.conditions + b.conditions, filters=a.filters + b.filters)
