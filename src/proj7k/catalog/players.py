"""
A player's public osu!mania profile, fetched from osu! for the desktop app (ADR-0041).

The desktop app ships without an OAuth secret, so it asks the catalog site instead: the site holds the operator's
OAuth client (client-credentials grant, `public` scope; the same `OSU_CLIENT_ID`/`OSU_CLIENT_SECRET` as the crawler)
and answers `GET /api/players/<user id>` with the few fields the home page shows. Answers are kept for `ttl_s`, so
however often a home page is opened, osu! is asked about one player at most once per that window.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: How long a profile is reused. osu! itself refreshes ranks a few times an hour.
TTL_S = 600.0
#: A lookup osu! answered with an error is retried no sooner than this.
ERROR_TTL_S = 60.0
MAX_ENTRIES = 5000


class PlayerUnavailable(Exception):
    """osu! could not be asked (no OAuth client configured, or osu! failed)."""


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _rank(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def public_profile(user: Dict[str, Any]) -> Dict[str, Any]:
    """The fields of osu!'s `GET /users/{id}/mania` the desktop shows: overall and per-keycount (4K/7K) rank and pp."""
    stats = user.get("statistics") or {}
    variants = {}
    for v in stats.get("variants") or []:
        if isinstance(v, dict) and v.get("variant"):
            variants[str(v["variant"])] = {
                "pp": _num(v.get("pp")), "global_rank": _rank(v.get("global_rank")),
                "country_rank": _rank(v.get("country_rank")),
            }
    level = stats.get("level") or {}
    history = (user.get("rank_history") or {}).get("data") or []
    country = user.get("country") or {}
    return {
        "id": int(user.get("id") or 0),
        "username": user.get("username") or "",
        "country_code": user.get("country_code") or country.get("code") or "",
        "avatar_url": user.get("avatar_url"),
        "mania": {
            "pp": _num(stats.get("pp")),
            "global_rank": _rank(stats.get("global_rank")),
            "country_rank": _rank(stats.get("country_rank")),
            "accuracy": _num(stats.get("hit_accuracy")),
            "play_count": stats.get("play_count"),
            "play_time_s": stats.get("play_time"),
            "level": _num(level.get("current")),
            "level_progress": _num(level.get("progress")),
            "ranked_score": stats.get("ranked_score"),
            "max_combo": stats.get("maximum_combo"),
            "grade_counts": stats.get("grade_counts") or {},
        },
        "variants": variants,
        "rank_history": [int(r) for r in history if isinstance(r, (int, float))],
    }


class PlayerProfiles:
    """Profiles by osu! user id, through `fetch_user(user_id)` (the osu! API call), cached for `ttl_s`."""

    def __init__(self, fetch_user: Optional[Callable[[int], Optional[Dict[str, Any]]]],
                 ttl_s: float = TTL_S, error_ttl_s: float = ERROR_TTL_S, max_entries: int = MAX_ENTRIES,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.fetch_user, self.ttl_s, self.error_ttl_s = fetch_user, ttl_s, error_ttl_s
        self.max_entries, self.clock = max_entries, clock
        self._cache: "OrderedDict[int, Tuple[float, Optional[Dict[str, Any]], Optional[str]]]" = OrderedDict()
        self._lock = threading.Lock()
        self._inflight: Dict[int, threading.Lock] = {}
        # osu! is asked one player at a time (the client spaces its requests); a lookup that would queue longer
        # than `queue_timeout_s` is turned away rather than piling up behind a burst of strangers' ids.
        self._upstream = threading.Lock()
        self.queue_timeout_s = 10.0

    @property
    def available(self) -> bool:
        return self.fetch_user is not None

    def _cached(self, user_id: int) -> Optional[Tuple[float, Optional[Dict[str, Any]], Optional[str]]]:
        with self._lock:
            hit = self._cache.get(user_id)
            if hit and self.clock() < hit[0]:
                self._cache.move_to_end(user_id)
                return hit
            return None

    def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        """The profile, or None when osu! has no such player. Raises `PlayerUnavailable` when osu! can't be asked."""
        if self.fetch_user is None:
            raise PlayerUnavailable("no osu! OAuth client configured")
        hit = self._cached(user_id)
        if hit is not None:
            if hit[2] is not None:
                raise PlayerUnavailable(hit[2])
            return hit[1]
        with self._lock:
            gate = self._inflight.setdefault(user_id, threading.Lock())
        with gate:   # one request to osu! per player at a time; the rest wait for its answer
            hit = self._cached(user_id)
            if hit is None:
                try:
                    if not self._upstream.acquire(timeout=self.queue_timeout_s):
                        raise PlayerUnavailable("busy")
                    try:
                        user = self.fetch_user(user_id)
                    finally:
                        self._upstream.release()
                    hit = (self.clock() + self.ttl_s, public_profile(user) if user else None, None)
                except Exception as e:
                    logger.warning("osu! profile of %s unavailable: %s", user_id, e)
                    hit = (self.clock() + self.error_ttl_s, None, str(e) or type(e).__name__)
                with self._lock:
                    self._cache[user_id] = hit
                    self._cache.move_to_end(user_id)
                    while len(self._cache) > self.max_entries:
                        self._cache.popitem(last=False)
        with self._lock:
            self._inflight.pop(user_id, None)
        if hit[2] is not None:
            raise PlayerUnavailable(hit[2])
        return hit[1]


def osu_user_fetcher(client: Any) -> Callable[[int], Optional[Dict[str, Any]]]:
    """`GET /users/{id}/mania` through an `ingest.OsuApiClient`; None for a player osu! doesn't know."""

    def fetch(user_id: int) -> Optional[Dict[str, Any]]:
        try:
            return client.get(f"users/{user_id}/mania", [("key", "id")], retries=1)
        except Exception as e:
            if getattr(e, "code", None) == 404:
                return None
            raise

    return fetch
