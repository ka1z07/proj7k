"""
LiveServer: Unified HTTP and WebSocket server for real-time radar streaming.

SPEC-P2.4-01 / ADR-0010.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from proj7k.live.engine import LiveEngine

logger = logging.getLogger("proj7k.live.server")

DEFAULT_STATIC_DIR = Path(__file__).parent / "static"


class LiveServer:
    """
    Lightweight asynchronous service providing HTTP dashboard serving
    and WebSocket bidirection broadcasting on a single port.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7770,
        engine: Optional[LiveEngine] = None,
        no_watch: bool = False,
        static_dir: Optional[Path] = None,
    ):
        self.host = host
        self.port = port
        self.engine = engine if engine is not None else LiveEngine()
        self.no_watch = no_watch
        self.static_dir = static_dir if static_dir is not None else DEFAULT_STATIC_DIR

        index_path = self.static_dir / "index.html"
        if index_path.exists():
            self._index_html = index_path.read_bytes()
        else:
            self._index_html = b"<!DOCTYPE html><html><body><h1>proj7k Live</h1></body></html>"

        self._active_connections: Set[ServerConnection] = set()
        self._current_beatmap: Optional[Dict[str, Any]] = None
        self._server: Optional[Server] = None

    def _process_request(self, connection: ServerConnection, request: Request) -> Optional[Response]:
        """
        Intercepts HTTP requests prior to WebSocket handshake.
        If path is WebSocket endpoint, returns None to proceed with handshake.
        """
        path = request.path.split("?")[0]

        if path.startswith("/ws"):
            return None

        if path in ("/", "/index.html"):
            return Response(
                status_code=200,
                reason_phrase="OK",
                headers=Headers([
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("Content-Length", str(len(self._index_html))),
                    ("Connection", "close"),
                ]),
                body=self._index_html,
            )

        if path == "/api/status":
            body = json.dumps({
                "status": "ok",
                "connected_clients": len(self._active_connections),
                "no_watch": self.no_watch,
            }).encode("utf-8")
            return Response(
                status_code=200,
                reason_phrase="OK",
                headers=Headers([
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                    ("Connection", "close"),
                ]),
                body=body,
            )

        not_found = b"Not Found"
        return Response(
            status_code=404,
            reason_phrase="Not Found",
            headers=Headers([
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(not_found))),
                ("Connection", "close"),
            ]),
            body=not_found,
        )

    async def _handle_ws(self, connection: ServerConnection) -> None:
        """Handles lifecycle and incoming messages for a connected WebSocket client."""
        self._active_connections.add(connection)
        logger.debug(f"Client connected: {connection.remote_address}")

        try:
            # Send initial welcome state frame
            welcome_frame = {
                "type": "welcome",
                "status": "ready",
                "version": "2.4.0",
                "message": "Connected to proj7k.live radar stream",
            }
            await connection.send(json.dumps(welcome_frame))

            # If current beatmap exists, replay it to newly connected client
            if self._current_beatmap is not None:
                await connection.send(json.dumps(self._current_beatmap))

            async for raw_message in connection:
                try:
                    data = json.loads(raw_message)
                    msg_type = data.get("type")

                    if msg_type == "analyze_content":
                        content = data.get("content", "")
                        frame = self.engine.analyze_content(content)
                        await self.broadcast(frame)

                    elif msg_type == "analyze":
                        file_path = Path(data.get("path", ""))
                        if not file_path.exists():
                            err = {"type": "error", "message": f"File not found: {file_path}"}
                            await connection.send(json.dumps(err))
                        else:
                            frame = self.engine.analyze_file(file_path)
                            await self.broadcast(frame)

                    elif msg_type == "ping":
                        await connection.send(json.dumps({"type": "pong"}))

                except Exception as e:
                    logger.error(f"Error handling message: {e}", exc_info=True)
                    err = {"type": "error", "message": str(e)}
                    await connection.send(json.dumps(err))

        finally:
            self._active_connections.discard(connection)
            logger.debug(f"Client disconnected: {connection.remote_address}")

    async def broadcast(self, frame: Dict[str, Any]) -> None:
        """Broadcasts a state frame to all currently connected WebSocket clients."""
        if frame.get("type") == "beatmap_update":
            self._current_beatmap = frame
        payload = json.dumps(frame)

        if self._active_connections:
            send_tasks = [conn.send(payload) for conn in list(self._active_connections)]
            await asyncio.gather(*send_tasks, return_exceptions=True)

    async def start(self) -> None:
        """Starts the unified HTTP and WebSocket server."""
        self._server = await serve(
            self._handle_ws,
            self.host,
            self.port,
            process_request=self._process_request,
        )
        logger.info(f"proj7k.live server listening on http://{self.host}:{self.port}/")

    async def stop(self) -> None:
        """Gracefully stops the server and terminates client connections."""
        for conn in list(self._active_connections):
            try:
                await conn.close()
            except Exception:
                pass
        self._active_connections.clear()

        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=1.0)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            self._server = None

