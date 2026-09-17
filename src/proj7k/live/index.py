"""
LazerRealmIndex: In-memory inverted index and file resolver for 7K beatmaps.

SPEC-P2.4-02 / ADR-0010.
"""

from difflib import SequenceMatcher
import logging
from pathlib import Path
import re
from typing import Dict, List, Optional, Sequence, Tuple

from proj7k.lazer.annotator import strip_injected_suffix
from proj7k.lazer.bridge import (
    DEFAULT_REALM_PATH,
    LazerBeatmapRecord,
    RealmBridgeClient,
)
from proj7k.lazer.daemon import DEFAULT_FILES_DIR, resolve_beatmap_file

logger = logging.getLogger("proj7k.live.index")


def normalize_token(s: str) -> str:
    """Normalize string by lowercasing and collapsing whitespace."""
    return " ".join(s.lower().strip().split())


def clean_difficulty(difficulty: str) -> str:
    """
    Clean difficulty name:
    1. Strip injected proj7k suffix, e.g. " (10.74★ Tech)"
    2. Normalize whitespace and lowercase.
    """
    stripped = strip_injected_suffix(difficulty)
    return normalize_token(stripped)


class LazerRealmIndex:
    """
    In-memory inverted index mapping (title, difficulty) -> LazerBeatmapRecord.
    Supports O(1) exact lookup, suffix stripping, fuzzy fallback matching,
    and instantaneous physical file resolution.
    """

    def __init__(
        self,
        records: Optional[Sequence[LazerBeatmapRecord]] = None,
        realm_path: Optional[Path] = None,
        files_dir: Optional[Path] = None,
        bridge_client: Optional[RealmBridgeClient] = None,
    ):
        self._initial_records = list(records) if records is not None else None
        self.realm_path = realm_path or DEFAULT_REALM_PATH
        self.files_dir = files_dir or DEFAULT_FILES_DIR
        self.bridge_client = bridge_client

        self._records: List[LazerBeatmapRecord] = []
        self._title_diff_index: Dict[Tuple[str, str], List[LazerBeatmapRecord]] = {}
        self._artist_title_diff_index: Dict[Tuple[str, str, str], LazerBeatmapRecord] = {}

    @property
    def is_warmed_up(self) -> bool:
        return len(self._records) > 0

    def warmup(self) -> int:
        """
        Loads 7K beatmaps and builds in-memory inverted indices.
        Target performance: <0.5s for 10,000+ beatmaps.
        """
        if self._initial_records is not None:
            raw_records = self._initial_records
        else:
            if not self.realm_path.exists():
                logger.warning(
                    f"osu!lazer database not found at '{self.realm_path}'. "
                    "Running without preloaded Realm index."
                )
                return 0
            client = self.bridge_client or RealmBridgeClient(default_realm_path=self.realm_path)
            raw_records = client.dump_7k_beatmaps(realm_path=self.realm_path)

        # Filter to 7K mania records
        self._records = [r for r in raw_records if r.is_7k_mania]
        self._build_indices(self._records)
        logger.info(f"LazerRealmIndex warmed up with {len(self._records)} 7K beatmaps.")
        return len(self._records)

    def _build_indices(self, records: Sequence[LazerBeatmapRecord]) -> None:
        self._title_diff_index.clear()
        self._artist_title_diff_index.clear()

        for rec in records:
            c_title = normalize_token(rec.title)
            c_diff = clean_difficulty(rec.difficulty_name)
            c_artist = normalize_token(rec.artist)

            # Store in (artist, title, diff) index
            self._artist_title_diff_index[(c_artist, c_title, c_diff)] = rec

            # Store in (title, diff) index
            key = (c_title, c_diff)
            if key not in self._title_diff_index:
                self._title_diff_index[key] = []
            self._title_diff_index[key].append(rec)

    def lookup_exact(
        self,
        title: str,
        difficulty: str,
        artist: Optional[str] = None,
    ) -> Optional[LazerBeatmapRecord]:
        """
        O(1) exact dictionary lookup by title and cleaned difficulty.
        """
        c_title = normalize_token(title)
        c_diff = clean_difficulty(difficulty)

        # Try with artist first if supplied
        if artist:
            c_artist = normalize_token(artist)
            exact_artist_match = self._artist_title_diff_index.get((c_artist, c_title, c_diff))
            if exact_artist_match is not None:
                return exact_artist_match

        # Try without artist
        candidates = self._title_diff_index.get((c_title, c_diff))
        if candidates:
            if artist:
                c_artist = normalize_token(artist)
                for c in candidates:
                    if normalize_token(c.artist) == c_artist:
                        return c
            return candidates[0]

        # Try stripping outer brackets if query diff had brackets and index did not
        if c_diff.startswith("[") and c_diff.endswith("]") and len(c_diff) > 2:
            inner_diff = c_diff[1:-1].strip()
            inner_candidates = self._title_diff_index.get((c_title, inner_diff))
            if inner_candidates:
                return inner_candidates[0]

        return None

    def lookup(
        self,
        title: str,
        difficulty: str,
        artist: Optional[str] = None,
        fuzzy_threshold: float = 0.6,
    ) -> Optional[LazerBeatmapRecord]:
        """
        Primary lookup interface:
        1. Fast O(1) exact lookup.
        2. If miss, fuzzy fallback matching.
        """
        exact = self.lookup_exact(title, difficulty, artist)
        if exact is not None:
            return exact

        return self._lookup_fuzzy(title, difficulty, artist, threshold=fuzzy_threshold)

    def _lookup_fuzzy(
        self,
        title: str,
        difficulty: str,
        artist: Optional[str] = None,
        threshold: float = 0.6,
    ) -> Optional[LazerBeatmapRecord]:
        """
        Fuzzy fallback matching when exact title or diff name varies.
        """
        q_title = normalize_token(title)
        q_diff = clean_difficulty(difficulty)
        q_artist = normalize_token(artist) if artist else ""

        best_record: Optional[LazerBeatmapRecord] = None
        best_score = 0.0

        for rec in self._records:
            r_title = normalize_token(rec.title)
            r_diff = clean_difficulty(rec.difficulty_name)

            title_sim = SequenceMatcher(None, q_title, r_title).ratio()
            diff_sim = SequenceMatcher(None, q_diff, r_diff).ratio()

            if q_artist:
                r_artist = normalize_token(rec.artist)
                artist_sim = SequenceMatcher(None, q_artist, r_artist).ratio()
                score = 0.4 * title_sim + 0.3 * diff_sim + 0.3 * artist_sim
            else:
                score = 0.6 * title_sim + 0.4 * diff_sim

            if score > best_score:
                best_score = score
                best_record = rec

        if best_score >= threshold:
            logger.debug(
                f"Fuzzy match hit: query=({title}, {difficulty}) -> "
                f"found=({best_record.title}, {best_record.difficulty_name}) score={best_score:.3f}"
            )
            return best_record

        return None

    def resolve_file(self, record: LazerBeatmapRecord) -> Optional[Path]:
        """
        Resolves the physical .osu chart file in the lazer files directory.
        """
        return resolve_beatmap_file(self.files_dir, record)
