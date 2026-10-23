"""
The player's side of the library: who plays on this machine, what they played, and the charts they have.

ADR-0034. The dashboard's home, history, library and chart pages read osu!lazer's own database (read-only, through
the Node bridge's `dump-player-data`) and join it with what the toolchain already knows:

- a chart's stars, dan and dominant skill are the difficulty engine's: the ones `proj7k.sync` wrote into the library
  when the chart carries a current injection, else evaluated here once per chart file and kept in a small cache file
  (`chart_stars.json`), so a chart imported after the last sync still reads in engine stars;
- a play under a rate mod is shown at the stars of the chart it actually was (the song compressed by the rate);
- a play whose replay the profiler has read carries that snapshot's UR, completion and break point.

Nothing here writes to osu!lazer. The library is re-read when `client.realm` changes, at most every
`min_reload_s` seconds, so a play just set in the game shows up on the next page load.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import io
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from proj7k.dan import CANONICAL_DAN_SR, CANONICAL_DAN_TIERS, estimate_canonical_dan
from proj7k.i18n import tr
from proj7k.lazer.annotator import (
    _normalize_tech_key,
    current_engine_version,
    parse_injected_metadata,
    strip_injected_suffix,
)

logger = logging.getLogger("proj7k.dashboard.player")

#: osu!lazer's ScoreRank, by its stored integer.
RANKS = {-1: "F", 0: "D", 1: "C", 2: "B", 3: "A", 4: "S", 5: "SH", 6: "X", 7: "XH"}
#: How a rank is written on the page: XH/X are SS (silver/gold), SH is a silver S.
RANK_LABELS = {"F": "F", "D": "D", "C": "C", "B": "B", "A": "A", "S": "S", "SH": "S", "X": "SS", "XH": "SS"}

#: osu!mania's judgements, best first, as lazer stores them (snake_case HitResult names) and as players say them.
JUDGEMENTS: Tuple[Tuple[str, str], ...] = (
    ("perfect", "MAX"), ("great", "300"), ("good", "200"), ("ok", "100"), ("meh", "50"), ("miss", "Miss"),
)

#: The online services a chart or player links out to. Nothing here calls them; the page only links.
OSU_WEB = "https://osu.ppy.sh"
CATALOG_WEB = "https://7k.kaiz.dev"
AVATAR_WEB = "https://a.ppy.sh"


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _online_rank(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def online_profile(user: Dict[str, Any]) -> Dict[str, Any]:
    """What the home page shows of osu!'s `GET /me/mania`: overall and per-keycount (4K/7K) rank and pp."""
    stats = user.get("statistics") or {}
    variants = {}
    for v in stats.get("variants") or []:
        if isinstance(v, dict) and v.get("variant"):
            variants[str(v["variant"])] = {"pp": _num(v.get("pp")), "global_rank": _online_rank(v.get("global_rank")),
                                           "country_rank": _online_rank(v.get("country_rank"))}
    level = stats.get("level") or {}
    history = (user.get("rank_history") or {}).get("data") or []
    return {
        "id": int(user.get("id") or 0),
        "username": user.get("username") or "",
        "country_code": user.get("country_code") or (user.get("country") or {}).get("code") or "",
        "mania": {
            "pp": _num(stats.get("pp")), "global_rank": _online_rank(stats.get("global_rank")),
            "country_rank": _online_rank(stats.get("country_rank")), "accuracy": _num(stats.get("hit_accuracy")),
            "play_count": stats.get("play_count"), "level": _num(level.get("current")),
        },
        "variants": variants,
        "rank_history": [int(r) for r in history if isinstance(r, (int, float))],
    }


class OsuAccount:
    """
    The player's osu! account, signed in through the catalog site (ADR-0041), and what osu! says about it.

    Signing in: `login_url()` points the system browser at the site's `/oauth/start`; osu! asks the player, the site
    trades the code for tokens and sends the browser to this dashboard's `/oauth/done` with a one-time handoff code,
    and `finish()` redeems it (checking the nonce this machine started with). Tokens are kept in `token_path`, readable
    only by the player; an expired access token is renewed through the site's `/oauth/refresh`. The rank and pp are
    read from osu! directly with the player's own token, at most once per `ttl_s`.
    """

    NONCE_TTL_S = 900.0

    def __init__(self, token_path: Optional[Path] = None, port: int = 7770, catalog: Optional[str] = None,
                 api: str = OSU_WEB, ttl_s: float = 600.0, error_ttl_s: float = 120.0, timeout_s: float = 10.0,
                 http: Optional[Callable[[str, Dict[str, str], Optional[bytes]], bytes]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        import os

        self.token_path = token_path or Path.home() / ".proj7k" / "osu_account.json"
        self.port = port
        self.catalog = (catalog or os.environ.get("PROJ7K_CATALOG_URL") or CATALOG_WEB).rstrip("/")
        self.api, self.ttl_s, self.error_ttl_s, self.timeout_s = api.rstrip("/"), ttl_s, error_ttl_s, timeout_s
        self.http = http or self._urlopen
        self.clock = clock
        self._nonce: Optional[Tuple[str, float]] = None
        self._profile: Optional[Tuple[float, Dict[str, Any]]] = None
        self._lock = threading.Lock()

    def _urlopen(self, url: str, headers: Dict[str, str], body: Optional[bytes]) -> bytes:
        import urllib.request

        req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET",
                                     headers={"Accept": "application/json", "User-Agent": "proj7k-desktop", **headers})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as res:
            return res.read(4 * 2**20)

    def _post_catalog(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return json.loads(self.http(f"{self.catalog}{path}", {"Content-Type": "application/json"},
                                    json.dumps(data).encode()))

    # --- tokens ---

    def _tokens(self) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("access_token") else None
        except (OSError, ValueError):
            return None

    def _save(self, tokens: Dict[str, Any], user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        import os

        old = self._tokens() or {}
        record = {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token") or old.get("refresh_token"),
            "expires_at": self.clock() + float(tokens.get("expires_in") or 0),
            "user": user if user is not None else old.get("user"),
        }
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f)
        os.replace(tmp, self.token_path)
        return record

    def _access_token(self) -> Optional[str]:
        tokens = self._tokens()
        if tokens is None:
            return None
        if self.clock() < float(tokens.get("expires_at") or 0) - 120:
            return tokens["access_token"]
        if not tokens.get("refresh_token"):
            return None
        return self._save(self._post_catalog("/oauth/refresh", {"refresh_token": tokens["refresh_token"]}))["access_token"]

    # --- signing in and out ---

    def login_url(self) -> str:
        import secrets
        import urllib.parse

        with self._lock:
            if self._nonce is None or self.clock() >= self._nonce[1]:
                self._nonce = (secrets.token_urlsafe(24), self.clock() + self.NONCE_TTL_S)
            nonce = self._nonce[0]
        return f"{self.catalog}/oauth/start?" + urllib.parse.urlencode({"port": self.port, "nonce": nonce})

    def finish(self, nonce: str, handoff: Optional[str], error: Optional[str] = None) -> Optional[str]:
        """Complete a sign-in osu! sent back here; None when it worked, else what went wrong."""
        import hmac

        with self._lock:
            expected = self._nonce
            ok = expected is not None and self.clock() < expected[1] and hmac.compare_digest(expected[0], nonce or "")
            if ok:
                self._nonce = None
        if not ok:
            return tr("登录已过期或不是从这台电脑发起的，请回到 proj7k 重新点登录。",
                      "This sign-in expired or wasn't started on this computer. Sign in again from proj7k.")
        if error or not handoff:
            return tr("osu! 没有授权（{error}）。", "osu! didn't authorize ({error}).").format(error=error or "denied")
        try:
            tokens = self._post_catalog("/oauth/redeem", {"handoff": handoff})
            self._save(tokens)
            user = self._fetch_me()
            self._save(tokens, user={"id": user.get("id"), "username": user.get("username")})
        except Exception as e:
            logger.warning(f"osu! sign-in failed: {e}")
            return tr("登录没有完成：{error}", "Sign-in didn't complete: {error}").format(error=e)
        with self._lock:
            self._profile = (self.clock() + self.ttl_s, online_profile(user))
        return None

    def logout(self) -> None:
        with self._lock:
            self._profile = None
        try:
            self.token_path.unlink()
        except FileNotFoundError:
            pass

    # --- what osu! says ---

    def _fetch_me(self) -> Dict[str, Any]:
        token = self._access_token()
        if token is None:
            raise PermissionError("not signed in")
        return json.loads(self.http(f"{self.api}/api/v2/me/mania", {"Authorization": f"Bearer {token}"}, None))

    def status(self) -> Dict[str, Any]:
        """`{"signed_in": False, "login_url"}`, or `{"signed_in": True, "user", "profile"}` (profile None offline)."""
        tokens = self._tokens()
        if tokens is None:
            return {"signed_in": False, "login_url": self.login_url()}
        now = self.clock()
        with self._lock:
            hit = self._profile
        if hit is None or now >= hit[0]:
            try:
                hit = (now + self.ttl_s, online_profile(self._fetch_me()))
            except Exception as e:
                if getattr(e, "code", None) == 401 or isinstance(e, PermissionError):
                    self.logout()   # the player revoked the app on osu!, or the refresh token is gone
                    return {"signed_in": False, "login_url": self.login_url()}
                logger.info(f"osu! profile unavailable: {e}")
                hit = (now + self.error_ttl_s, {})
            with self._lock:
                self._profile = hit
        return {"signed_in": True, "user": tokens.get("user") or {}, "profile": hit[1] or None}


def store_path(files_dir: Path, file_hash: str) -> Path:
    """A file in osu!lazer's content-addressed store."""
    return files_dir / file_hash[:1] / file_hash[:2] / file_hash


def _judgements(stats: Dict[str, Any]) -> Dict[str, int]:
    flat = {str(k).lower().replace("_", ""): v for k, v in (stats or {}).items()}
    return {key: int(flat.get(key.replace("_", ""), 0) or 0) for key, _ in JUDGEMENTS}


def _parse_date(text: str) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# --- engine stars ----------------------------------------------------------------------------------------------


class ChartStars:
    """
    The engine's reading of chart files the library has not been synced with, keyed by the file's store hash and
    stamped with the engine version, persisted as one small JSON file. Rate-adjusted readings (a play under DT/HT) are
    kept in memory only.
    """

    def __init__(self, path: Optional[Path]):
        self.path = path
        self.version = current_engine_version()
        self._lock = threading.Lock()
        self._rated: Dict[Tuple[str, float], float] = {}
        self._entries: Dict[str, Dict[str, Any]] = {}
        if path is not None and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == self.version:
                    self._entries = dict(data.get("charts") or {})
            except (OSError, ValueError):
                logger.warning(f"Ignoring an unreadable chart star cache at {path}")

    def get(self, file_hash: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._entries.get(file_hash)

    def evaluate(self, file_hash: str, osu_path: Path) -> Optional[Dict[str, Any]]:
        from proj7k.engine.evaluate import evaluate_osu
        from proj7k.engine.skills import SKILL_TECH_KEY

        cached = self.get(file_hash)
        if cached is not None:
            return cached
        try:
            profile = evaluate_osu(osu_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            logger.info(f"Engine could not read {osu_path.name}: {e}")
            return None
        entry = {"stars": round(float(profile.total_stars), 3), "dominant": SKILL_TECH_KEY[profile.dominant_skill]}
        with self._lock:
            self._entries[file_hash] = entry
        return entry

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            payload = {"version": self.version, "charts": dict(self._entries)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            logger.warning(f"Could not write the chart star cache {self.path}: {e}")

    def rated(self, file_hash: str, osu_path: Path, rate: float) -> Optional[float]:
        """The chart's stars played at `rate` (DT 1.5, HT 0.75, a custom speed)."""
        key = (file_hash, round(rate, 3))
        with self._lock:
            if key in self._rated:
                return self._rated[key]
        from proj7k.field import trace_beatmap
        from proj7k.parser import parse_osu_7k
        from proj7k.profiler.cli import _scale_beatmap_clock_rate

        try:
            beatmap = parse_osu_7k(osu_path.read_text(encoding="utf-8", errors="replace"))
            stars = round(float(trace_beatmap(_scale_beatmap_clock_rate(beatmap, rate)).profile.total_stars), 3)
        except Exception as e:
            logger.info(f"Engine could not read {osu_path.name} at {rate}x: {e}")
            return None
        with self._lock:
            self._rated[key] = stars
        return stars


# --- the library snapshot --------------------------------------------------------------------------------------


@dataclass
class LibrarySnapshot:
    """One read of the library: charts by MD5, plays newest first, and what the profiler measured."""
    charts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    plays: List[Dict[str, Any]] = field(default_factory=list)
    loaded_at: float = 0.0
    error: Optional[str] = None

    def plays_of(self, player: Optional[str]) -> List[Dict[str, Any]]:
        if not player:
            return self.plays
        key = player.strip().lower()
        return [p for p in self.plays if p["player_name"].strip().lower() == key]

    def players(self) -> List[Dict[str, Any]]:
        by_name: Dict[str, Dict[str, Any]] = {}
        for p in self.plays:
            entry = by_name.setdefault(p["player_name"], {
                "name": p["player_name"], "user_id": p["user_id"], "country": p["country"], "plays": 0, "last_played": p["date"],
            })
            entry["plays"] += 1
            if entry["user_id"] in (None, -1, 0) and p["user_id"] not in (None, -1, 0):
                entry["user_id"] = p["user_id"]
        return sorted(by_name.values(), key=lambda e: -e["plays"])

    def default_player(self) -> Optional[str]:
        players = self.players()
        return players[0]["name"] if players else None

    def play(self, play_id: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.plays if p["id"] == play_id), None)


def chart_from_record(raw: Dict[str, Any], stars: ChartStars, files_dir: Path) -> Dict[str, Any]:
    """A library chart as the pages show it, in engine stars."""
    name = raw.get("difficulty_name", "")
    injected = parse_injected_metadata(name)
    file_hash = raw.get("file_hash", "")
    stars_value, dominant, source = float(raw.get("star_rating", -1.0)), None, "lazer"
    if injected is not None:
        stars_value, dominant = injected.star_rating, _normalize_tech_key(injected.dominant_tech)
        source = "engine" if injected.matches_version(current_engine_version()) else "engine_old"
    else:
        cached = stars.get(file_hash) if file_hash else None
        if cached is not None:
            stars_value, dominant, source = cached["stars"], cached["dominant"], "engine"
    length_ms = float(raw.get("length_ms") or 0.0)
    return {
        "md5": raw.get("md5_hash", ""),
        "file_hash": file_hash,
        "online_id": int(raw.get("online_id") or -1),
        "set_id": int(raw.get("set_online_id") or -1),
        "title": raw.get("title", ""),
        "title_unicode": raw.get("title_unicode") or raw.get("title", ""),
        "artist": raw.get("artist", ""),
        "artist_unicode": raw.get("artist_unicode") or raw.get("artist", ""),
        "creator": raw.get("creator", ""),
        "version": strip_injected_suffix(name) or name,
        "stars": round(stars_value, 2),
        "stars_source": source,
        "dan": estimate_canonical_dan(stars_value) if stars_value > 0 else None,
        "dominant": dominant,
        "length_s": round(length_ms / 1000.0, 1),
        "bpm": round(float(raw.get("bpm") or 0.0), 1),
        "od": raw.get("od"),
        "hp": raw.get("hp"),
        "status": raw.get("status"),
        "date_added": raw.get("date_added", ""),
        "last_played": raw.get("last_played", ""),
        "preview_ms": raw.get("preview_time", -1),
        "has_cover": bool(raw.get("background_hash")) and store_path(files_dir, raw["background_hash"]).is_file(),
        "has_audio": bool(raw.get("audio_hash")),
        "background_hash": raw.get("background_hash", ""),
        "audio_hash": raw.get("audio_hash", ""),
        "tags": raw.get("tags", ""),
    }


def play_from_score(raw: Dict[str, Any], chart: Dict[str, Any]) -> Dict[str, Any]:
    from proj7k.profiler.mods import resolve_play_mods

    mods = resolve_play_mods(0, raw.get("mods") or [])
    rank = RANKS.get(int(raw.get("rank", -1)), "F")
    judgements = _judgements(raw.get("statistics") or {})
    notes = sum(judgements.values())
    return {
        "id": raw.get("id", ""),
        "online_id": raw.get("online_id", -1),
        "player_name": raw.get("player_name") or "Unknown",
        "user_id": raw.get("user_id", -1),
        "country": raw.get("country", ""),
        "date": raw.get("date", ""),
        "timestamp": _parse_date(raw.get("date", "")),
        "score": int(raw.get("total_score") or 0),
        "accuracy": float(raw.get("accuracy") or 0.0),
        "rank": rank,
        "rank_label": RANK_LABELS[rank],
        "passed": rank != "F",
        "max_combo": int(raw.get("max_combo") or 0),
        "pp": raw.get("pp"),
        "mods": mods.acronyms,
        "clock_rate": mods.clock_rate,
        "unsupported_mods": mods.unsupported,
        "judgements": judgements,
        "notes": notes,
        "replay_hash": raw.get("replay_file_hash", ""),
        "md5": chart["md5"],
        "file_hash": chart["file_hash"],
        # The stars of what was played: the chart's, or the chart under its rate (filled in by the loader).
        "stars": chart["stars"],
        "dan": chart["dan"],
        "profiler": None,
    }


def _attach_snapshots(plays: List[Dict[str, Any]], snapshots: Iterable[Any]) -> None:
    by_replay = {}
    by_chart: Dict[str, List[Any]] = defaultdict(list)
    for s in snapshots:
        if s.replay_hash:
            by_replay[s.replay_hash] = s
        by_chart[s.beatmap_hash].append(s)
    for p in plays:
        snap = by_replay.get(p["replay_hash"]) if p["replay_hash"] else None
        if snap is None:
            snap = next((s for s in by_chart.get(p["md5"], ()) if abs(s.timestamp - p["timestamp"]) < 120), None)
        if snap is None:
            continue
        overall = (snap.summary or {}).get("overall") or {}
        p["profiler"] = {
            "snapshot_id": snap.id,
            "ur": round(snap.overall_ur, 1) if snap.overall_ur is not None else None,
            "completion": round(snap.completion_rate, 4),
            "valid": snap.is_valid_play,
            "failed": snap.is_failed,
            "level_stars": overall.get("star_rating"),
            "fatal_time_ms": snap.fatal_time_ms,
            "break_skill": (snap.summary or {}).get("dominant_break_technique"),
            "skills": {k: v.get("star_rating") for k, v in (snap.capacities or {}).items()},
            "tested": {k: bool(v.get("tested")) for k, v in (snap.capacities or {}).items()},
            "broke": {k: bool(v.get("broke_down")) for k, v in (snap.capacities or {}).items()},
        }


Loader = Callable[[], Dict[str, Any]]


class PlayerLibrary:
    """
    The library as the player pages read it, re-read when osu!lazer's database changes. Thread-safe: the server reads
    it from worker threads, never from its event loop.
    """

    def __init__(
        self,
        realm_path: Path,
        files_dir: Path,
        db_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        loader: Optional[Loader] = None,
        min_reload_s: float = 10.0,
        evaluate_in_background: bool = True,
    ):
        self.realm_path = Path(realm_path)
        self.files_dir = Path(files_dir)
        self.db_path = db_path
        self.min_reload_s = min_reload_s
        self.stars = ChartStars(Path(cache_dir) / "chart_stars.json" if cache_dir else None)
        self._loader = loader or self._bridge_loader
        self._lock = threading.Lock()
        self._snapshot: Optional[LibrarySnapshot] = None
        self._mtime: Optional[float] = None
        self._checked_at = 0.0
        self._evaluate_in_background = evaluate_in_background
        self._evaluator: Optional[threading.Thread] = None

    def _bridge_loader(self) -> Dict[str, Any]:
        from proj7k.lazer.bridge import RealmBridgeClient

        return RealmBridgeClient(default_realm_path=self.realm_path).dump_player_data(realm_path=self.realm_path)

    def _realm_mtime(self) -> Optional[float]:
        try:
            return self.realm_path.stat().st_mtime
        except OSError:
            return None

    def snapshot(self, force: bool = False) -> LibrarySnapshot:
        with self._lock:
            now = time.monotonic()
            if self._snapshot is not None and not force and now - self._checked_at < self.min_reload_s:
                return self._snapshot
            self._checked_at = now
            mtime = self._realm_mtime()
            if self._snapshot is not None and not force and mtime == self._mtime:
                return self._snapshot
            self._snapshot = self._load()
            self._mtime = mtime
            snapshot = self._snapshot
        self._start_evaluator(snapshot)
        return snapshot

    def _load(self) -> LibrarySnapshot:
        if self._loader is self._bridge_loader and not self.realm_path.exists():
            return LibrarySnapshot(loaded_at=time.time(), error=tr(f"没有找到 osu!lazer 数据库：{self.realm_path}", f"osu!lazer database not found: {self.realm_path}"))
        try:
            data = self._loader()
        except Exception as e:
            logger.warning(f"Could not read the osu!lazer library: {e}")
            return LibrarySnapshot(loaded_at=time.time(), error=tr(f"读取 osu!lazer 数据库失败：{e}", f"Failed to read the osu!lazer database: {e}"))

        charts: Dict[str, Dict[str, Any]] = {}
        for raw in data.get("beatmaps") or []:
            chart = chart_from_record(raw, self.stars, self.files_dir)
            if chart["md5"]:
                charts[chart["md5"]] = chart
        plays: List[Dict[str, Any]] = []
        for raw in data.get("scores") or []:
            md5 = raw.get("beatmap_md5") or (raw.get("beatmap") or {}).get("md5_hash", "")
            chart = charts.get(md5)
            if chart is None:
                # A score on a chart no longer in the library (deleted, or an older version of the difficulty).
                chart = chart_from_record(raw.get("beatmap") or {"md5_hash": md5}, self.stars, self.files_dir)
                chart["missing"] = True
                if chart["md5"]:
                    charts[chart["md5"]] = chart
            plays.append(play_from_score(raw, chart))
        plays.sort(key=lambda p: p["timestamp"], reverse=True)

        for p in plays:
            if abs(p["clock_rate"] - 1.0) > 1e-3 and p["file_hash"]:
                osu = store_path(self.files_dir, p["file_hash"])
                rated = self.stars.rated(p["file_hash"], osu, p["clock_rate"]) if osu.is_file() else None
                if rated is not None:
                    p["stars"], p["dan"] = round(rated, 2), estimate_canonical_dan(rated)

        try:
            from proj7k.profiler.storage import ProfilerStorage

            with ProfilerStorage(db_path=self.db_path) as storage:
                snapshots = [s for name in storage.list_players() for s in storage.get_snapshots(name)]
            _attach_snapshots(plays, snapshots)
        except Exception as e:
            logger.info(f"Profiler snapshots not joined: {e}")
        return LibrarySnapshot(charts=charts, plays=plays, loaded_at=time.time())

    def _start_evaluator(self, snapshot: LibrarySnapshot) -> None:
        """Reads charts the library has no engine stars for, played ones first, one at a time in the background."""
        if not self._evaluate_in_background or (self._evaluator is not None and self._evaluator.is_alive()):
            return
        played = Counter(p["md5"] for p in snapshot.plays)
        todo = [c for c in snapshot.charts.values() if c["stars_source"] == "lazer" and c["file_hash"]]
        if not todo:
            return
        todo.sort(key=lambda c: -played.get(c["md5"], 0))

        def run() -> None:
            done = 0
            for chart in todo:
                osu = store_path(self.files_dir, chart["file_hash"])
                if not osu.is_file():
                    continue
                entry = self.stars.evaluate(chart["file_hash"], osu)
                if entry is not None:
                    chart.update(stars=round(entry["stars"], 2), dominant=entry["dominant"], stars_source="engine",
                                 dan=estimate_canonical_dan(entry["stars"]))
                    for p in snapshot.plays:
                        if p["md5"] == chart["md5"] and abs(p["clock_rate"] - 1.0) <= 1e-3:
                            p["stars"], p["dan"] = chart["stars"], chart["dan"]
                    done += 1
                    if done % 20 == 0:
                        self.stars.save()
            if done:
                self.stars.save()
                logger.info(f"Engine stars read for {done} chart(s) the library was not synced with.")

        self._evaluator = threading.Thread(target=run, name="proj7k-chart-stars", daemon=True)
        self._evaluator.start()


# --- what the pages ask for ------------------------------------------------------------------------------------


def chart_public(chart: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in chart.items() if k not in ("background_hash", "audio_hash", "tags")}
    out["links"] = chart_links(chart)
    return out


def chart_links(chart: Dict[str, Any]) -> Dict[str, Optional[str]]:
    set_id, online_id = chart.get("set_id", -1), chart.get("online_id", -1)
    online = set_id is not None and set_id > 0
    return {
        "osu": f"{OSU_WEB}/beatmapsets/{set_id}#mania/{online_id}" if online and online_id > 0 else (
            f"{OSU_WEB}/beatmapsets/{set_id}" if online else None),
        "catalog": f"{CATALOG_WEB}/beatmapsets/{set_id}#{online_id}" if online else None,
    }


def _best_by_chart(plays: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for p in plays:
        cur = best.get(p["md5"])
        if cur is None or (p["score"], p["accuracy"]) > (cur["score"], cur["accuracy"]):
            best[p["md5"]] = p
    return best


def _player_info(snapshot: LibrarySnapshot, player: str) -> Dict[str, Any]:
    info = next((p for p in snapshot.players() if p["name"].lower() == player.lower()), None)
    user_id = info["user_id"] if info else -1
    return {
        "name": info["name"] if info else player,
        "user_id": user_id,
        "country": info["country"] if info else "",
        "avatar": f"/api/me/avatar/{user_id}" if user_id and user_id > 0 else None,
        "profile_url": f"{OSU_WEB}/users/{user_id}/mania" if user_id and user_id > 0 else None,
    }


def overview(snapshot: LibrarySnapshot, player: str, now: Optional[float] = None, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The player's home page: who they are, how much and how well they play, and what changed lately."""
    now = now or time.time()
    plays = snapshot.plays_of(player)
    passed = [p for p in plays if p["passed"]]
    best = _best_by_chart(plays)
    ranks = Counter(p["rank_label"] for p in best.values())
    day = 86400.0
    recent30 = [p for p in plays if now - p["timestamp"] <= 30 * day]
    prev30 = [p for p in plays if 30 * day < now - p["timestamp"] <= 60 * day]

    def median(xs: List[float]) -> Optional[float]:
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None

    play_time = 0.0
    for p in plays:
        chart = snapshot.charts.get(p["md5"]) or {}
        play_time += float(chart.get("length_s") or 0.0) / (p["clock_rate"] or 1.0)

    # Plays per day for the last 26 weeks, oldest first, by the player's local calendar day.
    days: Counter = Counter()
    for p in plays:
        if p["timestamp"] and now - p["timestamp"] <= 182 * day:
            days[datetime.fromtimestamp(p["timestamp"]).strftime("%Y-%m-%d")] += 1

    # Per dan: the charts of that dan played, cleared, and the best accuracy there.
    dans: Dict[str, Dict[str, Any]] = {}
    for p in best.values():
        if not p["dan"]:
            continue
        d = dans.setdefault(p["dan"], {"dan": p["dan"], "charts": 0, "passed": 0, "best_accuracy": 0.0, "a_or_better": 0})
        d["charts"] += 1
        d["passed"] += int(p["passed"])
        d["a_or_better"] += int(p["rank"] in ("A", "S", "SH", "X", "XH"))
        d["best_accuracy"] = max(d["best_accuracy"], p["accuracy"])
    dan_rows = [dans[t] for t in CANONICAL_DAN_TIERS if t in dans]

    # The hardest clears: one play per chart, by the stars of what was played.
    top = sorted((p for p in passed), key=lambda p: (-p["stars"], -p["accuracy"]))
    seen, top_plays = set(), []
    for p in top:
        if p["md5"] in seen:
            continue
        seen.add(p["md5"])
        top_plays.append(p)
        if len(top_plays) == 10:
            break

    def window(ps: List[Dict[str, Any]]) -> Dict[str, Any]:
        cleared = [p for p in ps if p["passed"]]
        return {
            "plays": len(ps),
            "median_accuracy": median([p["accuracy"] for p in ps]),
            "median_stars": median([p["stars"] for p in ps]),
            "best_stars": max((p["stars"] for p in cleared), default=None),
        }

    # Where the player stands: the profiler's fitted level when replays were read, else the median of the hardest
    # clears (a rough stand-in that only sees what was passed).
    level: Optional[Dict[str, Any]] = None
    all_time = (profile or {}).get("all_time")
    if all_time and all_time.get("overall_stars"):
        level = {"stars": all_time["overall_stars"], "source": "profile"}
    elif len(top_plays) >= 3:
        level = {"stars": round(median([p["stars"] for p in top_plays]) or 0.0, 2), "source": "clears"}
    if level is not None:
        level["dan"] = estimate_canonical_dan(level["stars"])

    return {
        "player": _player_info(snapshot, player),
        "level": level,
        "next": _next_steps(snapshot, plays, best, level, now),
        "totals": {
            "plays": len(plays),
            "passed": len(passed),
            "charts": len(best),
            "play_time_s": round(play_time),
            "hits": sum(p["notes"] - p["judgements"]["miss"] for p in plays),
            "max_combo": max((p["max_combo"] for p in plays), default=0),
            "first_play": plays[-1]["date"] if plays else None,
            "last_play": plays[0]["date"] if plays else None,
            "median_accuracy": median([p["accuracy"] for p in plays]),
            "ranks": {k: ranks.get(k, 0) for k in ("SS", "S", "A", "B", "C", "D")},
        },
        "recent": window(recent30),
        "previous": window(prev30),
        "activity": [{"day": k, "plays": v} for k, v in sorted(days.items())],
        "dans": dan_rows,
        "top_plays": [play_row(snapshot, p) for p in top_plays],
        "recent_plays": [play_row(snapshot, p) for p in plays[:12]],
        # Every play's stars and accuracy over time, for the progression chart.
        "progression": [
            {"t": p["timestamp"], "stars": p["stars"], "accuracy": round(p["accuracy"], 4), "rank": p["rank_label"], "id": p["id"]}
            for p in reversed(plays) if p["timestamp"]
        ],
        "profile": profile,
        "dan_stars": [{"name": k, "stars": round(v, 2)} for k, v in CANONICAL_DAN_SR.items()],
        "library_charts": len(snapshot.charts),
    }


def _next_steps(snapshot: LibrarySnapshot, plays: List[Dict[str, Any]], best: Dict[str, Dict[str, Any]],
                level: Optional[Dict[str, Any]], now: float) -> Dict[str, Any]:
    """What to play next around the player's level: charts close to it not yet cleared at A, and the dan above."""
    if level is None:
        return {"almost": [], "next_dan": None}
    stars = level["stars"]
    recent = {p["md5"] for p in plays if now - p["timestamp"] <= 90 * 86400.0}
    almost = [
        b for md5, b in best.items()
        if md5 in recent and b["rank"] in ("F", "D", "C", "B") and stars - 0.75 <= b["stars"] <= stars + 0.5
        and not (snapshot.charts.get(md5) or {}).get("missing")
    ]
    almost.sort(key=lambda b: (abs(b["stars"] - stars), -b["accuracy"]))
    tiers = list(CANONICAL_DAN_TIERS)
    next_dan = None
    if level["dan"] in tiers and tiers.index(level["dan"]) + 1 < len(tiers):
        name = tiers[tiers.index(level["dan"]) + 1]
        charts = [c for c in snapshot.charts.values() if c["dan"] == name and not c.get("missing")]
        next_dan = {
            "dan": name,
            "stars": round(CANONICAL_DAN_SR[name], 2),
            "charts": len(charts),
            "unplayed": sum(1 for c in charts if c["md5"] not in best),
            "cleared": sum(1 for c in charts if c["md5"] in best and best[c["md5"]]["passed"]),
        }
    return {"almost": [play_row(snapshot, b) for b in almost[:4]], "next_dan": next_dan}


def play_row(snapshot: LibrarySnapshot, p: Dict[str, Any]) -> Dict[str, Any]:
    """A play with the chart fields a list needs."""
    chart = snapshot.charts.get(p["md5"]) or {}
    return {
        **{k: v for k, v in p.items() if k != "profiler"},
        "title": chart.get("title", ""),
        "title_unicode": chart.get("title_unicode", ""),
        "artist": chart.get("artist", ""),
        "version": chart.get("version", ""),
        "creator": chart.get("creator", ""),
        "chart_stars": chart.get("stars"),
        "dominant": chart.get("dominant"),
        "has_cover": chart.get("has_cover", False),
        "missing_chart": bool(chart.get("missing")),
        "ur": (p.get("profiler") or {}).get("ur"),
        "analyzed": p.get("profiler") is not None,
    }


def history(snapshot: LibrarySnapshot, player: str) -> Dict[str, Any]:
    return {"player": _player_info(snapshot, player), "plays": [play_row(snapshot, p) for p in snapshot.plays_of(player)]}


def play_detail(snapshot: LibrarySnapshot, play_id: str) -> Optional[Dict[str, Any]]:
    p = snapshot.play(play_id)
    if p is None:
        return None
    chart = snapshot.charts.get(p["md5"]) or {}
    attempts = [play_row(snapshot, q) for q in snapshot.plays_of(p["player_name"]) if q["md5"] == p["md5"]]
    better = sum(1 for q in attempts if q["score"] > p["score"])
    return {
        "play": {**play_row(snapshot, p), "profiler": p.get("profiler")},
        "chart": chart_public(chart) if chart else None,
        "attempts": attempts,
        "personal_best": better == 0,
        "attempt_rank": better + 1,
        "judgement_labels": dict(JUDGEMENTS),
    }


def library(snapshot: LibrarySnapshot, player: Optional[str]) -> Dict[str, Any]:
    plays = snapshot.plays_of(player)
    counts = Counter(p["md5"] for p in plays)
    best = _best_by_chart(plays)
    rows = []
    for c in snapshot.charts.values():
        if c.get("missing"):
            continue
        b = best.get(c["md5"])
        rows.append({
            **{k: v for k, v in chart_public(c).items() if k != "links"},
            "plays": counts.get(c["md5"], 0),
            "best_rank": b["rank_label"] if b else None,
            "best_accuracy": b["accuracy"] if b else None,
            "best_play": b["id"] if b else None,
        })
    rows.sort(key=lambda r: (r["date_added"] or ""), reverse=True)
    return {"charts": rows, "pending_stars": sum(1 for c in snapshot.charts.values() if c["stars_source"] == "lazer")}


def chart_detail(snapshot: LibrarySnapshot, md5: str, player: Optional[str], files_dir: Path, engine: Any = None) -> Optional[Dict[str, Any]]:
    """A chart page: the engine's full reading of it (skills and the difficulty over time) and the player's plays."""
    chart = snapshot.charts.get(md5)
    if chart is None:
        return None
    out: Dict[str, Any] = {"chart": chart_public(chart)}
    osu = store_path(files_dir, chart["file_hash"]) if chart["file_hash"] else None
    if engine is not None and osu is not None and osu.is_file():
        try:
            frame = engine.analyze_file(osu)
            meta = frame.get("metadata") or {}
            out["engine"] = {
                "stars": round(float(frame["star_rating"]), 2),
                "dan": frame.get("dan_tier"),
                "skills": {k: round(float(v), 2) for k, v in (frame.get("radar") or {}).items() if k in _TECH_KEYS},
                "dominant": (frame.get("radar") or {}).get("dominant_technique"),
                "notes": meta.get("total_notes"),
                "hold_pct": meta.get("hold_pct"),
                "duration_s": meta.get("duration_seconds"),
                "avg_nps": meta.get("avg_nps"),
                "timeline": frame.get("timeline"),
            }
        except Exception as e:
            out["engine_error"] = str(e)
    plays = [play_row(snapshot, p) for p in snapshot.plays_of(player) if p["md5"] == md5]
    out["plays"] = plays
    out["best"] = max(plays, key=lambda p: (p["score"], p["accuracy"])) if plays else None
    return out


_TECH_KEYS = ("jack", "tech", "speed", "stream", "ln_general", "ln_tech", "ln_inverse", "ln_release")


def thumbnail(path: Path, width: int) -> Tuple[bytes, str]:
    """A cover scaled to `width` as JPEG (Pillow), or the original file when it cannot be read."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGB")
            if img.width > width:
                img = img.resize((width, max(1, round(img.height * width / img.width))))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=82)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        import mimetypes

        return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/jpeg"
