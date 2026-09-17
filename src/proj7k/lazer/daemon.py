"""
LazerSyncManager and Daemon Coordinator for osu!lazer database ingestion.

Implements SPEC-P2.3-03 / ADR-0009.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("proj7k.lazer.daemon")

from proj7k.cache import TwoLayerCache
from proj7k.difficulty import evaluate_intrinsic_difficulty
from proj7k.lazer.annotator import (
    ALL_BIAXIAL_COLLECTIONS,
    BeatmapCollectionEntry,
    annotate_batch_and_build_collections,
    build_biaxial_collection_map,
    format_injected_difficulty_name,
    inject_binned_skill_tags,
)
from proj7k.lazer.backup import DEFAULT_CACHE_DIR, LazerBackupManager
from proj7k.lazer.bridge import (
    BatchUpdateResult,
    DEFAULT_REALM_PATH,
    LazerBeatmapRecord,
    RealmBridgeClient,
)
from proj7k.lazer.lock import SafeFlushWindow, probe_realm_lock

DEFAULT_FILES_DIR = DEFAULT_REALM_PATH.parent / "files"
DEFAULT_LOCK_PATH = DEFAULT_REALM_PATH.parent / "client.realm.lock"


@dataclass(frozen=True)
class SyncOptions:
    """Configuration options for LazerSyncManager."""
    realm_path: Optional[Path] = None
    files_dir: Optional[Path] = None
    cache_dir: Optional[Path] = None
    lock_path: Optional[Path] = None
    auto_setup: bool = True
    lock_timeout_s: float = 0.0
    max_snapshots: int = 3


@dataclass(frozen=True)
class SyncSummary:
    """Summary metrics of a synchronization pass."""
    success: bool
    total_7k: int = 0
    evaluated_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    snapshot_path: Optional[str] = None
    error: Optional[str] = None


def resolve_beatmap_file(files_dir: Path, record: LazerBeatmapRecord) -> Optional[Path]:
    """
    Resolve the physical .osu file from osu!lazer content-addressed storage.
    Checks sharded hash path (files/a/ab/abcdef...), flat hash, and .osu extension.
    """
    if not files_dir.exists():
        return None

    candidates = [h for h in (record.file_hash, record.hash, record.md5_hash) if h]
    for h in candidates:
        if len(h) >= 2:
            sharded = files_dir / h[:1] / h[:2] / h
            if sharded.exists() and sharded.is_file():
                return sharded
        flat = files_dir / h
        if flat.exists() and flat.is_file():
            return flat
        with_osu = files_dir / f"{h}.osu"
        if with_osu.exists() and with_osu.is_file():
            return with_osu

    return None


_EXTRACT_INJECTED_PATTERN = re.compile(r"\s*\((\d+\.\d+)★\s+([A-Za-z_]+)\)$")


def try_extract_annotated_metadata(record: LazerBeatmapRecord) -> Optional[Tuple[float, str]]:
    """
    If a record was previously annotated by proj7k and its attributes match,
    extract (star_rating, dominant_tech) directly without re-evaluating the chart.
    """
    match = _EXTRACT_INJECTED_PATTERN.search(record.difficulty_name)
    if not match:
        return None
    try:
        sr = float(match.group(1))
        tech_title = match.group(2)
        dominant_tech = tech_title.lower()
        if abs(record.star_rating - sr) < 0.01 and f"dominant_{dominant_tech}" in record.tags:
            return (sr, dominant_tech)
    except (ValueError, IndexError):
        return None
    return None


class LazerSyncManager:
    """
    Coordinates incremental difficulty evaluation, safe flush window locking,
    disaster recovery backups, and batch Realm database updates.
    """

    def __init__(
        self,
        options: Optional[SyncOptions] = None,
        bridge_client: Optional[RealmBridgeClient] = None,
        backup_manager: Optional[LazerBackupManager] = None,
    ):
        self.options = options or SyncOptions()
        self.realm_path = Path(self.options.realm_path or DEFAULT_REALM_PATH)
        self.files_dir = Path(self.options.files_dir or DEFAULT_FILES_DIR)
        self.cache_dir = Path(self.options.cache_dir or DEFAULT_CACHE_DIR)
        self.lock_path = Path(self.options.lock_path or (self.realm_path.parent / "client.realm.lock"))

        self.bridge_client = bridge_client or RealmBridgeClient(
            default_realm_path=self.realm_path
        )
        self.backup_manager = backup_manager or LazerBackupManager(
            realm_path=self.realm_path,
            cache_dir=self.cache_dir,
            max_snapshots=self.options.max_snapshots,
        )
        self.cache = TwoLayerCache(
            cache_dir=self.cache_dir / "cache",
            enabled=True,
        )

    def sync_once(self, wait_for_lock: bool = False) -> SyncSummary:
        """
        Execute a single incremental synchronization pass within a safe flush window.
        """
        if not self.realm_path.exists():
            return SyncSummary(
                success=False,
                error=f"osu!lazer database not found at '{self.realm_path}'",
            )
        if not self.files_dir.exists():
            return SyncSummary(
                success=False,
                error=f"osu!lazer files directory not found at '{self.files_dir}'",
            )

        if self.options.auto_setup:
            try:
                self.bridge_client.ensure_installed()
            except Exception as e:
                return SyncSummary(success=False, error=f"Bridge environment setup failed: {e}")

        timeout = 60.0 if wait_for_lock else self.options.lock_timeout_s
        if not probe_realm_lock(self.lock_path):
            if timeout > 0.0:
                with SafeFlushWindow(self.lock_path, timeout_s=timeout, raise_on_busy=False) as win:
                    if not win.is_acquired:
                        return SyncSummary(
                            success=False,
                            error=(
                                f"Safe flush window is closed: osu!lazer database lock at '{self.lock_path}' "
                                "is held by the game process."
                            ),
                        )
            else:
                return SyncSummary(
                    success=False,
                    error=(
                        f"Safe flush window is closed: osu!lazer database lock at '{self.lock_path}' "
                        "is held by the game process."
                    ),
                )


        # 1. Dump 7K beatmap records from Realm
        try:
            beatmaps = self.bridge_client.dump_7k_beatmaps(
                realm_path=self.realm_path,
                auto_setup=False,
            )
        except Exception as e:
            return SyncSummary(success=False, error=f"Failed to dump 7K beatmaps: {e}")

        mania_7k = [b for b in beatmaps if b.is_7k_mania]
        if not mania_7k:
            return SyncSummary(success=True, total_7k=0)

        # 2. Incremental diffing and evaluation
        items_to_update: List[Tuple[LazerBeatmapRecord, float, str]] = []
        already_annotated_items: List[Tuple[LazerBeatmapRecord, float, str]] = []
        skipped_count = 0
        failed_count = 0

        total_maps = len(mania_7k)
        for idx, rec in enumerate(mania_7k, start=1):
            if idx % 500 == 0 or idx == total_maps:
                logger.info(
                    f"Processing 7K beatmaps: {idx}/{total_maps} "
                    f"({len(items_to_update)} to update, {skipped_count} unchanged)..."
                )

            # Fast-path: check if chart is already annotated and matches
            extracted = try_extract_annotated_metadata(rec)
            if extracted is not None:
                sr, dom_tech = extracted
                already_annotated_items.append((rec, sr, dom_tech))
                skipped_count += 1
                continue

            osu_path = resolve_beatmap_file(self.files_dir, rec)
            if not osu_path:
                failed_count += 1
                continue

            try:
                result = evaluate_intrinsic_difficulty(osu_path)
                sr = result.star_rating
                dom_tech = result.radar.dominant_technique
            except Exception:
                failed_count += 1
                continue

            target_name = format_injected_difficulty_name(rec.difficulty_name, sr, dom_tech)
            target_tags = inject_binned_skill_tags(rec.tags, dom_tech, sr)

            if (
                rec.difficulty_name == target_name
                and rec.tags == target_tags
                and abs(rec.star_rating - sr) < 0.005
            ):
                skipped_count += 1
                already_annotated_items.append((rec, sr, dom_tech))
                continue

            items_to_update.append((rec, sr, dom_tech))

        if not items_to_update:
            return SyncSummary(
                success=True,
                total_7k=len(mania_7k),
                evaluated_count=0,
                updated_count=0,
                skipped_count=skipped_count,
                failed_count=failed_count,
            )

        # Build collections containing ALL 7k beatmaps to prevent purging existing charts
        all_collection_items = items_to_update + already_annotated_items
        collection_entries = [
            BeatmapCollectionEntry(
                md5_hash=r.md5_hash or r.hash,
                dominant_tech=dom_tech,
                star_rating=sr,
            )
            for r, sr, dom_tech in all_collection_items
        ]
        collections = build_biaxial_collection_map(collection_entries)
        updates, _ = annotate_batch_and_build_collections(items_to_update)


        # 3. Enter safe flush window for atomic write transactions
        with SafeFlushWindow(self.lock_path, timeout_s=timeout, raise_on_busy=False) as window:
            if not window.is_acquired:
                return SyncSummary(
                    success=False,
                    error=(
                        f"Safe flush window is closed: osu!lazer database lock at '{self.lock_path}' "
                        "is held by the game process."
                    ),
                )

            snapshot_path = self.backup_manager.create_realm_snapshot()
            self.backup_manager.record_original_states([r for r, _, _ in items_to_update])

        # Release safe flush window lock prior to invoking Node bridge,
        # allowing Node's Realm Core engine to acquire client.realm.lock without deadlocking.
        update_res = self.bridge_client.apply_batch_update(
            updates=updates,
            collections=collections,
            realm_path=self.realm_path,
            auto_setup=False,
        )

        if not update_res.success:
            return SyncSummary(
                success=False,
                total_7k=len(mania_7k),
                error=f"Database update failed: {update_res.error}",
            )

        return SyncSummary(
            success=True,
            total_7k=len(mania_7k),
            evaluated_count=len(items_to_update),
            updated_count=update_res.updated_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            snapshot_path=str(snapshot_path) if snapshot_path else None,
        )

    def revert_all(self) -> BatchUpdateResult:
        """
        Roll back all beatmaps and delete collections within a safe flush window.
        """
        with SafeFlushWindow(self.lock_path, timeout_s=self.options.lock_timeout_s, raise_on_busy=False) as window:
            if not window.is_acquired:
                return BatchUpdateResult(
                    success=False,
                    error=(
                        f"Safe flush window is closed: cannot revert while osu!lazer is holding "
                        f"database lock at '{self.lock_path}'"
                    ),
                )
        return self.backup_manager.revert_realm_modifications(
            bridge_client=self.bridge_client,
            auto_setup=self.options.auto_setup,
        )


class LazerDaemon:
    """
    Background daemon coordinator that continuously polls the osu!lazer database,
    detects safe flush windows, and applies incremental updates.
    """

    def __init__(
        self,
        manager: Optional[LazerSyncManager] = None,
        interval_s: float = 5.0,
    ):
        self.manager = manager or LazerSyncManager()
        self.interval_s = max(0.001, float(interval_s))
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Signal the daemon loop to terminate gracefully."""
        self._stop_event.set()

    def run(self, stop_event: Optional[threading.Event] = None) -> None:
        """
        Run the daemon loop until stop_event (or self._stop_event) is set.
        """
        stop = stop_event or self._stop_event
        logger.info(f"Starting LazerDaemon (polling interval: {self.interval_s:.1f}s)...")

        while not stop.is_set():
            summary = self.manager.sync_once(wait_for_lock=False)
            if summary.success:
                if summary.updated_count > 0:
                    logger.info(
                        f"Successfully synced {summary.updated_count} beatmaps "
                        f"({summary.skipped_count} unchanged, {summary.failed_count} failed). "
                        f"Snapshot: {summary.snapshot_path}"
                    )
                else:
                    logger.debug(
                        f"Sync check complete: 0 updates needed "
                        f"({summary.skipped_count} unchanged, {summary.failed_count} failed)."
                    )
            else:
                if "Safe flush window is closed" in (summary.error or ""):
                    logger.debug("Safe flush window closed (osu!lazer running). Waiting for next cycle...")
                else:
                    logger.warning(f"Sync cycle warning: {summary.error}")

            if stop.wait(timeout=self.interval_s):
                break

        logger.info("LazerDaemon stopped gracefully.")


def run_daemon(
    manager: Optional[LazerSyncManager] = None,
    interval_s: float = 5.0,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """Convenience helper to run LazerDaemon."""
    daemon = LazerDaemon(manager=manager, interval_s=interval_s)
    daemon.run(stop_event=stop_event)

