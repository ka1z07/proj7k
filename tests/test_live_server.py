"""
Tests for proj7k.live.server (Seam 2: LiveServer).

Verifies HTTP static page serving (offline guarantee), WebSocket handshake,
welcome frame, manual analysis dispatch, and multi-client broadcasting.
"""

import asyncio
import json
import socket
import urllib.request
import pytest
import websockets

from proj7k.cache import TwoLayerCache
from proj7k.live.engine import LiveEngine
from proj7k.live.server import LiveServer


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_dummy_osu_content(title: str = "Live Test Chart") -> str:
    lines = [
        "osu file format v14",
        "[General]",
        "Mode: 3",
        "[Metadata]",
        f"Title:{title}",
        "Artist:Live Artist",
        "Creator:Mapper",
        "Version:7K Hard",
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


def test_http_static_serving_offline_guard(tmp_path):
    async def _run():
        port = _get_free_port()
        cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
        engine = LiveEngine(cache=cache)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)

        await server.start()
        try:
            url = f"http://127.0.0.1:{port}/"

            def _fetch():
                with urllib.request.urlopen(url) as req:
                    assert req.status == 200
                    return req.read().decode("utf-8")

            html = await asyncio.to_thread(_fetch)
            assert "<!DOCTYPE html>" in html
            assert "proj7k" in html.lower()

            # Zero-dependency offline guard: no http:// or https:// external links in HTML
            lower_html = html.lower()
            for external in ("http://", "https://"):
                # Ensure no external scripts, fonts or stylesheets
                assert external not in lower_html, f"External network dependency found: {external}"
        finally:
            await server.stop()

    asyncio.run(_run())


def test_websocket_welcome_and_manual_analysis(tmp_path):
    async def _run():
        port = _get_free_port()
        cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
        engine = LiveEngine(cache=cache)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)

        await server.start()
        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                # 1. Welcome frame
                raw_welcome = await asyncio.wait_for(ws.recv(), timeout=2.0)
                welcome = json.loads(raw_welcome)
                assert welcome["type"] == "welcome"
                assert welcome["status"] == "ready"

                # 2. Send manual analysis request with content
                req = {
                    "type": "analyze_content",
                    "content": _make_dummy_osu_content("WS Beatmap"),
                }
                await ws.send(json.dumps(req))

                # 3. Receive beatmap_update frame
                raw_update = await asyncio.wait_for(ws.recv(), timeout=3.0)
                update = json.loads(raw_update)
                assert update["type"] == "beatmap_update"
                assert update["metadata"]["title"] == "WS Beatmap"
                assert update["star_rating"] > 0.0
                assert "radar" in update
                assert "jack" in update["radar"]
        finally:
            await server.stop()

    asyncio.run(_run())


def test_websocket_broadcast_to_multiple_clients(tmp_path):
    async def _run():
        port = _get_free_port()
        cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
        engine = LiveEngine(cache=cache)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)

        await server.start()
        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws1, websockets.connect(ws_url) as ws2:
                # Welcome frames
                await ws1.recv()
                await ws2.recv()

                # Client 1 triggers analysis
                req = {
                    "type": "analyze_content",
                    "content": _make_dummy_osu_content("Broadcast Chart"),
                }
                await ws1.send(json.dumps(req))

                # Both client 1 and client 2 receive the broadcast
                res1 = json.loads(await asyncio.wait_for(ws1.recv(), timeout=3.0))
                res2 = json.loads(await asyncio.wait_for(ws2.recv(), timeout=3.0))

                assert res1["type"] == "beatmap_update"
                assert res2["type"] == "beatmap_update"
                assert res1["metadata"]["title"] == "Broadcast Chart"
                assert res2["metadata"]["title"] == "Broadcast Chart"
        finally:
            await server.stop()

    asyncio.run(_run())


def test_websocket_analyze_file_not_found(tmp_path):
    async def _run():
        port = _get_free_port()
        cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
        engine = LiveEngine(cache=cache)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)

        await server.start()
        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                await ws.recv()  # welcome frame

                req = {
                    "type": "analyze",
                    "path": str(tmp_path / "non_existent.osu"),
                }
                await ws.send(json.dumps(req))

                raw_resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
                resp = json.loads(raw_resp)
                assert resp["type"] == "error"
                assert "not found" in resp["message"].lower()
        finally:
            await server.stop()

    asyncio.run(_run())


def test_websocket_clock_sync_broadcast_and_query(tmp_path):
    async def _run():
        port = _get_free_port()
        cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
        engine = LiveEngine(cache=cache)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)

        await server.start()
        try:
            ws_url = f"ws://127.0.0.1:{port}/ws"
            async with websockets.connect(ws_url) as ws:
                await ws.recv()  # welcome

                # 1. Query clock when idle
                await ws.send(json.dumps({"type": "get_clock"}))
                raw_idle = await asyncio.wait_for(ws.recv(), timeout=2.0)
                idle_clock = json.loads(raw_idle)
                assert idle_clock["type"] == "clock_sync"
                assert idle_clock["status"] == "idle"
                assert idle_clock["active"] is False

                # 2. Server broadcasts clock_sync playing
                sync_frame = {
                    "type": "clock_sync",
                    "status": "playing",
                    "active": True,
                    "start_ms": 12500.0,
                    "time_ms": 12500.0,
                    "rate": 1.0,
                    "server_time": 1726574000.0,
                }
                await server.broadcast(sync_frame)

                # 3. Client receives broadcast
                raw_broadcast = await asyncio.wait_for(ws.recv(), timeout=2.0)
                received = json.loads(raw_broadcast)
                assert received["type"] == "clock_sync"
                assert received["status"] == "playing"
                assert received["start_ms"] == 12500.0

                # 4. Subsequent query returns updated state
                await ws.send(json.dumps({"type": "get_clock"}))
                raw_query = await asyncio.wait_for(ws.recv(), timeout=2.0)
                queried = json.loads(raw_query)
                assert queried["type"] == "clock_sync"
                assert queried["status"] == "playing"
                assert queried["start_ms"] == 12500.0
        finally:
            await server.stop()

    asyncio.run(_run())


def test_http_overlay_mode_serving_and_offline_guard(tmp_path):
    from proj7k.live.guard import StaticAssetGuard

    async def _run():
        port = _get_free_port()
        cache = TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)
        engine = LiveEngine(cache=cache)
        server = LiveServer(host="127.0.0.1", port=port, engine=engine)

        await server.start()
        try:
            urls = [
                f"http://127.0.0.1:{port}/?mode=overlay",
                f"http://127.0.0.1:{port}/?mode=overlay&radar=1&strain=0&scale=1.2",
                f"http://127.0.0.1:{port}/index.html?mode=overlay&radar=0&strain=1",
            ]

            for url in urls:
                def _fetch():
                    with urllib.request.urlopen(url) as req:
                        assert req.status == 200
                        assert "text/html" in req.headers.get("Content-Type", "")
                        return req.read().decode("utf-8")

                body = await asyncio.to_thread(_fetch)
                assert "<canvas id=\"radarCanvas\"" in body
                assert "<canvas id=\"strainCanvas\"" in body
                assert "overlay-mode" in body
                # Assert 100% offline safety with StaticAssetGuard
                StaticAssetGuard.assert_offline_safe_content(body)
        finally:
            await server.stop()

    asyncio.run(_run())

