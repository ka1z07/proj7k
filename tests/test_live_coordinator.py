"""
Tests for proj7k.live.coordinator (Seam 3: LiveSessionCoordinator).

Verifies top-level integration connecting LazerLogWatcher, LazerRealmIndex,
LiveEngine, and LiveServer. Tests end-to-end log event injection, WebSocket
broadcasting, cache hit latency (<5ms), and missing file error degradation.
"""

import asyncio
import json
from pathlib import Path
import socket
import time
import pytest
import websockets

from proj7k.cache import TwoLayerCache
from proj7k.lazer.bridge import LazerBeatmapRecord
from proj7k.live.coordinator import LiveSessionCoordinator
from proj7k.live.engine import LiveEngine
from proj7k.live.index import LazerRealmIndex
from proj7k.live.server import LiveServer
from proj7k.live.watcher import (
    BeatmapChangedEvent,
    ClockSeekingEvent,
    ClockStartedEvent,
    ClockStoppedEvent,
    LazerLogWatcher,
)


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_dummy_osu_content(title: str = "Live Test Chart", diff: str = "7K Hard") -> str:
    lines = [
        "osu file format v14",
        "[General]",
        "Mode: 3",
        "[Metadata]",
        f"Title:{title}",
        "Artist:Live Artist",
        "Creator:Mapper",
        f"Version:{diff}",
        "[Difficulty]",
        "CircleSize:7",
        "[TimingPoints]",
        "0,400,4,2,0,50,1,0",
        "[HitObjects]",
    ]
    for i in range(40):
        col_x = [36, 109, 182, 256, 329, 402, 475][i % 7]
        t = i * 120
        lines.append(f"{col_x},192,{t},1,0,0:0:0:0:")
    return "\n".join(lines)


def _setup_test_environment(tmp_path: Path):
    files_dir = tmp_path / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    file_hash = "abcdef1234567890"
    shard_dir = files_dir / file_hash[:1] / file_hash[:2]
    shard_dir.mkdir(parents=True, exist_ok=True)
    osu_file = shard_dir / file_hash
    osu_file.write_text(_make_dummy_osu_content("Live Chart", "7K Hyper"), encoding="utf-8")

    rec = LazerBeatmapRecord(
        id="101",
        hash=file_hash,
        md5_hash=file_hash,
        file_hash=file_hash,
        star_rating=5.5,
        difficulty_name="7K Hyper (5.50★ Jack)",
        tags="dominant_jack",
        title="Live Chart",
        artist="Live Artist",
        ruleset_id=3,
        circle_size=7.0,
    )

    index = LazerRealmIndex(records=[rec], files_dir=files_dir)
    index.warmup()

    cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=True)
    engine = LiveEngine(cache=cache)

    return files_dir, index, engine


def test_coordinator_on_beatmap_changed_broadcasts_radar(tmp_path: Path):
    async def _run():
        port = _get_free_port()
        files_dir, index, engine = _setup_test_environment(tmp_path)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)
        coordinator = LiveSessionCoordinator(server=server, engine=engine, index=index)

        await server.start()
        await coordinator.start()

        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                # 1. Welcome frame
                welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                assert welcome["type"] == "welcome"

                # 2. Inject beatmap change event
                ev = BeatmapChangedEvent(
                    artist="Live Artist",
                    title="Live Chart",
                    difficulty="7K Hyper",
                    creator="Mapper",
                )
                frame = await coordinator.on_beatmap_changed(ev)
                assert frame is not None
                assert frame["type"] == "beatmap_update"

                # 3. Assert client receives the broadcast frame
                raw_client_frame = await asyncio.wait_for(ws.recv(), timeout=2.0)
                client_frame = json.loads(raw_client_frame)
                assert client_frame["type"] == "beatmap_update"
                assert client_frame["metadata"]["title"] == "Live Chart"
                assert client_frame["star_rating"] > 0.0
                assert "radar" in client_frame
                assert "jack" in client_frame["radar"]
                assert "stream" in client_frame["radar"]
                assert client_frame["radar"]["stream"] > 0.0

                # 4. Secondary switch test: cache hit latency < 5ms
                t0 = time.perf_counter()
                cached_frame = await coordinator.on_beatmap_changed(ev)
                latency_ms = (time.perf_counter() - t0) * 1000
                assert cached_frame is not None
                assert cached_frame["cached"] is True
                assert latency_ms < 15.0, f"Cache hit latency was {latency_ms:.2f}ms"

        finally:
            await coordinator.stop()
            await server.stop()

    asyncio.run(_run())


def test_coordinator_error_degradation_not_found(tmp_path: Path):
    async def _run():
        port = _get_free_port()
        files_dir, index, engine = _setup_test_environment(tmp_path)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)
        coordinator = LiveSessionCoordinator(server=server, engine=engine, index=index)

        await server.start()
        await coordinator.start()

        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                await ws.recv()  # welcome

                # Event for a beatmap completely missing in index
                ev = BeatmapChangedEvent(
                    artist="Unknown Artist",
                    title="Unknown Title",
                    difficulty="Unknown Diff",
                    creator="Unknown",
                )
                res = await coordinator.on_beatmap_changed(ev)
                assert res is None

                # Client should receive graceful error notification
                err_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                assert err_msg["type"] == "error"
                assert err_msg["code"] == "beatmap_not_found"
                assert "Unknown Title" in err_msg["message"]
        finally:
            await coordinator.stop()
            await server.stop()

    asyncio.run(_run())


def test_coordinator_missing_physical_file(tmp_path: Path):
    async def _run():
        port = _get_free_port()
        files_dir, index, engine = _setup_test_environment(tmp_path)

        # Add a record with missing file
        ghost_rec = LazerBeatmapRecord(
            id="999",
            hash="ghosthash123",
            md5_hash="ghosthash123",
            file_hash="ghosthash123",
            star_rating=6.0,
            difficulty_name="Ghost Diff",
            tags="",
            title="Ghost Song",
            artist="Ghost Artist",
            ruleset_id=3,
            circle_size=7.0,
        )
        index._records.append(ghost_rec)
        index._build_indices(index._records)

        server = LiveServer(host="127.0.0.1", port=port, engine=engine)
        coordinator = LiveSessionCoordinator(server=server, engine=engine, index=index)

        await server.start()
        await coordinator.start()

        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                await ws.recv()  # welcome

                ev = BeatmapChangedEvent(
                    artist="Ghost Artist",
                    title="Ghost Song",
                    difficulty="Ghost Diff",
                    creator="Ghost",
                )
                res = await coordinator.on_beatmap_changed(ev)
                assert res is None

                # Client receives physical file missing notification
                err_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                assert err_msg["type"] == "error"
                assert "file not found" in err_msg["message"].lower()
        finally:
            await coordinator.stop()
            await server.stop()

    asyncio.run(_run())


def test_end_to_end_log_watcher_injection(tmp_path: Path):
    async def _run():
        port = _get_free_port()
        files_dir, index, engine = _setup_test_environment(tmp_path)

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        runtime_log = logs_dir / "runtime.log"
        runtime_log.write_text("2026-09-17 12:00:00 [verbose]: osu! started\n", encoding="utf-8")

        watcher = LazerLogWatcher(logs_dir=logs_dir, poll_interval_s=0.02, start_at_end=True)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)
        coordinator = LiveSessionCoordinator(
            server=server,
            engine=engine,
            index=index,
            watcher=watcher,
        )

        await server.start()
        await coordinator.start()

        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                await ws.recv()  # welcome

                # Append log line to runtime.log simulating user selecting beatmap in lazer
                await asyncio.sleep(0.05)
                with open(runtime_log, "a", encoding="utf-8") as f:
                    f.write(
                        "Game-wide working beatmap updated to Live Artist - Live Chart [7K Hyper] (Mapper)\n"
                    )
                    f.flush()

                # Client should receive broadcast automatically from watcher -> coordinator -> server
                raw_frame = await asyncio.wait_for(ws.recv(), timeout=3.0)
                frame = json.loads(raw_frame)
                assert frame["type"] == "beatmap_update"
                assert frame["metadata"]["title"] == "Live Chart"
                assert frame["metadata"]["artist"] == "Live Artist"
        finally:
            await coordinator.stop()
            await server.stop()

    asyncio.run(_run())


def test_coordinator_clock_sync_lifecycle(tmp_path: Path):
    async def _run():
        port = _get_free_port()
        files_dir, index, engine = _setup_test_environment(tmp_path)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)
        coordinator = LiveSessionCoordinator(server=server, engine=engine, index=index)

        await server.start()
        await coordinator.start()

        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                await ws.recv()  # welcome

                # 1. Initially idle
                assert coordinator.is_playing is False
                assert coordinator.clock_state.status == "idle"

                # 2. Seeking before start (lead-in)
                seek_ev1 = ClockSeekingEvent(time_ms=-1680.0)
                await coordinator.handle_log_event(seek_ev1)

                raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                msg1 = json.loads(raw_msg)
                assert msg1["type"] == "clock_sync"
                assert msg1["status"] == "idle"
                assert msg1["active"] is False
                assert msg1["start_ms"] == -1680.0
                assert msg1["rate"] == 0.0

                # 3. Clock started (gameplay begins)
                start_ev = ClockStartedEvent()
                await coordinator.handle_log_event(start_ev)

                raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                msg2 = json.loads(raw_msg)
                assert msg2["type"] == "clock_sync"
                assert msg2["status"] == "playing"
                assert msg2["active"] is True
                assert msg2["start_ms"] == -1680.0
                assert msg2["rate"] == 1.0
                assert coordinator.is_playing is True

                # 4. Skip intro seeking
                seek_ev2 = ClockSeekingEvent(time_ms=3402.4)
                await coordinator.handle_log_event(seek_ev2)

                raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                msg3 = json.loads(raw_msg)
                assert msg3["type"] == "clock_sync"
                assert msg3["status"] == "playing"
                assert msg3["active"] is True
                assert msg3["start_ms"] == pytest.approx(3402.4)
                assert msg3["rate"] == 1.0

                # 5. Stop gameplay
                stop_ev = ClockStoppedEvent()
                await coordinator.handle_log_event(stop_ev)

                raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                msg4 = json.loads(raw_msg)
                assert msg4["type"] == "clock_sync"
                assert msg4["status"] == "idle"
                assert msg4["active"] is False
                assert msg4["rate"] == 0.0
                assert coordinator.is_playing is False

        finally:
            await coordinator.stop()
            await server.stop()

    asyncio.run(_run())


def test_coordinator_clock_reset_on_beatmap_switch_while_playing(tmp_path: Path):
    async def _run():
        port = _get_free_port()
        files_dir, index, engine = _setup_test_environment(tmp_path)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)
        coordinator = LiveSessionCoordinator(server=server, engine=engine, index=index)

        await server.start()
        await coordinator.start()

        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                await ws.recv()  # welcome

                # Start clock playing
                await coordinator.handle_log_event(ClockStartedEvent())
                await ws.recv()  # clock_sync playing
                assert coordinator.is_playing is True

                # Beatmap switch occurs while playing
                ev = BeatmapChangedEvent(
                    artist="Live Artist",
                    title="Live Chart",
                    difficulty="7K Hyper",
                    creator="Mapper",
                )
                await coordinator.handle_log_event(ev)

                # Expect clock_sync idle frame followed by beatmap_update frame
                msg1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                assert msg1["type"] == "clock_sync"
                assert msg1["status"] == "idle"
                assert msg1["active"] is False
                assert coordinator.is_playing is False

                msg2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                assert msg2["type"] == "beatmap_update"
                assert msg2["metadata"]["title"] == "Live Chart"

        finally:
            await coordinator.stop()
            await server.stop()

    asyncio.run(_run())


def test_end_to_end_gameplay_clock_sync_from_log_watcher(tmp_path: Path):
    async def _run():
        port = _get_free_port()
        files_dir, index, engine = _setup_test_environment(tmp_path)

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        runtime_log = logs_dir / "runtime.log"
        runtime_log.write_text("2026-09-17 12:00:00 [verbose]: osu! started\n", encoding="utf-8")

        watcher = LazerLogWatcher(logs_dir=logs_dir, poll_interval_s=0.02, start_at_end=True)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)
        coordinator = LiveSessionCoordinator(
            server=server,
            engine=engine,
            index=index,
            watcher=watcher,
        )

        await server.start()
        await coordinator.start()

        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                await ws.recv()  # welcome

                # Append clock events to runtime.log
                await asyncio.sleep(0.05)
                with open(runtime_log, "a", encoding="utf-8") as f:
                    f.write("2026-09-17 12:01:00 [verbose]: GameplayClockContainer seeking to -1500\n")
                    f.write("2026-09-17 12:01:00 [verbose]: GameplayClockContainer started via call to StartGameplayClock\n")
                    f.write("2026-09-17 12:01:02 [verbose]: GameplayClockContainer seeking to 8500.5\n")
                    f.write("2026-09-17 12:01:30 [verbose]: GameplayClockContainer stopped via call to StopGameplayClock\n")
                    f.flush()

                # 1. Seek before start
                f1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                assert f1["type"] == "clock_sync"
                assert f1["start_ms"] == -1500.0

                # 2. Started
                f2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                assert f2["type"] == "clock_sync"
                assert f2["status"] == "playing"
                assert f2["active"] is True
                assert f2["rate"] == 1.0

                # 3. Seeking (skip intro)
                f3 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                assert f3["type"] == "clock_sync"
                assert f3["status"] == "playing"
                assert f3["start_ms"] == 8500.5

                # 4. Stopped
                f4 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                assert f4["type"] == "clock_sync"
                assert f4["status"] == "idle"
                assert f4["active"] is False
                assert f4["rate"] == 0.0

        finally:
            await coordinator.stop()
            await server.stop()

    asyncio.run(_run())

