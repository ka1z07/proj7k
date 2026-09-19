"""
SQLite Persistence and Noise Filtering Layer for 7K Player Replay Profiler (ADR-0012).

Persists lightweight match snapshots isolated by player identity and timestamp,
and discards warmup/retry noise (<30s or <50% completion) while preserving fatal
peak strains from mid-song failed runs.
"""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Union

from proj7k.parser import Beatmap7K
from proj7k.radar import TECHNIQUE_NAMES
from proj7k.strain import compute_8d_strain_timeseries


DOTNET_TICKS_EPOCH_DELTA_S = 62135596800.0  # Seconds between 0001-01-01 and 1970-01-01


def ticks_to_unix_seconds(ticks: int) -> float:
    """
    Converts .NET DateTime 100-nanosecond ticks to Unix epoch seconds.
    If the value looks like a modern Unix timestamp (seconds or ms), adjusts accordingly.
    """
    if ticks <= 0:
        return time.time()
    # If standard .NET ticks (> year 2000 is ~ 6.3e17)
    if ticks > 500_000_000_000_000_000:
        return (ticks / 10_000_000.0) - DOTNET_TICKS_EPOCH_DELTA_S
    # If milliseconds timestamp
    if ticks > 100_000_000_000:
        return ticks / 1000.0
    return float(ticks)


def is_noise_match(duration_s: float, completion_rate: float, is_failed: bool) -> bool:
    """
    Noise Filter (ADR-0012):
    Discards aborted runs with played duration < 30s or completion < 50%,
    while preserving fatal peak strains from Failed matches.
    """
    if is_failed:
        return False
    return (duration_s < 30.0) or (completion_rate < 0.50)


@dataclass
class MatchSnapshot:
    """
    Lightweight, indexable summary of a single play session.
    """
    player_name: str
    timestamp: float                       # Unix epoch timestamp in seconds
    beatmap_hash: str
    replay_hash: str
    play_duration_s: float
    completion_rate: float
    is_valid_play: bool                    # duration >= 30s and completion >= 50%
    is_failed: bool                        # Failed mid-song before clearing
    overall_ur: Optional[float]            # Overall hit variance Unstable Rate (10 * sigma)
    capacities: Dict[str, Dict[str, Any]]  # 8-dim strain capacities and ratings
    fatal_time_ms: Optional[float] = None
    fatal_column: Optional[int] = None
    fatal_peak_strains: Dict[str, float] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "player_name": self.player_name,
            "timestamp": round(self.timestamp, 3),
            "beatmap_hash": self.beatmap_hash,
            "replay_hash": self.replay_hash,
            "play_duration_s": round(self.play_duration_s, 2),
            "completion_rate": round(self.completion_rate, 4),
            "is_valid_play": self.is_valid_play,
            "is_failed": self.is_failed,
            "overall_ur": round(self.overall_ur, 2) if self.overall_ur is not None else None,
            "capacities": self.capacities,
            "fatal_time_ms": round(self.fatal_time_ms, 1) if self.fatal_time_ms is not None else None,
            "fatal_column": self.fatal_column,
            "fatal_peak_strains": {k: round(v, 2) for k, v in self.fatal_peak_strains.items()},
            "summary": self.summary,
            "created_at": round(self.created_at, 3),
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MatchSnapshot":
        return cls(
            id=row["id"],
            player_name=row["player_name"],
            timestamp=float(row["timestamp"]),
            beatmap_hash=row["beatmap_hash"],
            replay_hash=row["replay_hash"] or "",
            play_duration_s=float(row["play_duration_s"]),
            completion_rate=float(row["completion_rate"]),
            is_valid_play=bool(row["is_valid_play"]),
            is_failed=bool(row["is_failed"]),
            overall_ur=float(row["overall_ur"]) if row["overall_ur"] is not None else None,
            capacities=json.loads(row["capacities_json"]),
            fatal_time_ms=float(row["fatal_time_ms"]) if row["fatal_time_ms"] is not None else None,
            fatal_column=int(row["fatal_column"]) if row["fatal_column"] is not None else None,
            fatal_peak_strains=json.loads(row["fatal_peak_strains_json"]),
            summary=json.loads(row["summary_json"]),
            created_at=float(row["created_at"]),
        )


def check_replay_lifebar_failed(life_bar_str: str) -> bool:
    """Checks whether player life bar dropped to 0 in replay."""
    if not life_bar_str:
        return False
    for pt in life_bar_str.split(","):
        parts = pt.split("|")
        if len(parts) >= 2:
            try:
                if float(parts[1]) <= 0.0:
                    return True
            except ValueError:
                pass
    return False


def build_snapshot_from_report(
    report: Any,
    is_failed: Optional[bool] = None,
    beatmap: Optional[Beatmap7K] = None,
) -> MatchSnapshot:
    """
    Transforms a ProfilerIngestionReport into a lightweight MatchSnapshot.
    """
    # 1. Infer is_failed if not explicitly supplied
    if is_failed is None:
        lb = getattr(report, "life_bar", "")
        if check_replay_lifebar_failed(lb):
            is_failed = True
        elif report.pathology and report.pathology.cascade_precursor and report.pathology.cascade_precursor.fatal_time_ms is not None:
            fatal_t = report.pathology.cascade_precursor.fatal_time_ms
            play_end_ms = report.play_duration_s * 1000.0
            # If the play aborted prematurely within 3s of fatal break and had misses
            if report.completion_rate < 0.95 and report.miss_count > 0 and (play_end_ms <= fatal_t + 3000.0):
                is_failed = True
            else:
                is_failed = False
        else:
            is_failed = False

    # 2. Extract fatal point and peak strains
    fatal_time_ms = None
    fatal_col = None
    fatal_strains: Dict[str, float] = {}

    if report.pathology and report.pathology.cascade_precursor:
        fatal_time_ms = report.pathology.cascade_precursor.fatal_time_ms
        fatal_col = report.pathology.cascade_precursor.fatal_column

    if fatal_time_ms is not None:
        if beatmap is not None:
            ts = compute_8d_strain_timeseries(beatmap)
            strains_at_fatal = ts.get_strains_at(fatal_time_ms / 1000.0)
            fatal_strains = {k: round(float(v), 2) for k, v in strains_at_fatal.items()}
        elif report.skill_radar:
            for dim, res in report.skill_radar.dimensions.items():
                fatal_strains[dim] = round(float(res.peak_chart_strain), 2)

    # 3. Extract 8-dim capacities dict
    capacities_dict: Dict[str, Dict[str, Any]] = {}
    if report.skill_radar:
        for dim, res in report.skill_radar.dimensions.items():
            capacities_dict[dim] = {
                "effective_capacity": round(float(res.effective_capacity), 2),
                "star_rating": round(float(res.star_rating), 2),
                "dan_tier": res.dan_tier,
                "tested": res.tested,
                "has_inflection": res.has_inflection,
                "peak_strain": round(float(res.peak_chart_strain), 2),
            }

    # 4. Extract overall UR
    overall_ur = None
    if report.alignment_result and report.alignment_result.aligned_hits:
        valid_offsets = [
            h.offset_ms for h in report.alignment_result.aligned_hits
            if h.offset_ms is not None
        ]
        if valid_offsets:
            import numpy as np
            overall_ur = float(np.std(valid_offsets) * 10.0)

    if overall_ur is None and report.pathology:
        if report.pathology.bimanual:
            tot = report.pathology.bimanual.left_hit_count + report.pathology.bimanual.right_hit_count
            if tot > 0:
                overall_ur = (
                    report.pathology.bimanual.left_ur * report.pathology.bimanual.left_hit_count
                    + report.pathology.bimanual.right_ur * report.pathology.bimanual.right_hit_count
                ) / tot
        elif report.pathology.tracks:
            valid_urs = [t.ur for t in report.pathology.tracks.values() if t.ur > 0]
            if valid_urs:
                overall_ur = sum(valid_urs) / len(valid_urs)

    # 5. Timestamp
    timestamp_sec = ticks_to_unix_seconds(report.timestamp_ticks)

    summary_info = {
        "total_notes": report.total_notes,
        "total_hits": report.total_hits,
        "miss_count": report.miss_count,
        "ghost_tap_count": report.ghost_tap_count,
        "mods": report.mods,
    }
    if report.pathology and report.pathology.cascade_precursor:
        summary_info["dominant_break_technique"] = report.pathology.cascade_precursor.dominant_technique

    return MatchSnapshot(
        player_name=report.player_name,
        timestamp=timestamp_sec,
        beatmap_hash=report.beatmap_hash,
        replay_hash=getattr(report, "replay_hash", "") or f"{report.player_name}_{timestamp_sec}",
        play_duration_s=report.play_duration_s,
        completion_rate=report.completion_rate,
        is_valid_play=report.is_valid_play,
        is_failed=is_failed,
        overall_ur=overall_ur,
        capacities=capacities_dict,
        fatal_time_ms=fatal_time_ms,
        fatal_column=fatal_col,
        fatal_peak_strains=fatal_strains,
        summary=summary_info,
        created_at=time.time(),
    )


class ProfilerStorage:
    """
    SQLite persistence manager for match snapshots, isolated by player.
    """

    def __init__(self, db_path: Optional[Union[Path, str]] = None):
        if db_path is None:
            env_db = os.environ.get("PROJ7K_PROFILER_DB")
            if env_db:
                self.db_path = Path(env_db)
            else:
                self.db_path = Path.home() / ".proj7k" / "profiler.db"
        else:
            self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute("PRAGMA journal_mode = WAL;")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS match_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    beatmap_hash TEXT NOT NULL,
                    replay_hash TEXT,
                    play_duration_s REAL NOT NULL,
                    completion_rate REAL NOT NULL,
                    is_valid_play INTEGER NOT NULL,
                    is_failed INTEGER NOT NULL,
                    overall_ur REAL,
                    capacities_json TEXT NOT NULL,
                    fatal_time_ms REAL,
                    fatal_column INTEGER,
                    fatal_peak_strains_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_player_timestamp
                ON match_snapshots (player_name, timestamp DESC);
            """)

    def save_snapshot(self, snapshot: MatchSnapshot) -> int:
        """
        Inserts a match snapshot directly into SQLite.
        """
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO match_snapshots (
                    player_name, timestamp, beatmap_hash, replay_hash,
                    play_duration_s, completion_rate, is_valid_play, is_failed,
                    overall_ur, capacities_json, fatal_time_ms, fatal_column,
                    fatal_peak_strains_json, summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.player_name,
                    snapshot.timestamp,
                    snapshot.beatmap_hash,
                    snapshot.replay_hash,
                    snapshot.play_duration_s,
                    snapshot.completion_rate,
                    1 if snapshot.is_valid_play else 0,
                    1 if snapshot.is_failed else 0,
                    snapshot.overall_ur,
                    json.dumps(snapshot.capacities),
                    snapshot.fatal_time_ms,
                    snapshot.fatal_column,
                    json.dumps(snapshot.fatal_peak_strains),
                    json.dumps(snapshot.summary),
                    snapshot.created_at,
                ),
            )
            snapshot.id = cur.lastrowid
            return cur.lastrowid

    def save_snapshot_with_filter(self, snapshot: MatchSnapshot) -> Optional[int]:
        """
        Applies noise filtering: discards warmup/retry matches (<30s or <50% completion)
        while preserving fatal peak strains from Failed matches.
        Returns the new snapshot ID if saved, or None if filtered as noise.
        """
        if is_noise_match(snapshot.play_duration_s, snapshot.completion_rate, snapshot.is_failed):
            return None
        return self.save_snapshot(snapshot)

    def save_report_with_filter(
        self,
        report: Any,
        is_failed: Optional[bool] = None,
        beatmap: Optional[Beatmap7K] = None,
    ) -> Optional[MatchSnapshot]:
        """
        Constructs a MatchSnapshot from a ProfilerIngestionReport, applies the noise filter,
        and saves it to SQLite if valid.
        """
        snapshot = build_snapshot_from_report(report, is_failed=is_failed, beatmap=beatmap)
        snapshot_id = self.save_snapshot_with_filter(snapshot)
        if snapshot_id is None:
            return None
        snapshot.id = snapshot_id
        return snapshot

    def get_snapshots(
        self,
        player_name: str,
        since_timestamp: Optional[float] = None,
        until_timestamp: Optional[float] = None,
        include_failed: bool = True,
    ) -> List[MatchSnapshot]:
        """
        Retrieves historical match snapshots for a player within [since_timestamp, until_timestamp].
        Multi-player isolation is enforced via player_name filter.
        """
        query = "SELECT * FROM match_snapshots WHERE player_name = ?"
        params: List[Any] = [player_name]

        if since_timestamp is not None:
            query += " AND timestamp >= ?"
            params.append(since_timestamp)
        if until_timestamp is not None:
            query += " AND timestamp <= ?"
            params.append(until_timestamp)
        if not include_failed:
            query += " AND is_failed = 0"

        query += " ORDER BY timestamp DESC"

        cur = self.conn.execute(query, params)
        return [MatchSnapshot.from_row(row) for row in cur.fetchall()]

    def list_players(self) -> List[str]:
        """
        Lists all distinct player names in the database.
        """
        cur = self.conn.execute("SELECT DISTINCT player_name FROM match_snapshots ORDER BY player_name ASC")
        return [row[0] for row in cur.fetchall()]

    def close(self) -> None:
        """Closes the underlying SQLite connection."""
        self.conn.close()

    def __enter__(self) -> "ProfilerStorage":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
