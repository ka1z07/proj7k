"""
LiveSessionCoordinator: Top-level integration hub connecting LazerLogWatcher,
LazerRealmIndex, LiveEngine, and LiveServer.

SPEC-P2.4-02 / ADR-0010.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from proj7k.live.engine import LiveEngine
from proj7k.live.index import LazerRealmIndex
from proj7k.live.server import LiveServer
from proj7k.live.watcher import BeatmapChangedEvent, LazerLogWatcher

logger = logging.getLogger("proj7k.live.coordinator")


class LiveSessionCoordinator:
    """
    Coordinates real-time log event capture, fast inverted index resolution,
    cached difficulty evaluation, and multi-client WebSocket broadcasting.
    """

    def __init__(
        self,
        server: LiveServer,
        engine: LiveEngine,
        index: Optional[LazerRealmIndex] = None,
        watcher: Optional[LazerLogWatcher] = None,
    ):
        self.server = server
        self.engine = engine
        self.index = index
        self.watcher = watcher

        self._watcher_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._current_event: Optional[BeatmapChangedEvent] = None

    async def start(self) -> None:
        """
        Warms up the Realm index if needed and starts the log watcher background task.
        """
        if self.index is not None and not self.index.is_warmed_up:
            try:
                count = self.index.warmup()
                logger.info(f"LiveSessionCoordinator: index warmed up with {count} charts.")
            except Exception as e:
                logger.warning(f"Could not warm up Realm index (osu!lazer may not be installed): {e}")

        if self.watcher is not None:
            self._watcher_task = asyncio.create_task(
                self.watcher.run(callback=self.on_beatmap_changed)
            )
            logger.info("LiveSessionCoordinator: log watcher started.")

    async def stop(self) -> None:
        """
        Stops the watcher background task gracefully.
        """
        if self.watcher is not None:
            self.watcher.stop()

        if self._watcher_task is not None:
            try:
                await asyncio.wait_for(self._watcher_task, timeout=1.0)
            except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError):
                self._watcher_task.cancel()
            self._watcher_task = None

    async def on_beatmap_changed(
        self,
        event: BeatmapChangedEvent,
    ) -> Optional[Dict[str, Any]]:
        """
        Handles a beatmap change event:
        1. Resolves record in LazerRealmIndex (<1ms).
        2. Resolves physical .osu path in files/ storage (<1ms).
        3. Analyzes chart through LiveEngine (cache hit <5ms).
        4. Broadcasts canonical frame to all WebSocket clients.
        """
        async with self._lock:
            self._current_event = event

            if self.index is None:
                logger.warning("No Realm index configured in LiveSessionCoordinator.")
                return None

            # 1. Lookup beatmap record
            record = self.index.lookup(
                title=event.title,
                difficulty=event.difficulty,
                artist=event.artist,
            )

            if record is None:
                msg = f"Beatmap not found in 7K Realm index: {event.title} [{event.difficulty}]"
                logger.warning(msg)
                err_frame = {
                    "type": "beatmap_not_found",
                    "message": msg,
                    "event": {
                        "artist": event.artist,
                        "title": event.title,
                        "difficulty": event.difficulty,
                        "creator": event.creator,
                    },
                }
                await self.server.broadcast(err_frame)
                return None

            # 2. Resolve physical .osu chart file
            osu_file = self.index.resolve_file(record)
            if osu_file is None or not osu_file.exists():
                msg = f"Physical .osu file not found on disk for {event.title} [{event.difficulty}]"
                logger.warning(msg)
                err_frame = {
                    "type": "error",
                    "message": msg,
                }
                await self.server.broadcast(err_frame)
                return None

            # 3. Analyze chart
            try:
                frame = self.engine.analyze_file(osu_file)
                frame["metadata"]["realm_id"] = record.id
                await self.server.broadcast(frame)
                return frame
            except Exception as e:
                logger.error(f"Error evaluating chart {osu_file}: {e}", exc_info=True)
                err_frame = {
                    "type": "error",
                    "message": f"Error analyzing chart: {e}",
                }
                await self.server.broadcast(err_frame)
                return None
