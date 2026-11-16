"""
The signed-in player's top plays and 7K total in engine pp (ADR-0061).

Two sources, merged:

- **osu!** — the account's best mania scores (`OsuAccount.best_scores`, at most 200, ranked by osu!'s own pp), kept
  when the chart is a 7K mania chart (not a convert). The chart file is the library's copy when osu!lazer has it,
  else downloaded once from osu! into `<cache>/osu_files/<beatmap id>.osu`.
- **this machine** — the passed 7K plays osu!lazer keeps for the same account (by user id, else by name), which reach
  charts osu!'s best 200 leave out: a chart osu! rates low but the engine rates high never makes osu!'s list.

Each play is scored with `proj7k.enginepp.play_pp` at the engine's stars of the chart as played (rate mods
included). One play per chart counts, the one worth most. By default only ranked and approved charts count (what
osu! pays pp for); `scope="all"` also counts loved, qualified, unranked and local charts.

The work (downloads, an engine pass per new chart) runs in a background thread; `status()` answers at once with the
last result and whether a newer one is being worked out.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from proj7k import enginepp
from proj7k.dan import estimate_canonical_dan
from proj7k.profiler.mods import UNSUPPORTED_ACRONYMS, resolve_play_mods

logger = logging.getLogger("proj7k.dashboard.topplays")

OSU_WEB = "https://osu.ppy.sh"
#: osu!lazer's `BeatmapOnlineStatus` values that earn pp (Ranked, Approved), and osu! API's names for them.
PP_STATUSES = frozenset({1, 2})
PP_STATUS_NAMES = frozenset({"ranked", "approved"})
#: A result older than this is worked out again when a page asks.
STALE_S = 600.0

_LEGACY_STATS = {"count_geki": "perfect", "count_300": "great", "count_katu": "good", "count_100": "ok",
                 "count_50": "meh", "count_miss": "miss"}


def api_judgements(stats: Dict[str, Any]) -> Dict[str, int]:
    """osu!mania's six judgements from an API score, in lazer's names or the legacy count_* ones."""
    out = {k: 0 for k in ("perfect", "great", "good", "ok", "meh", "miss")}
    for k, v in (stats or {}).items():
        name = _LEGACY_STATS.get(k, str(k).lower())
        if name in out and isinstance(v, (int, float)):
            out[name] += int(v)
    return out


def _api_mods(raw: Any) -> List[Dict[str, Any]]:
    return [m if isinstance(m, dict) else {"acronym": str(m)} for m in raw or []]


def is_7k_mania(score: Dict[str, Any]) -> bool:
    b = score.get("beatmap") or {}
    mode_ok = b.get("mode") == "mania" or b.get("mode_int") == 3
    return mode_ok and not b.get("convert") and abs(float(b.get("cs") or 0) - 7) < 1e-6


def engine_stars(stars: Any, key: str, path: Path, rate: float, chart: Optional[Dict[str, Any]]) -> Optional[float]:
    """The engine's total stars of the chart as played: at its rate, else the library's current reading or a fresh one."""
    if abs(rate - 1.0) > 1e-3:
        value = stars.rated(key, path, rate)
        return float(value) if value is not None else None
    if chart and chart.get("stars_source") == "engine" and (chart.get("stars") or 0) > 0:
        return float(chart["stars"])
    entry = stars.evaluate(key, path)
    return float(entry["stars"]) if entry else None


class TopPlays:
    """The engine-pp report for the signed-in account, worked out in the background and kept in memory."""

    def __init__(self, account: Any, library: Any, cache_dir: Optional[Path] = None,
                 clock: Callable[[], float] = time.time, background: bool = True) -> None:
        self.account = account
        self.library = library
        self.osu_dir = Path(cache_dir) / "osu_files" if cache_dir else None
        self.clock = clock
        self.background = background
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._result: Optional[Dict[str, Any]] = None
        self._progress = {"done": 0, "total": 0}

    # --- asking -----------------------------------------------------------------------------------------------

    def status(self, scope: str = "ranked", refresh: bool = False) -> Dict[str, Any]:
        account = self.account.status()
        if not account.get("signed_in"):
            return {"signed_in": False, "login_url": account.get("login_url")}
        user = account.get("user") or {}
        with self._lock:
            result = self._result
            if result is not None and result.get("user_id") != user.get("id"):
                result = self._result = None    # someone else signed in
            running = self._worker is not None and self._worker.is_alive()
            stale = result is None or refresh or self.clock() - result["computed_at"] > STALE_S
            if stale and not running:
                self._progress = {"done": 0, "total": 0}
                if self.background:
                    self._worker = threading.Thread(target=self._run, name="proj7k-engine-pp", daemon=True)
                    self._worker.start()
                    running = True
            progress = dict(self._progress)
        if stale and not self.background:
            self._run()
            with self._lock:
                result, running = self._result, False
        profile = account.get("profile") or {}
        official_7k = ((profile.get("variants") or {}).get("7k") or {}).get("pp")
        out: Dict[str, Any] = {"signed_in": True, "user": user, "running": running, "progress": progress,
                               "official_7k_pp": official_7k, "scope": scope}
        if result is not None:
            out.update(summarize(result, scope))
        return out

    # --- working it out ---------------------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            result = self.compute()
        except Exception as e:
            logger.warning(f"Engine pp could not be worked out: {e}", exc_info=True)
            result = {"user_id": self.account.user().get("id"), "computed_at": self.clock(), "plays": [],
                      "notes": {}, "error": str(e)}
        with self._lock:
            self._result = result

    def _tick(self, total: Optional[int] = None) -> None:
        with self._lock:
            if total is not None:
                self._progress["total"] = total
            else:
                self._progress["done"] += 1

    def compute(self) -> Dict[str, Any]:
        user = self.account.user()
        user_id, username = user.get("id"), str(user.get("username") or "")
        notes: Dict[str, Any] = {"online_scores": 0, "online_7k": 0, "online_unread": 0, "local_plays": 0,
                                 "skipped_mods": 0, "online_error": None}
        snapshot = self.library.snapshot()
        stars = self.library.stars
        files_dir = self.library.files_dir

        try:
            online = self.account.best_scores()
        except Exception as e:
            logger.info(f"osu! best scores unavailable: {e}")
            online, notes["online_error"] = [], str(e)
        notes["online_scores"] = len(online)
        online = [s for s in online if is_7k_mania(s)]
        notes["online_7k"] = len(online)

        def mine(p: Dict[str, Any]) -> bool:
            if user_id and p.get("user_id") not in (None, -1, 0):
                return int(p["user_id"]) == int(user_id)
            return bool(username) and p["player_name"].strip().lower() == username.lower()

        local = [p for p in snapshot.plays if p["passed"] and mine(p)]
        self._tick(total=len(online) + len(local))

        plays: List[Dict[str, Any]] = []
        online_ids = set()
        for s in online:
            row = self._online_play(s, snapshot, stars, files_dir)
            self._tick()
            if row is None:
                notes["online_unread"] += 1
                continue
            if row.get("skipped"):
                notes["skipped_mods"] += 1
                continue
            online_ids.add(row["score_id"])
            plays.append(row)

        for p in local:
            self._tick()
            if p.get("online_id") not in (None, -1, 0) and p["online_id"] in online_ids:
                for row in plays:   # the same score, seen from both sides
                    if row.get("score_id") == p["online_id"]:
                        row["play_id"], row["source"] = p["id"], "both"
                continue
            if p.get("unsupported_mods"):
                notes["skipped_mods"] += 1
                continue
            row = self._local_play(p, snapshot, stars, files_dir)
            if row is not None:
                plays.append(row)
                notes["local_plays"] += 1
        stars.save()
        return {"user_id": user_id, "computed_at": self.clock(), "plays": plays, "notes": notes, "error": None}

    def _osu_file(self, score: Dict[str, Any], snapshot: Any, files_dir: Path) -> Optional[tuple]:
        """(key, path, chart) for the score's chart: the library's copy, else osu!'s (downloaded once)."""
        from proj7k.dashboard.player import store_path

        b = score.get("beatmap") or {}
        md5 = b.get("checksum") or ""
        chart = snapshot.charts.get(md5) if md5 else None
        if chart and chart.get("file_hash"):
            path = store_path(files_dir, chart["file_hash"])
            if path.is_file():
                return chart["file_hash"], path, chart
        beatmap_id = b.get("id") or score.get("beatmap_id")
        if not beatmap_id or self.osu_dir is None:
            return None
        path = self.osu_dir / f"{int(beatmap_id)}.osu"
        data = path.read_bytes() if path.is_file() else b""
        if not data or (md5 and hashlib.md5(data).hexdigest() != md5):
            try:
                data = self.account.beatmap_file(int(beatmap_id))
            except Exception as e:
                logger.info(f"Could not download chart {beatmap_id}: {e}")
                return None
            if not data.strip():
                return None
            self.osu_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return f"osu:{hashlib.md5(data).hexdigest()}", path, chart

    def _online_play(self, s: Dict[str, Any], snapshot: Any, stars: Any, files_dir: Path) -> Optional[Dict[str, Any]]:
        mods_raw = _api_mods(s.get("mods"))
        mods = resolve_play_mods(0, mods_raw)
        b, bs = s.get("beatmap") or {}, s.get("beatmapset") or {}
        if any(a in UNSUPPORTED_ACRONYMS for a in enginepp.acronyms(mods_raw)):
            return {"skipped": True}
        found = self._osu_file(s, snapshot, files_dir)
        if found is None:
            return None
        key, path, chart = found
        value = engine_stars(stars, key, path, mods.clock_rate, chart)
        if value is None:
            return None
        judgements = api_judgements(s.get("statistics") or {})
        legacy = True if s.get("legacy_score_id") else None
        status = b.get("status")
        return {
            "source": "online",
            "score_id": s.get("id"),
            "play_id": None,
            "md5": b.get("checksum") or key,
            "beatmap_id": b.get("id"),
            "set_id": b.get("beatmapset_id") or bs.get("id"),
            "title": bs.get("title") or (chart or {}).get("title", ""),
            "title_unicode": bs.get("title_unicode") or bs.get("title") or (chart or {}).get("title_unicode", ""),
            "artist": bs.get("artist") or (chart or {}).get("artist", ""),
            "version": b.get("version") or (chart or {}).get("version", ""),
            "creator": bs.get("creator") or (chart or {}).get("creator", ""),
            "has_cover": bool(chart and chart.get("has_cover")),
            "in_library": chart is not None,
            "mods": mods.acronyms,
            "accuracy": float(s.get("accuracy") or 0.0),
            "rank_label": {"X": "SS", "XH": "SS", "SH": "S"}.get(s.get("rank"), s.get("rank") or ""),
            "date": s.get("ended_at") or s.get("created_at") or "",
            "stars": round(float(value), 2),
            "dan": estimate_canonical_dan(float(value)),
            "official_sr": b.get("difficulty_rating"),
            "official_pp": s.get("pp"),
            "pp": enginepp.play_pp(float(value), judgements, mods_raw, legacy),
            "eligible": status in PP_STATUS_NAMES if status is not None else True,
        }

    def _local_play(self, p: Dict[str, Any], snapshot: Any, stars: Any, files_dir: Path) -> Optional[Dict[str, Any]]:
        from proj7k.dashboard.player import store_path

        chart = snapshot.charts.get(p["md5"]) or {}
        path = store_path(files_dir, p["file_hash"]) if p.get("file_hash") else None
        if path is None or not path.is_file():
            return None
        value = engine_stars(stars, p["file_hash"], path, p["clock_rate"], chart)
        if value is None:
            return None
        return {
            "source": "local",
            "score_id": p.get("online_id") if p.get("online_id") not in (None, -1, 0) else None,
            "play_id": p["id"],
            "md5": p["md5"],
            "beatmap_id": chart.get("online_id") if (chart.get("online_id") or -1) > 0 else None,
            "set_id": chart.get("set_id") if (chart.get("set_id") or -1) > 0 else None,
            "title": chart.get("title", ""),
            "title_unicode": chart.get("title_unicode", ""),
            "artist": chart.get("artist", ""),
            "version": chart.get("version", ""),
            "creator": chart.get("creator", ""),
            "has_cover": bool(chart.get("has_cover")),
            "in_library": True,
            "mods": p["mods"],
            "accuracy": p["accuracy"],
            "rank_label": p["rank_label"],
            "date": p["date"],
            "stars": round(value, 2),
            "dan": estimate_canonical_dan(value),
            "official_sr": None,
            "official_pp": p.get("pp"),
            "pp": enginepp.play_pp(value, p["judgements"], p["mods"]),
            "eligible": chart.get("status") in PP_STATUSES,
        }


def summarize(result: Dict[str, Any], scope: str = "ranked") -> Dict[str, Any]:
    """The page's view of a result: the top 100 (one play per chart) with each play's weight, and the total."""
    plays = [p for p in result["plays"] if scope == "all" or p["eligible"]]
    best = enginepp.best_per_chart(plays)
    top = best[:enginepp.TOP_PLAYS]
    rows = []
    for i, p in enumerate(top):
        weight = enginepp.WEIGHT_DECAY ** i
        rows.append({**p, "pp": round(p["pp"], 2), "url": f"{OSU_WEB}/b/{int(p['beatmap_id'])}" if p.get("beatmap_id") else None, "weight": round(weight, 4), "weighted_pp": round(p["pp"] * weight, 2),
                     "official_pp": round(p["official_pp"], 2) if isinstance(p.get("official_pp"), (int, float)) else None})
    from_sources = {k: sum(1 for p in top if p["source"] == k) for k in ("online", "local", "both")}
    return {
        "computed_at": result["computed_at"],
        "total_pp": round(enginepp.weighted_total(p["pp"] for p in top), 2),
        "plays": rows,
        "charts": len(best),
        "sources": from_sources,
        "notes": result.get("notes") or {},
        "error": result.get("error"),
        "excluded_unranked": sum(1 for p in result["plays"] if not p["eligible"]) if scope != "all" else 0,
    }

