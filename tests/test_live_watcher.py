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
    ClockSeekingEvent,
    ClockStartedEvent,
    ClockStoppedEvent,
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

    # Difficulty with nested brackets
    line4 = (
        "Game-wide working beatmap updated to "
        "xi - FREEDOM DiVE [[FOUR DIMENSIONS]] (Nakagawa-Kanon)"
    )
    event4 = parse_log_line(line4)
    assert event4 is not None
    assert event4.difficulty == "[FOUR DIMENSIONS]"

    # Irrelevant log lines return None
    assert parse_log_line("Running at 60 FPS") is None
    assert parse_log_line("Loading database...") is None


def test_parse_real_lazer_log_format():
    """
    Verifies parsing real osu!lazer log lines where creator is in parentheses
    before difficulty in brackets:
    'Game-wide working beatmap updated to <Artist> - <Title> (<Creator>) [<Difficulty>]'
    """
    line1 = (
        "2026-09-17 12:14:42 [verbose]: Game-wide working beatmap updated to "
        "Kyutatsuki - Sea of Stars (tyrcs) [Interstellar Expedition (12.23★ LN_Inverse)]"
    )
    event1 = parse_log_line(line1)
    assert event1 is not None, f"Failed to parse real lazer line: {line1}"
    assert event1.artist == "Kyutatsuki"
    assert event1.title == "Sea of Stars"
    assert event1.creator == "tyrcs"
    assert event1.difficulty == "Interstellar Expedition (12.23★ LN_Inverse)"

    line2 = (
        "2026-09-17 09:13:55 [verbose]: Game-wide working beatmap updated to "
        "xi - Blue Zenith (Jinjin) [jakads' Dimensions (7.36★ Stream)]"
    )
    event2 = parse_log_line(line2)
    assert event2 is not None
    assert event2.artist == "xi"
    assert event2.title == "Blue Zenith"
    assert event2.creator == "Jinjin"
    assert event2.difficulty == "jakads' Dimensions (7.36★ Stream)"

    # Nested brackets in difficulty with creator
    line3 = (
        "2026-09-17 12:10:19 [verbose]: Game-wide working beatmap updated to "
        "sun3 / BGA:Johnny / OBJ:K-SPIN - Synth Stream(RUNOTHER7) (5ynt3ck) [[BMS] [i_13] (6.30★ Stream)]"
    )
    event3 = parse_log_line(line3)
    assert event3 is not None
    assert event3.creator == "5ynt3ck"
    assert event3.difficulty == "[BMS] [i_13] (6.30★ Stream)"


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

def test_parse_log_line_clock_events():
    # 1. Start event
    line_start = (
        "2026-09-17 09:15:35 [verbose]: "
        "GameplayClockContainer started via call to StartGameplayClock"
    )
    ev_start = parse_log_line(line_start)
    assert isinstance(ev_start, ClockStartedEvent)

    # 2. Seeking event with float milliseconds
    line_seek_float = (
        "2026-09-17 09:15:36 [verbose]: "
        "GameplayClockContainer seeking to 3402.4003548964024"
    )
    ev_seek_float = parse_log_line(line_seek_float)
    assert isinstance(ev_seek_float, ClockSeekingEvent)
    assert ev_seek_float.time_ms == pytest.approx(3402.4003548964024)

    # 3. Seeking event with negative lead-in milliseconds
    line_seek_neg = (
        "2026-09-10 18:09:49 [verbose]: "
        "GameplayClockContainer seeking to -1680"
    )
    ev_seek_neg = parse_log_line(line_seek_neg)
    assert isinstance(ev_seek_neg, ClockSeekingEvent)
    assert ev_seek_neg.time_ms == -1680.0

    # 4. Seeking event to 0 (restart)
    line_seek_zero = "GameplayClockContainer seeking to 0"
    ev_seek_zero = parse_log_line(line_seek_zero)
    assert isinstance(ev_seek_zero, ClockSeekingEvent)
    assert ev_seek_zero.time_ms == 0.0

    # 5. Stop event
    line_stop = (
        "2026-09-17 10:02:26 [verbose]: "
        "GameplayClockContainer stopped via call to StopGameplayClock"
    )
    ev_stop = parse_log_line(line_stop)
    assert isinstance(ev_stop, ClockStoppedEvent)


def test_watcher_captures_clock_events_stream(tmp_path: Path):
    async def _run():
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        log_file = logs_dir / "runtime.log"
        log_file.write_text("2026-09-17 10:00:00 [verbose]: Starting osu!\n", encoding="utf-8")

        watcher = LazerLogWatcher(logs_dir=logs_dir, poll_interval_s=0.02)
        received_events = []

        async def on_event(ev):
            received_events.append(ev)

        task = asyncio.create_task(watcher.run(callback=on_event))

        try:
            await asyncio.sleep(0.05)
            with open(log_file, "a", encoding="utf-8") as f:
                # 1. Beatmap change
                f.write(
                    "Game-wide working beatmap updated to Camellia - crystallized [7K Hyper] (Smoothie World)\n"
                )
                # 2. Seek before start
                f.write("GameplayClockContainer seeking to -1500\n")
                # 3. Clock started
                f.write("GameplayClockContainer started via call to StartGameplayClock\n")
                # 4. Skip intro seeking
                f.write("GameplayClockContainer seeking to 5200.5\n")
                # 5. Clock stopped
                f.write("GameplayClockContainer stopped via call to StopGameplayClock\n")
                f.flush()

            # Wait for all 5 events
            for _ in range(50):
                if len(received_events) >= 5:
                    break
                await asyncio.sleep(0.02)

            assert len(received_events) == 5
            assert isinstance(received_events[0], BeatmapChangedEvent)
            assert received_events[0].title == "crystallized"

            assert isinstance(received_events[1], ClockSeekingEvent)
            assert received_events[1].time_ms == -1500.0

            assert isinstance(received_events[2], ClockStartedEvent)

            assert isinstance(received_events[3], ClockSeekingEvent)
            assert received_events[3].time_ms == 5200.5

            assert isinstance(received_events[4], ClockStoppedEvent)
        finally:
            watcher.stop()
            await task

    asyncio.run(_run())



