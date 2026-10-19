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
            return LibrarySnapshot(loaded_at=time.time(), error=f"没有找到 osu!lazer 数据库：{self.realm_path}")
        try:
            data = self._loader()
        except Exception as e:
            logger.warning(f"Could not read the osu!lazer library: {e}")
            return LibrarySnapshot(loaded_at=time.time(), error=f"读取 osu!lazer 数据库失败：{e}")

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

    return {
        "player": _player_info(snapshot, player),
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
