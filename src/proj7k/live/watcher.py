"""
LazerLogWatcher: Non-blocking osu!lazer runtime log tailing and event extraction.

SPEC-P2.4-02 / ADR-0010.
"""

import asyncio
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, List, Optional

logger = logging.getLogger("proj7k.live.watcher")

DEFAULT_LOGS_DIR = Path.home() / "Library" / "Application Support" / "osu" / "logs"

# Regex matching working beatmap update in osu!lazer logs:
# Canonical osu!lazer format: "Game-wide working beatmap updated to Artist - Title (Creator) [Difficulty]"
# Legacy/alternative format: "Game-wide working beatmap updated to Artist - Title [Difficulty] (Creator)"
BEATMAP_UPDATED_LAZER_PATTERN = re.compile(
    r"Game-wide working beatmap updated to (?P<artist_title>.*?)\s+\((?P<creator>[^)]*)\)\s*\[(?P<diff>.*)\]\.?$"
)
BEATMAP_UPDATED_LEGACY_PATTERN = re.compile(
    r"Game-wide working beatmap updated to (?P<artist_title>.*?)\s*\[(?P<diff>.*)\]\s*\((?P<creator>[^)]*)\)\.?$"
)
BEATMAP_UPDATED_PATTERN = BEATMAP_UPDATED_LAZER_PATTERN


# Regex matching gameplay clock events in osu!lazer logs
CLOCK_STARTED_PATTERN = re.compile(
    r"GameplayClockContainer started via call to StartGameplayClock"
)
CLOCK_SEEKING_PATTERN = re.compile(
    r"GameplayClockContainer seeking to\s+(?P<time_ms>-?\d+(?:\.\d+)?)"
)
CLOCK_STOPPED_PATTERN = re.compile(
    r"GameplayClockContainer stopped via call to StopGameplayClock"
)


@dataclass(frozen=True)
class BeatmapChangedEvent:
    artist: str
    title: str
    difficulty: str
    creator: str
    raw_line: str = ""


@dataclass(frozen=True)
class ClockStartedEvent:
    raw_line: str = ""


@dataclass(frozen=True)
class ClockSeekingEvent:
    time_ms: float
    raw_line: str = ""


@dataclass(frozen=True)
class ClockStoppedEvent:
    raw_line: str = ""


from typing import Union

LiveWatcherEvent = Union[
    BeatmapChangedEvent,
    ClockStartedEvent,
    ClockSeekingEvent,
    ClockStoppedEvent,
]


def parse_log_line(line: str) -> Optional[LiveWatcherEvent]:
    """
    Parses a single osu!lazer log line and extracts LiveWatcherEvent if matched.
    Matches:
    - BeatmapChangedEvent: "Game-wide working beatmap updated to ..."
    - ClockSeekingEvent: "GameplayClockContainer seeking to <time_ms>"
    - ClockStartedEvent: "GameplayClockContainer started via call to StartGameplayClock"
    - ClockStoppedEvent: "GameplayClockContainer stopped via call to StopGameplayClock"
    """
    clean_line = line.strip()
    if not clean_line:
        return None

    # 1. Beatmap change event
    if "Game-wide working beatmap updated to" in clean_line:
        match = BEATMAP_UPDATED_LAZER_PATTERN.search(
            clean_line
        ) or BEATMAP_UPDATED_LEGACY_PATTERN.search(clean_line)
        if match:
            artist_title = match.group("artist_title").strip()
            difficulty = match.group("diff").strip()
            creator = match.group("creator").strip()

            if " - " in artist_title:
                artist, title = artist_title.split(" - ", 1)
                artist = artist.strip()
                title = title.strip()
            else:
                artist = ""
                title = artist_title

            return BeatmapChangedEvent(
                artist=artist,
                title=title,
                difficulty=difficulty,
                creator=creator,
                raw_line=clean_line,
            )

    # 2. Clock seeking event
    if "GameplayClockContainer seeking to" in clean_line:
        match = CLOCK_SEEKING_PATTERN.search(clean_line)
        if match:
            return ClockSeekingEvent(
                time_ms=float(match.group("time_ms")),
                raw_line=clean_line,
            )

    # 3. Clock started event
    if "GameplayClockContainer started via call to StartGameplayClock" in clean_line:
        return ClockStartedEvent(raw_line=clean_line)

    # 4. Clock stopped event
    if "GameplayClockContainer stopped via call to StopGameplayClock" in clean_line:
        return ClockStoppedEvent(raw_line=clean_line)

    return None


def find_latest_runtime_log(logs_dir: Path) -> Optional[Path]:
    """
    Finds the latest runtime log file in logs_dir by modification time.
    Matches *runtime.log (e.g. runtime.log, 20260917.runtime.log).
    """
    if not logs_dir.exists() or not logs_dir.is_dir():
        return None

    candidates: List[Path] = [
        p for p in logs_dir.iterdir() if p.is_file() and p.name.endswith("runtime.log")
    ]
    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


class LazerLogWatcher:
    """
    Asynchronous watcher that tails the active osu!lazer runtime log file
    and emits BeatmapChangedEvent on beatmap switches.
    Handles log rotation and file truncation automatically.
    """

    def __init__(
        self,
        logs_dir: Optional[Path] = None,
        poll_interval_s: float = 0.05,
        start_at_end: bool = True,
    ):
        self.logs_dir = logs_dir or DEFAULT_LOGS_DIR
        self.poll_interval_s = max(0.005, poll_interval_s)
        self.start_at_end = start_at_end
        self._running = False
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Signals the watcher loop to stop."""
        self._running = False
        self._stop_event.set()

    async def _sleep_poll(self) -> None:
        """Helper to pause for poll_interval_s or until stop_event is signaled."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_s)
        except (asyncio.TimeoutError, TimeoutError):
            pass

    async def run(
        self,
        callback: Callable[[BeatmapChangedEvent], Awaitable[None]],
    ) -> None:
        """
        Runs the tailing loop, calling callback(event) for each beatmap change event.
        """
        self._running = True
        self._stop_event.clear()

        current_file: Optional[Path] = None
        current_ino: Optional[int] = None
        file_obj = None
        offset = 0
        is_first_open = True

        try:
            while self._running and not self._stop_event.is_set():
                latest = find_latest_runtime_log(self.logs_dir)

                # Check for log rotation: new or changed runtime log file or inode change
                file_changed = False
                if latest is not None:
                    try:
                        latest_stat = latest.stat()
                        if current_file is None or latest != current_file or current_ino != latest_stat.st_ino:
                            file_changed = True
                    except OSError:
                        pass

                if file_changed and latest is not None:
                    if file_obj is not None:
                        file_obj.close()
                        file_obj = None

                    current_file = latest
                    try:
                        latest_stat = current_file.stat()
                        current_ino = latest_stat.st_ino
                        file_obj = open(current_file, "r", encoding="utf-8", errors="replace")
                        if self.start_at_end and is_first_open:
                            file_obj.seek(0, os.SEEK_END)
                        else:
                            file_obj.seek(0, os.SEEK_SET)
                        is_first_open = False
                        offset = file_obj.tell()
                        logger.info(f"LazerLogWatcher started tailing {current_file}")
                    except OSError as e:
                        logger.warning(f"Failed to open log file {current_file}: {e}")
                        file_obj = None

                if file_obj is None:
                    await self._sleep_poll()
                    continue

                # Check for file truncation
                try:
                    file_size = current_file.stat().st_size
                    if file_size < offset:
                        logger.info(f"Log file truncated: {current_file}. Resetting offset to 0.")
                        file_obj.seek(0, os.SEEK_SET)
                        offset = 0
                except OSError:
                    pass

                # Read available lines
                lines_read = 0
                while True:
                    cur_pos = file_obj.tell()
                    line = file_obj.readline()
                    if not line:
                        break
                    if not line.endswith("\n"):
                        # Line is incomplete, revert offset and wait for flush
                        file_obj.seek(cur_pos)
                        break

                    lines_read += 1
                    event = parse_log_line(line)
                    if event is not None:
                        try:
                            await callback(event)
                        except Exception as e:
                            logger.error(f"Error in watcher callback: {e}", exc_info=True)

                offset = file_obj.tell()
                await self._sleep_poll()

        finally:
            if file_obj is not None:
                try:
                    file_obj.close()
                except OSError:
                    pass
            self._running = False
