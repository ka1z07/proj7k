"""
Tests for proj7k.live.watcher (Seam 1: LazerLogWatcher).

Verifies regex extraction of beatmap metadata from osu!lazer runtime.log,
non-blocking tailing, and automatic log rotation handling.
"""

import asyncio
from pathlib import Path
import pytest

from proj7k.live.watcher import (
    BeatmapChangedEvent,
    LazerLogWatcher,
    parse_log_line,
)


def test_parse_log_line_beatmap_update():
    # Standard format with timestamp and log level prefix
    line1 = (
        "2026-09-17 19:10:00 [verbose]: Game-wide working beatmap updated to "
        "Camellia - crystallized [7K Hyper] (Smoothie World)."
    )
    event1 = parse_log_line(line1)
    assert event1 is not None
    assert isinstance(event1, BeatmapChangedEvent)
    assert event1.artist == "Camellia"
    assert event1.title == "crystallized"
    assert event1.difficulty == "7K Hyper"
    assert event1.creator == "Smoothie World"

    # Title with hyphens
    line2 = (
        "Game-wide working beatmap updated to "
        "xi - FREEDOM DiVE -Another- [FOUR DIMENSIONS] (Nakagawa-Kanon)"
    )
    event2 = parse_log_line(line2)
    assert event2 is not None
    assert event2.artist == "xi"
    assert event2.title == "FREEDOM DiVE -Another-"
    assert event2.difficulty == "FOUR DIMENSIONS"
    assert event2.creator == "Nakagawa-Kanon"

    # Injected suffix in difficulty
    line3 = (
        "Game-wide working beatmap updated to "
        "Team Grimoire - C18H27NO3 [14 (10.74★ Tech)] (Kz)"
    )
    event3 = parse_log_line(line3)
    assert event3 is not None
    assert event3.artist == "Team Grimoire"
    assert event3.title == "C18H27NO3"
    assert event3.difficulty == "14 (10.74★ Tech)"
    assert event3.creator == "Kz"

    # Irrelevant log lines return None
    assert parse_log_line("Running at 60 FPS") is None
    assert parse_log_line("Loading database...") is None


def test_watcher_tail_and_log_rotation(tmp_path: Path):
    async def _run():
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        log_file1 = logs_dir / "20260917.runtime.log"
        log_file1.write_text("2026-09-17 10:00:00 [verbose]: Starting osu!\n", encoding="utf-8")

        watcher = LazerLogWatcher(logs_dir=logs_dir, poll_interval_s=0.02)
        event_queue: asyncio.Queue[BeatmapChangedEvent] = asyncio.Queue()

        async def on_event(ev: BeatmapChangedEvent):
            await event_queue.put(ev)

        task = asyncio.create_task(watcher.run(callback=on_event))

        try:
            await asyncio.sleep(0.05)
            # 1. Append beatmap update to file 1
            with open(log_file1, "a", encoding="utf-8") as f:
                f.write(
                    "Game-wide working beatmap updated to Artist1 - Title1 [Diff1] (Creator1)\n"
                )
                f.flush()

            event1 = await asyncio.wait_for(event_queue.get(), timeout=2.0)
            assert event1.title == "Title1"
            assert event1.artist == "Artist1"
            assert event1.difficulty == "Diff1"

            # 2. Simulate log rotation: a newer runtime.log is created
            log_file2 = logs_dir / "20260918.runtime.log"
            log_file2.write_text("2026-09-18 00:00:00 [verbose]: Rotated log\n", encoding="utf-8")
            await asyncio.sleep(0.05)

            with open(log_file2, "a", encoding="utf-8") as f:
                f.write(
                    "Game-wide working beatmap updated to Artist2 - Title2 [Diff2] (Creator2)\n"
                )
                f.flush()

            event2 = await asyncio.wait_for(event_queue.get(), timeout=2.0)
            assert event2.title == "Title2"
            assert event2.artist == "Artist2"
            assert event2.difficulty == "Diff2"

        finally:
            watcher.stop()
            await task

    asyncio.run(_run())


def test_watcher_truncation_handling(tmp_path: Path):
    async def _run():
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        log_file = logs_dir / "runtime.log"
        log_file.write_text("initial line 1\ninitial line 2\n", encoding="utf-8")

        watcher = LazerLogWatcher(logs_dir=logs_dir, poll_interval_s=0.02, start_at_end=True)
        events = []

        async def on_event(ev: BeatmapChangedEvent):
            events.append(ev)

        task = asyncio.create_task(watcher.run(callback=on_event))

        try:
            await asyncio.sleep(0.05)
            # Truncate file to 0 bytes
            with open(log_file, "r+", encoding="utf-8") as f:
                f.truncate(0)
            await asyncio.sleep(0.05)
            # Write new beatmap
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("Game-wide working beatmap updated to Artist - Title [Diff] (Creator)\n")
            await asyncio.sleep(0.1)
            assert len(events) == 1
            assert events[0].title == "Title"
        finally:
            watcher.stop()
            await task

    asyncio.run(_run())


