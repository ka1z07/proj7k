"""
Disaster Recovery Snapshots and State Backup Manager for osu!lazer database ingestion.

Implements SPEC-P2.3-03 / ADR-0009.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional, Sequence, Union

from proj7k.lazer.annotator import ALL_BIAXIAL_COLLECTIONS
from proj7k.lazer.bridge import (
    BatchUpdateResult,
    BeatmapRevertPayload,
    DEFAULT_REALM_PATH,
    LazerBeatmapRecord,
    RealmBridgeClient,
)

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "proj7k"
DEFAULT_BACKUP_STATE_FILE = DEFAULT_CACHE_DIR / "lazer_backup_state.json"



@dataclass(frozen=True)
class BeatmapBackupState:
    """Pre-modification pristine state of a beatmap record."""
    id: str
    original_star_rating: float
    original_difficulty_name: str
    original_tags: str
    recorded_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "original_star_rating": self.original_star_rating,
            "original_difficulty_name": self.original_difficulty_name,
            "original_tags": self.original_tags,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BeatmapBackupState":
        return cls(
            id=str(data.get("id", "")),
            original_star_rating=float(data.get("original_star_rating", -1.0)),
            original_difficulty_name=str(data.get("original_difficulty_name", "")),
            original_tags=str(data.get("original_tags", "")),
            recorded_at=str(data.get("recorded_at", "")),
        )

    def to_revert_payload(self) -> BeatmapRevertPayload:
        return BeatmapRevertPayload(
            id=self.id,
            star_rating=self.original_star_rating,
            difficulty_name=self.original_difficulty_name,
            tags=self.original_tags,
        )



class LazerBackupManager:
    """
    Manages database snapshots and pristine attribute sidecar storage.
    Guarantees non-destructive rollback and prevents overwriting pre-proj7k values.
    """

    def __init__(
        self,
        realm_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        max_snapshots: int = 3,
    ):
        self.realm_path = Path(realm_path or DEFAULT_REALM_PATH)
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.max_snapshots = max(1, int(max_snapshots))
        self.state_file = self.cache_dir / "lazer_backup_state.json"

    def list_snapshots(self) -> List[Path]:
        """List existing snapshot files sorted chronologically (oldest first)."""
        parent = self.realm_path.parent
        prefix = f"{self.realm_path.name}.backup_"
        if not parent.exists():
            return []

        def _snapshot_timestamp(p: Path) -> int:
            try:
                return int(p.name.split("backup_")[-1])
            except (ValueError, IndexError):
                return 0

        candidates = [p for p in parent.iterdir() if p.is_file() and p.name.startswith(prefix)]
        return sorted(candidates, key=lambda p: (_snapshot_timestamp(p), p.name))


    def create_realm_snapshot(self) -> Optional[Path]:
        """
        Create a timestamped copy of client.realm and prune snapshots exceeding max_snapshots.
        Returns the created snapshot path, or None if client.realm does not exist.
        """
        if not self.realm_path.exists():
            return None

        self.realm_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = f"{int(time.time() * 1000)}"
        snapshot_path = self.realm_path.parent / f"{self.realm_path.name}.backup_{timestamp}"

        shutil.copy2(self.realm_path, snapshot_path)

        # Rotate and prune oldest snapshots
        snapshots = self.list_snapshots()
        excess = len(snapshots) - self.max_snapshots
        if excess > 0:
            for old_snapshot in snapshots[:excess]:
                try:
                    old_snapshot.unlink(missing_ok=True)
                except OSError:
                    pass

        return snapshot_path

    def load_backup_states(self) -> Dict[str, BeatmapBackupState]:
        """Load currently saved pristine backup states from disk."""
        if not self.state_file.exists():
            return {}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                return {k: BeatmapBackupState.from_dict(v) for k, v in raw.items()}
        except Exception:
            pass
        return {}

    def record_original_states(self, records: Sequence[LazerBeatmapRecord]) -> int:
        """
        Record the pristine state of incoming beatmaps.
        Employs first-write protection: if a beatmap ID is already recorded,
        its original state is NEVER overwritten.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        states = self.load_backup_states()

        added = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        for rec in records:
            if rec.id not in states:
                states[rec.id] = BeatmapBackupState(
                    id=rec.id,
                    original_star_rating=rec.star_rating,
                    original_difficulty_name=rec.difficulty_name,
                    original_tags=rec.tags,
                    recorded_at=now_iso,
                )
                added += 1

        if added > 0 or not self.state_file.exists():
            payload = {k: v.to_dict() for k, v in states.items()}
            temp_file = self.cache_dir / f"{self.state_file.name}.tmp_{os.getpid()}"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            temp_file.replace(self.state_file)

        return added

    def clear_backup_states(self) -> None:
        """Remove the sidecar backup state file."""
        if self.state_file.exists():
            try:
                self.state_file.unlink(missing_ok=True)
            except OSError:
                pass

    def revert_realm_modifications(
        self,
        bridge_client: RealmBridgeClient,
        auto_setup: bool = True,
    ) -> BatchUpdateResult:
        """
        Perform a full, lossless rollback using the stored pristine backup states.
        Restores original StarRating, DifficultyName, and Tags, and deletes proj7k collections.
        """
        states = self.load_backup_states()
        if not states:
            return BatchUpdateResult(success=True, updated_count=0)

        reverts = [state.to_revert_payload() for state in states.values()]

        result = bridge_client.revert_batch(
            reverts=reverts,
            collections_to_clean=list(ALL_BIAXIAL_COLLECTIONS),
            realm_path=self.realm_path,
            auto_setup=auto_setup,
        )

        if result.success:
            self.clear_backup_states()

        return result
