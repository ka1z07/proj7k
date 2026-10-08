"""
LiveServer: Unified HTTP and WebSocket server for real-time radar streaming.

SPEC-P2.4-01 / ADR-0010.
"""

import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from proj7k.live.clock import ClockState
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
        max_size: Optional[int] = 2**20,
    ):
        self.host = host
        self.port = port
        self.engine = engine if engine is not None else LiveEngine()
        self.no_watch = no_watch
        self.static_dir = static_dir if static_dir is not None else DEFAULT_STATIC_DIR
        #: Largest WebSocket message accepted, in bytes (websockets' own default is 1 MiB).
        self.max_size = max_size

        index_path = self.static_dir / "index.html"
        if index_path.exists():
            self._index_html = index_path.read_bytes()
        else:
            self._index_html = b"<!DOCTYPE html><html><body><h1>proj7k Live</h1></body></html>"

        self._active_connections: Set[ServerConnection] = set()
        self._current_beatmap: Optional[Dict[str, Any]] = None
        self._current_clock: Optional[Dict[str, Any]] = None
        self._server: Optional[Server] = None

    @staticmethod
    def _response(
        status: int,
        reason: str,
        body: bytes = b"",
        content_type: Optional[str] = None,
        extra_headers: Sequence[Tuple[str, str]] = (),
    ) -> Response:
        headers = []
        if content_type:
            headers.append(("Content-Type", content_type))
        headers.extend(extra_headers)
        headers.append(("Content-Length", str(len(body))))
        headers.append(("Connection", "close"))
        return Response(status_code=status, reason_phrase=reason, headers=Headers(headers), body=body)

    @classmethod
    def _json_response(cls, payload: Any, status: int = 200, reason: str = "OK") -> Response:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return cls._response(status, reason, body, "application/json; charset=utf-8")

    @staticmethod
    def _same_origin(request: Request) -> bool:
        """
        A browser names the page that opened a WebSocket in `Origin`; any web page can open one to 127.0.0.1, so
        only this server's own pages may (non-browser clients send no Origin and are let through).
        """
        origin = request.headers.get("Origin")
        if origin is None:
            return True
        return urlsplit(origin).netloc.lower() == (request.headers.get("Host") or "").lower()

    def _process_request(self, connection: ServerConnection, request: Request) -> Optional[Response]:
        """
        Intercepts HTTP requests prior to WebSocket handshake.
        If path is WebSocket endpoint, returns None to proceed with handshake.
        """
        path, _, query = request.path.partition("?")

        if path.startswith("/ws"):
            if not self._same_origin(request):
                return self._response(403, "Forbidden", b"Cross-origin WebSocket refused", "text/plain; charset=utf-8")
            return None

        response = self._route(path, query)
        if response is not None:
            return response
        return self._response(404, "Not Found", b"Not Found", "text/plain; charset=utf-8")

    def _route(self, path: str, query: str) -> Optional[Response]:
        """The HTTP response for `path`, or None for 404. Subclasses add pages and fall back to this."""
        if path in ("/", "/index.html"):
            return self._response(200, "OK", self._index_html, "text/html; charset=utf-8")

        if path == "/api/status":
            return self._json_response({
                "status": "ok",
                "connected_clients": len(self._active_connections),
                "no_watch": self.no_watch,
            })

        if path == "/favicon.ico":
            return self._response(204, "No Content")

        return None

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

            # If current clock exists and is playing, replay clock snapshot
            if self._current_clock is not None and self._current_clock.get("status") == "playing":
                await connection.send(json.dumps(self._current_clock))

            async for raw_message in connection:
                try:
                    data = json.loads(raw_message)
                    await self._handle_message(connection, data)
                except Exception as e:
                    logger.error(f"Error handling message: {e}", exc_info=True)
                    err = {"type": "error", "message": str(e)}
                    await connection.send(json.dumps(err))

        finally:
            self._active_connections.discard(connection)
            logger.debug(f"Client disconnected: {connection.remote_address}")

    async def _handle_message(self, connection: ServerConnection, data: Dict[str, Any]) -> None:
        """Handle one client message. Subclasses take their own message types and fall back to this."""
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

        elif msg_type in ("clock_sync", "get_clock"):
            clock_frame = self._current_clock or ClockState.create_idle().to_dict()
            await connection.send(json.dumps(clock_frame))

    async def broadcast(self, frame: Dict[str, Any]) -> None:
        """Broadcasts a state frame to all currently connected WebSocket clients."""
        if frame.get("type") == "beatmap_update":
            self._current_beatmap = frame
        elif frame.get("type") == "clock_sync":
            self._current_clock = frame
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
            max_size=self.max_size,
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

