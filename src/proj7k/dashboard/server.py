"""
DashboardServer: the live radar server grown into one control panel for live, sync, downscaler and profiler.

ADR-0024. One port serves every page and one WebSocket carries both the live radar frames and the job frames:

- `job_start {kind, params, uploads?: {param: {name, data(base64)}}, request_id?}` queues a job; the sender gets
  `job_started {request_id, job}` and every client then gets `job_update {job}` and `job_log {id, line}` frames.
- `job_cancel {id}`, `jobs_list` (answered with `jobs {jobs}`), `job_logs {id}` (answered with `job_logs {id, logs}`).
- `open_artifact {id, index, reveal?}` hands a job's output to the desktop: an .osz opens in osu!lazer (its import),
  `reveal` opens the folder holding it. Only registered outputs can be opened, by index.

`game_chart {chart, unresolved}` goes to every client whenever the game selects another chart: `chart` is the 7K chart
osu!lazer has selected (title, stars, dan, and where it is in the library), `unresolved` the selection the live index
could not find (not 7K, or not in the library yet). The pages preselect it as the downscaler's input and the profiler's
replay source. `/api/current` answers the same.

HTTP: `/` is the dashboard shell (or the live page itself for `?mode=overlay`, the OBS URL ADR-0010 published),
`/live`, `/sync`, `/downscaler`, `/generator`, `/profiler` are its tools, `/home`, `/history`, `/library`, `/chart`
and `/play` its player pages (ADR-0034), `/api/*` is read-only state, and `/files/<job>/<index>/<name>` serves a
job's registered outputs.

The player pages read `/api/me/*` (`proj7k.dashboard.player`): `players`, `overview`, `history`, `library`,
`chart/<md5>`, `play/<id>`, and from osu!lazer's file store a chart's `cover/<md5>` and `audio/<md5>` and a play's
`replay/<id>` (.osr). They are answered off the event loop: the first one reads the library through the Node bridge.
`open_replay {play}` hands a play's replay to osu!lazer, which shows it.
"""

import asyncio
import base64
import json
import logging
import mimetypes
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote

from websockets.asyncio.server import ServerConnection
from websockets.http11 import Request, Response

from proj7k.dashboard.actions import DashboardConfig, build_actions
from proj7k.dashboard import player as player_api
from proj7k.dashboard.jobs import JobManager
from proj7k.live.engine import LiveEngine
from proj7k.live.server import LiveServer

logger = logging.getLogger("proj7k.dashboard.server")

DASHBOARD_STATIC_DIR = Path(__file__).parent / "static"

#: The dashboard's pages, by path, and the file each one is.
PAGES = {
    "/": "shell.html",
    "/index.html": "shell.html",
    "/sync": "sync.html",
    "/downscaler": "downscaler.html",
    "/generator": "generator.html",
    "/profiler": "profiler.html",
    "/home": "home.html",
    "/history": "history.html",
    "/library": "library.html",
    "/chart": "chart.html",
    "/play": "play.html",
}

#: Uploads ride the WebSocket base64-encoded, so this caps an upload at about 190 MB (a long lossless audio file fits).
MAX_MESSAGE_BYTES = 256 * 2**20

_FILES_PATH = re.compile(r"^/files/([\w.-]+)/(\d+)/([^/]+)$")
_PLAYER_API = re.compile(r"^/api/me/(players|overview|history|library|refresh|chart|play|cover|audio|replay|avatar)(?:/([\w.-]+))?$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "file"


def open_with_system(path: Path) -> None:
    """Hand a file or folder to the desktop's default handler (osu!lazer registers itself for .osz)."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class DashboardServer(LiveServer):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7770,
        engine: Optional[LiveEngine] = None,
        no_watch: bool = False,
        config: Optional[DashboardConfig] = None,
        jobs: Optional[JobManager] = None,
        static_dir: Optional[Path] = None,
        opener=open_with_system,
    ):
        super().__init__(host=host, port=port, engine=engine, no_watch=no_watch, max_size=MAX_MESSAGE_BYTES)
        self.config = config or DashboardConfig()
        self.dashboard_static_dir = static_dir or DASHBOARD_STATIC_DIR
        self.jobs = jobs or JobManager(build_actions(self.config), self.config.jobs_dir)
        self.jobs.notify = self._notify_from_thread
        self.opener = opener
        self.library = player_api.PlayerLibrary(
            self.config.realm_path, self.config.lazer_files_dir, db_path=self.config.db_path,
            cache_dir=self.config.output_dir,
        )
        # The chart pages' own engine: the live radar's is used from the event loop, these from worker threads.
        self._chart_engine = LiveEngine(max_mem_entries=64)
        self._profiles: Dict[Tuple[str, float], Dict[str, Any]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._game_chart: Dict[str, Any] = {"type": "game_chart", "chart": None, "unresolved": None}
        # Every page and asset is read once: the set of servable static paths is fixed at start.
        self._static: Dict[str, Tuple[bytes, str]] = {}
        for page_path, name in PAGES.items():
            self._static[page_path] = (self._read_static(name), "text/html; charset=utf-8")
        self._static["/live"] = (self._index_html, "text/html; charset=utf-8")
        for f in sorted(self.dashboard_static_dir.glob("*")):
            if f.is_file() and f.suffix in (".css", ".js"):
                ctype = "text/css; charset=utf-8" if f.suffix == ".css" else "text/javascript; charset=utf-8"
                self._static[f"/static/{f.name}"] = (f.read_bytes(), ctype)

    def _read_static(self, name: str) -> bytes:
        path = self.dashboard_static_dir / name
        if path.exists():
            return path.read_bytes()
        return f"<!DOCTYPE html><html><body><h1>proj7k</h1><p>{name} missing</p></body></html>".encode("utf-8")

    # --- job frames ----------------------------------------------------------------------------------------

    def _notify_from_thread(self, frame: Dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(frame), loop)
        except RuntimeError:
            pass

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await super().start()

    async def stop(self) -> None:
        self.jobs.shutdown()
        await super().stop()

    # --- HTTP ----------------------------------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """The library, database and output locations, and whether osu!lazer currently holds its database."""
        from proj7k.dan import CANONICAL_DAN_SR
        from proj7k.engine.skills import SKILL_TECH_KEY
        from proj7k.lazer.lock import probe_realm_lock

        cfg = self.config
        realm_exists = cfg.realm_path.exists()
        lazer_running: Optional[bool] = None
        if realm_exists:
            try:
                lazer_running = not probe_realm_lock(cfg.lazer_lock_path)
            except Exception:
                lazer_running = None
        jobs = self.jobs.list()
        return {
            "platform": platform.system(),
            "realm_path": str(cfg.realm_path),
            "realm_exists": realm_exists,
            "files_dir": str(cfg.lazer_files_dir),
            "lazer_running": lazer_running,
            "db_path": str(cfg.db_path) if cfg.db_path else None,
            "output_dir": str(cfg.output_dir),
            "practice_dir": str(cfg.practice_dir),
            "generated_dir": str(cfg.generated_dir),
            "live_watch": not self.no_watch,
            "dan_tiers": [{"name": k, "stars": round(v, 2)} for k, v in CANONICAL_DAN_SR.items()],
            "skills": list(SKILL_TECH_KEY.values()),
            "active_jobs": sum(1 for j in jobs if not j.finished),
        }

    def _players(self) -> Dict[str, Any]:
        from proj7k.profiler.storage import ProfilerStorage

        with ProfilerStorage(db_path=self.config.db_path) as storage:
            return {"players": storage.list_players()}

    def _process_request(self, connection: ServerConnection, request: Request):
        path, _, query = request.path.partition("?")
        match = _FILES_PATH.match(path)
        if match:
            return self._serve_artifact(match.group(1), int(match.group(2)), unquote(match.group(3)), request)
        match = _PLAYER_API.match(path)
        if match:
            # websockets awaits a coroutine here; the work runs on a thread so the live radar keeps its frames.
            return asyncio.to_thread(self._player_route, match.group(1), unquote(match.group(2) or ""), query, request)
        return super()._process_request(connection, request)

    # --- the player pages (ADR-0034) ----------------------------------------------------------------------------

    def _profile_summary(self, player: str) -> Optional[Dict[str, Any]]:
        """The profiler's all-time and 30-day levels for the home page, recomputed when its database changes."""
        from proj7k.profiler.aggregate import aggregate_macro_profile
        from proj7k.profiler.storage import ProfilerStorage

        db = self.config.db_path or ProfilerStorage().db_path
        try:
            mtime = Path(db).stat().st_mtime
        except OSError:
            return None
        key = (player.lower(), mtime)
        if key not in self._profiles:
            out: Dict[str, Any] = {}
            with ProfilerStorage(db_path=self.config.db_path) as storage:
                for name, horizon in (("all_time", None), ("recent", 30.0)):
                    p = aggregate_macro_profile(storage=storage, player_name=player, horizon_days=horizon)
                    if p.total_matches:
                        out[name] = {
                            "matches": p.total_matches,
                            "overall_dan": p.overall_dan,
                            "overall_stars": round(p.overall_star_rating, 2),
                            "dominant": p.dominant_technique,
                            "bottleneck": p.bottleneck_technique,
                            "average_ur": p.average_ur,
                            "skills": {k: round(d.star_rating, 2) for k, d in p.dimensions.items()},
                        }
            self._profiles = {key: out}
        return self._profiles[key] or None

    def _player_route(self, what: str, arg: str, query: str, request: Request) -> Response:
        params = {k: v[-1] for k, v in parse_qs(query).items()}
        try:
            snapshot = self.library.snapshot(force=what == "refresh")
            player = params.get("player") or snapshot.default_player() or ""
            not_found = self._json_response({"error": "没有找到"}, 404, "Not Found")
            if what in ("players", "refresh"):
                return self._json_response({
                    "players": snapshot.players(), "default": snapshot.default_player(), "error": snapshot.error,
                    "charts": len(snapshot.charts), "plays": len(snapshot.plays),
                })
            profile = None
            if what in ("overview", "library") and player:
                try:
                    profile = self._profile_summary(player)
                except Exception as e:
                    logger.info(f"Profiler summary unavailable: {e}")
            if what == "overview":
                return self._json_response({**player_api.overview(snapshot, player, profile=profile), "error": snapshot.error})
            if what == "history":
                return self._json_response({**player_api.history(snapshot, player), "error": snapshot.error})
            if what == "library":
                level = profile["all_time"]["overall_stars"] if profile and "all_time" in profile else None
                return self._json_response({**player_api.library(snapshot, player), "level": level, "error": snapshot.error})
            if what == "chart":
                detail = player_api.chart_detail(snapshot, arg, player, self.config.lazer_files_dir, self._chart_engine)
                return self._json_response(detail) if detail else not_found
            if what == "play":
                detail = player_api.play_detail(snapshot, arg)
                return self._json_response(detail) if detail else not_found
            if what in ("cover", "audio"):
                chart = snapshot.charts.get(arg)
                file_hash = chart and chart["background_hash" if what == "cover" else "audio_hash"]
                target = player_api.store_path(self.config.lazer_files_dir, file_hash) if file_hash else None
                if target is None or not target.is_file():
                    return self._response(404, "Not Found", b"Not Found", "text/plain; charset=utf-8")
                if what == "cover":
                    width = max(64, min(1920, int(params.get("w") or 480)))
                    body, ctype = player_api.thumbnail(target, width)
                    return self._response(200, "OK", body, ctype, [("Cache-Control", "max-age=86400")])
                return self._file_response(target, request, "audio/mpeg")
            if what == "avatar":
                body = self._avatar(arg)
                if body is None:
                    return self._response(404, "Not Found", b"Not Found", "text/plain; charset=utf-8")
                return self._response(200, "OK", body, "image/png", [("Cache-Control", "max-age=86400")])
            if what == "replay":
                play = snapshot.play(arg)
                target = player_api.store_path(self.config.lazer_files_dir, play["replay_hash"]) if play and play["replay_hash"] else None
                if target is None or not target.is_file():
                    return self._response(404, "Not Found", b"Not Found", "text/plain; charset=utf-8")
                name = _safe_filename(f"{play['player_name']} - {(snapshot.charts.get(play['md5']) or {}).get('title', 'replay')} ({play['date'][:10]}).osr")
                return self._response(200, "OK", target.read_bytes(), "application/octet-stream", [
                    ("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name, safe='')}"),
                ])
        except Exception as e:
            logger.warning(f"/api/me/{what} failed: {e}", exc_info=True)
            return self._json_response({"error": str(e)}, 500, "Internal Server Error")
        return self._response(404, "Not Found", b"Not Found", "text/plain; charset=utf-8")

    def _avatar(self, user_id: str) -> Optional[bytes]:
        """The player's osu! avatar, fetched once from osu!'s public avatar host and kept beside the outputs."""
        if not user_id.isdigit() or int(user_id) <= 0:
            return None
        cached = self.config.output_dir / "avatars" / f"{user_id}.img"
        if cached.is_file() and cached.stat().st_size:
            return cached.read_bytes()
        import urllib.request

        try:
            with urllib.request.urlopen(f"{player_api.AVATAR_WEB}/{user_id}", timeout=5) as res:
                body = res.read(4 * 2**20)
        except Exception as e:
            logger.info(f"No avatar for user {user_id}: {e}")
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(body)
        return body

    def _open_replay(self, play_id: str) -> Path:
        """A play's replay copied out of lazer's store as an .osr (the store's files have no extension), then opened."""
        snapshot = self.library.snapshot()
        play = snapshot.play(play_id)
        if play is None or not play["replay_hash"]:
            raise LookupError("这局没有保存回放")
        source = player_api.store_path(self.config.lazer_files_dir, play["replay_hash"])
        if not source.is_file():
            raise FileNotFoundError(f"osu!lazer 的文件里找不到这局的回放：{source}")
        out_dir = self.config.output_dir / "replays"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / _safe_filename(f"{play['replay_hash'][:12]}.osr")
        if not target.exists():
            shutil.copyfile(source, target)
        self.opener(target)
        return target

    def _route(self, path: str, query: str) -> Optional[Response]:
        if path in ("/", "/index.html") and parse_qs(query).get("mode") == ["overlay"]:
            path = "/live"
        if path in self._static:
            body, ctype = self._static[path]
            return self._response(200, "OK", body, ctype, [("Cache-Control", "no-cache")])
        if path == "/api/config":
            return self._json_response(self.status())
        if path == "/api/players":
            try:
                return self._json_response(self._players())
            except Exception as e:
                return self._json_response({"players": [], "error": str(e)})
        if path == "/api/current":
            return self._json_response(self._game_chart)
        if path == "/api/jobs":
            return self._json_response({"jobs": [j.to_dict() for j in self.jobs.list()]})
        if path.startswith("/api/jobs/"):
            job = self.jobs.get(path.rsplit("/", 1)[-1])
            if job is None:
                return self._json_response({"error": "no such job"}, 404, "Not Found")
            return self._json_response(job.to_dict(with_logs=True))
        return super()._route(path, query)

    def _serve_artifact(self, job_id: str, index: int, name: str, request: Request) -> Response:
        """
        A job's registered output. `name` must be that output's name, or the name of another output of the same job
        in the same folder: the replay viewer loads its audio by a path relative to the page.
        """
        job = self.jobs.get(job_id)
        target: Optional[Path] = None
        if job is not None and 0 <= index < len(job.artifacts):
            anchor = job.artifacts[index].path
            if anchor.name == name:
                target = anchor
            else:
                target = next(
                    (a.path for a in job.artifacts if a.path.name == name and a.path.parent == anchor.parent), None,
                )
        if target is None or not target.is_file():
            return self._response(404, "Not Found", b"Not Found", "text/plain; charset=utf-8")

        extra = []
        if target.suffix.lower() in (".osz", ".osu"):
            extra.append(("Content-Disposition", f"attachment; filename*=UTF-8''{quote(target.name, safe='')}"))
        return self._file_response(target, request, extra_headers=extra)

    def _file_response(
        self, target: Path, request: Request, default_type: str = "application/octet-stream", extra_headers=(),
    ) -> Response:
        """A file, with byte ranges: audio seeking needs them."""
        ctype = mimetypes.guess_type(target.name)[0] or default_type
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        headers = [("Accept-Ranges", "bytes"), ("Cache-Control", "no-cache"), *extra_headers]
        data = target.read_bytes()

        range_header = request.headers.get("Range")
        m = _RANGE.match(range_header or "")
        if m and (m.group(1) or m.group(2)) and data:
            size = len(data)
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start, end = max(0, size - int(m.group(2))), size - 1
            if start > end or start >= size:
                return self._response(416, "Range Not Satisfiable", b"", None, [("Content-Range", f"bytes */{size}")])
            headers.append(("Content-Range", f"bytes {start}-{end}/{size}"))
            return self._response(206, "Partial Content", data[start:end + 1], ctype, headers)
        return self._response(200, "OK", data, ctype, headers)

    # --- WebSocket -----------------------------------------------------------------------------------------

    async def _handle_message(self, connection: ServerConnection, data: Dict[str, Any]) -> None:
        msg_type = data.get("type")

        if msg_type == "job_start":
            uploads: Dict[str, Tuple[str, bytes]] = {}
            for key, item in (data.get("uploads") or {}).items():
                uploads[str(key)] = (str(item.get("name") or key), base64.b64decode(item.get("data") or ""))
            try:
                job = self.jobs.submit(str(data.get("kind")), data.get("params") or {}, uploads)
            except ValueError as e:
                await connection.send(json.dumps({"type": "error", "request_id": data.get("request_id"), "message": str(e)}))
                return
            await connection.send(json.dumps(
                {"type": "job_started", "request_id": data.get("request_id"), "job": job.to_dict()}, ensure_ascii=False,
            ))

        elif msg_type == "job_cancel":
            self.jobs.cancel(str(data.get("id")))

        elif msg_type == "jobs_list":
            await connection.send(json.dumps(
                {"type": "jobs", "jobs": [j.to_dict() for j in self.jobs.list()]}, ensure_ascii=False,
            ))

        elif msg_type == "job_logs":
            job = self.jobs.get(str(data.get("id")))
            await connection.send(json.dumps(
                {"type": "job_logs", "id": data.get("id"), "logs": list(job.logs) if job else []}, ensure_ascii=False,
            ))

        elif msg_type == "open_artifact":
            job = self.jobs.get(str(data.get("id")))
            index = data.get("index")
            if job is None or not isinstance(index, int) or not 0 <= index < len(job.artifacts):
                await connection.send(json.dumps({"type": "error", "message": "没有这个输出文件"}, ensure_ascii=False))
                return
            path = job.artifacts[index].path
            target = path.parent if data.get("reveal") else path
            try:
                await asyncio.to_thread(self.opener, target)
            except Exception as e:
                await connection.send(json.dumps({"type": "error", "message": f"无法打开 {target}: {e}"}, ensure_ascii=False))

        elif msg_type == "open_replay":
            try:
                await asyncio.to_thread(self._open_replay, str(data.get("play") or ""))
            except Exception as e:
                await connection.send(json.dumps({"type": "error", "message": f"无法在 osu! 里打开回放：{e}"}, ensure_ascii=False))

        else:
            await super()._handle_message(connection, data)

    async def broadcast(self, frame: Dict[str, Any]) -> None:
        if frame.get("type") in ("job_update", "job_log"):
            payload = json.dumps(frame, ensure_ascii=False)
            if self._active_connections:
                await asyncio.gather(
                    *(conn.send(payload) for conn in list(self._active_connections)), return_exceptions=True,
                )
            return
        await super().broadcast(frame)
        game_chart = self._game_chart_frame(frame)
        if game_chart is not None:
            self._game_chart = game_chart
            await super().broadcast(game_chart)

    @staticmethod
    def _game_chart_frame(frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The `game_chart` frame a live frame implies: only the game's own selections count, not a dropped file."""
        if frame.get("type") == "beatmap_update":
            meta = frame.get("metadata") or {}
            lazer = meta.get("lazer")
            if not lazer:
                return None
            return {"type": "game_chart", "unresolved": None, "chart": {
                "title": meta.get("title", ""),
                "artist": meta.get("artist", ""),
                "creator": meta.get("creator", ""),
                "version": meta.get("version", ""),
                "star_rating": round(float(frame.get("star_rating") or 0.0), 3),
                "dan_tier": frame.get("dan_tier"),
                "dominant_technique": meta.get("dominant_technique"),
                "total_notes": meta.get("total_notes"),
                "duration_seconds": meta.get("duration_seconds"),
                **lazer,
            }}
        if frame.get("type") == "error" and frame.get("code") == "beatmap_not_found":
            return {"type": "game_chart", "chart": None, "unresolved": frame.get("event")}
        return None

