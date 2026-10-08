"""
Tests for proj7k.dashboard (ADR-0024): the job queue, the pages and APIs, the job protocol over the WebSocket, the
download route, the same-origin guard, and the actions run end to end on real charts.
"""

import asyncio
import base64
import json
import logging
from pathlib import Path
import socket
import threading
import urllib.error
import urllib.request

import pytest
import websockets

from proj7k.cache import TwoLayerCache
from proj7k.dashboard import DashboardConfig, DashboardServer, JobManager
from proj7k.dashboard.cli import build_config, build_parser
from proj7k.dashboard.server import DASHBOARD_STATIC_DIR
from proj7k.live.engine import LiveEngine
from proj7k.live.guard import StaticAssetGuard

REPO = Path(__file__).resolve().parents[1]
SAMPLE_OSU = REPO / "docs" / "sample_7k.osu"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _config(tmp_path: Path) -> DashboardConfig:
    return DashboardConfig(
        realm_path=tmp_path / "lazer" / "client.realm",
        cache_dir=tmp_path / "cache",
        db_path=tmp_path / "profiler.db",
        output_dir=tmp_path / "dashboard",
    )


def _wait(job, timeout=10.0):
    for _ in range(int(timeout / 0.01)):
        if job.finished:
            return job
        threading.Event().wait(0.01)
    raise AssertionError(f"job {job.id} did not finish: {job.status}")


# --- JobManager ------------------------------------------------------------------------------------------------


def test_job_manager_runs_actions_in_order_and_captures_logs_and_outputs(tmp_path):
    order = []
    log = logging.getLogger("proj7k.test_dashboard")
    log.setLevel(logging.INFO)

    def write(params, ctx):
        order.append(params["n"])
        log.info(f"writing {params['n']}")
        out = ctx.workdir / f"out{params['n']}.txt"
        out.write_text("x", encoding="utf-8")
        ctx.add_artifact(out, "text")
        return {"n": params["n"]}

    frames = []
    manager = JobManager({"write": write}, tmp_path / "jobs", notify=frames.append)
    try:
        jobs = [manager.submit("write", {"n": i}) for i in range(3)]
        for job in jobs:
            _wait(job)
        assert order == [0, 1, 2]
        assert all(j.status == "done" for j in jobs)
        assert jobs[1].result == {"n": 1}
        assert any("writing 1" in line for line in jobs[1].logs)
        assert not any("writing 1" in line for line in jobs[0].logs)
        assert jobs[2].artifacts[0].path.read_text(encoding="utf-8") == "x"
        assert jobs[2].to_dict()["artifacts"][0]["url"] == f"/files/{jobs[2].id}/0/out2.txt"
        assert {"job_update", "job_log"} <= {f["type"] for f in frames}
    finally:
        manager.shutdown()


def test_job_manager_reports_failures_and_writes_uploads(tmp_path):
    def boom(params, ctx):
        raise RuntimeError("osu!lazer database not found")

    def read(params, ctx):
        return {"content": Path(params["file"]).read_text(encoding="utf-8"), "path": params["file"]}

    manager = JobManager({"boom": boom, "read": read}, tmp_path / "jobs")
    try:
        failed = _wait(manager.submit("boom"))
        assert failed.status == "failed"
        assert failed.error == "osu!lazer database not found"

        job = manager.submit("read", {}, uploads={"file": ("../evil/map.osu", "hello".encode("utf-8"))})
        assert job.params["file"] == "../evil/map.osu"  # what clients see before it runs: the name only
        _wait(job)
        assert job.result["content"] == "hello"
        assert Path(job.result["path"]).parent == job.workdir

        with pytest.raises(ValueError):
            manager.submit("nope")
    finally:
        manager.shutdown()


def test_job_manager_cancels_a_queued_job(tmp_path):
    gate = threading.Event()
    manager = JobManager({"wait": lambda p, c: gate.wait(5), "noop": lambda p, c: {}}, tmp_path / "jobs")
    try:
        first = manager.submit("wait")
        second = manager.submit("noop")
        assert manager.cancel(second.id)
        gate.set()
        _wait(first)
        assert second.status == "cancelled"
        assert second.result is None
    finally:
        manager.shutdown()


# --- server ----------------------------------------------------------------------------------------------------


class _Server:
    """A dashboard on a free port, with fake or real actions."""

    def __init__(self, tmp_path, actions=None, opener=None):
        self.port = _free_port()
        self.config = _config(tmp_path)
        jobs = JobManager(actions, self.config.jobs_dir) if actions is not None else None
        self.opened = []
        self.server = DashboardServer(
            port=self.port,
            engine=LiveEngine(cache=TwoLayerCache(cache_dir=tmp_path / "cache", enabled=False)),
            no_watch=True,
            config=self.config,
            jobs=jobs,
            opener=opener or self.opened.append,
        )

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    async def get(self, path, headers=None):
        def _fetch():
            req = urllib.request.Request(self.url(path), headers=headers or {})
            try:
                with urllib.request.urlopen(req) as res:
                    return res.status, dict(res.headers), res.read()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read()

        return await asyncio.to_thread(_fetch)

    def ws(self, **kwargs):
        return websockets.connect(f"ws://127.0.0.1:{self.port}/ws", max_size=None, **kwargs)


async def _run_job(ws, kind, params, uploads=None):
    await ws.send(json.dumps({"type": "job_start", "kind": kind, "params": params, "uploads": uploads or {}, "request_id": "r1"}))
    job_id = None
    logs = []
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
        if msg["type"] == "job_started":
            assert msg["request_id"] == "r1"
            job_id = msg["job"]["id"]
        elif msg["type"] == "job_log" and msg["id"] == job_id:
            logs.append(msg["line"])
        elif msg["type"] == "job_update" and msg["job"]["id"] == job_id and msg["job"]["status"] in ("done", "failed", "cancelled"):
            return msg["job"], logs


def test_pages_apis_and_the_overlay_url(tmp_path):
    async def _run():
        s = _Server(tmp_path)
        await s.server.start()
        try:
            for path, marker in (("/", "proj7k // 控制台"), ("/sync", "曲库同步"), ("/downscaler", "降阶练习"),
                                 ("/profiler", "回放与画像"), ("/live", 'id="radarCanvas"')):
                status, headers, body = await s.get(path)
                assert status == 200, path
                assert headers["Content-Type"].startswith("text/html")
                assert marker in body.decode("utf-8"), path

            # The OBS overlay URL ADR-0010 published still lands on the live radar itself.
            _, _, overlay = await s.get("/?mode=overlay&radar=1")
            assert 'id="radarCanvas"' in overlay.decode("utf-8")

            status, headers, js = await s.get("/static/app.js")
            assert status == 200 and headers["Content-Type"].startswith("text/javascript")
            assert (await s.get("/static/../server.py"))[0] == 404

            _, _, cfg = await s.get("/api/config")
            cfg = json.loads(cfg)
            assert cfg["realm_exists"] is False and cfg["lazer_running"] is None
            assert {t["name"] for t in cfg["dan_tiers"]} >= {"0th", "7th", "Stellium"}
            assert len(cfg["skills"]) == 8

            assert json.loads((await s.get("/api/players"))[2]) == {"players": []}
            assert json.loads((await s.get("/api/jobs"))[2]) == {"jobs": []}
            assert (await s.get("/api/jobs/missing"))[0] == 404
            assert (await s.get("/api/status"))[0] == 200  # the live server's own route still answers
        finally:
            await s.server.stop()

    asyncio.run(_run())


def test_dashboard_static_assets_are_offline_safe():
    report = StaticAssetGuard.scan_directory(DASHBOARD_STATIC_DIR)
    assert sum(len(v) for v in report.values()) == 0, report
    for f in DASHBOARD_STATIC_DIR.iterdir():
        text = f.read_text(encoding="utf-8").lower()
        assert "http://" not in text and "https://" not in text, f.name


def test_websocket_refuses_other_origins(tmp_path):
    async def _run():
        s = _Server(tmp_path)
        await s.server.start()
        try:
            with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
                async with s.ws(origin="http://evil.example"):
                    pass
            assert exc.value.response.status_code == 403

            async with s.ws(origin=f"http://127.0.0.1:{s.port}") as ws:
                assert json.loads(await ws.recv())["type"] == "welcome"
        finally:
            await s.server.stop()

    asyncio.run(_run())


def test_job_protocol_downloads_ranges_and_open(tmp_path):
    def make(params, ctx):
        logging.getLogger("proj7k.test_dashboard").warning("making files")
        folder = ctx.workdir / "views"
        folder.mkdir()
        (folder / "view.html").write_text("<audio src='song.mp3'>", encoding="utf-8")
        (folder / "song.mp3").write_bytes(bytes(range(100)))
        (ctx.workdir / "secret.txt").write_text("no", encoding="utf-8")
        (ctx.workdir / "practice.osz").write_bytes(b"PK")
        ctx.add_artifact(folder / "view.html", "viewer")
        ctx.add_artifact(folder / "song.mp3", "audio")
        ctx.add_artifact(ctx.workdir / "practice.osz", "osz")
        return {"ok": params["x"]}

    async def _run():
        s = _Server(tmp_path, actions={"make": make})
        await s.server.start()
        try:
            async with s.ws() as ws:
                await ws.recv()  # welcome
                job, logs = await _run_job(ws, "make", {"x": 1})
                assert job["status"] == "done" and job["result"] == {"ok": 1}
                assert any("making files" in line for line in logs)
                view, audio, osz = job["artifacts"]
                assert osz["kind"] == "osz"

                status, _, body = await s.get(view["url"])
                assert status == 200 and b"song.mp3" in body
                # The page's relative audio link resolves to its sibling output...
                status, headers, body = await s.get(view["url"].rsplit("/", 1)[0] + "/song.mp3")
                assert status == 200 and body == bytes(range(100))
                # ...with byte ranges for seeking.
                status, headers, body = await s.get(audio["url"], {"Range": "bytes=10-19"})
                assert status == 206 and body == bytes(range(10, 20))
                assert headers["Content-Range"] == "bytes 10-19/100"
                # Anything not registered is not served.
                for bad in (f"/files/{job['id']}/0/secret.txt", f"/files/{job['id']}/0/..%2Fsecret.txt",
                            f"/files/{job['id']}/9/view.html", "/files/nope/0/view.html"):
                    assert (await s.get(bad))[0] == 404, bad
                status, headers, _ = await s.get(osz["url"])
                assert "attachment" in headers["Content-Disposition"]

                await ws.send(json.dumps({"type": "open_artifact", "id": job["id"], "index": 2}))
                await ws.send(json.dumps({"type": "open_artifact", "id": job["id"], "index": 0, "reveal": True}))
                await ws.send(json.dumps({"type": "jobs_list"}))
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert msg["type"] == "jobs" and msg["jobs"][0]["id"] == job["id"]
                assert [p.name for p in s.opened] == ["practice.osz", "views"]

                await ws.send(json.dumps({"type": "job_start", "kind": "nope", "request_id": "bad"}))
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert msg["type"] == "error" and msg["request_id"] == "bad"

                # The live radar still works on the same socket.
                await ws.send(json.dumps({"type": "analyze_content", "content": SAMPLE_OSU.read_text(encoding="utf-8")}))
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                assert msg["type"] == "beatmap_update" and msg["star_rating"] > 0
        finally:
            await s.server.stop()

    asyncio.run(_run())


# --- real actions ----------------------------------------------------------------------------------------------


def test_downscale_action_on_an_uploaded_chart(tmp_path):
    async def _run():
        s = _Server(tmp_path)
        await s.server.start()
        try:
            async with s.ws() as ws:
                await ws.recv()
                upload = {"input": {"name": "sample.osu", "data": base64.b64encode(SAMPLE_OSU.read_bytes()).decode()}}
                job, _ = await _run_job(ws, "downscale", {"target_sr": "1.2", "package": True}, upload)
                assert job["status"] == "done", job["error"]
                chart = job["result"]["charts"][0]
                assert chart["downscaled_stars"] < chart["original_stars"]
                assert chart["downscaled_notes"] < chart["original_notes"]
                assert set(chart["skills_original"]) == set(chart["skills_downscaled"]) and len(chart["skills_original"]) == 8
                kinds = sorted(a["kind"] for a in job["artifacts"])
                assert kinds == ["osu", "osz"]
                assert all(Path(a["path"]).parent == s.config.practice_dir for a in job["artifacts"])
                status, _, body = await s.get([a for a in job["artifacts"] if a["kind"] == "osu"][0]["url"])
                assert status == 200 and b"[HitObjects]" in body

                job, _ = await _run_job(ws, "downscale", {"input": str(tmp_path / "missing.osu"), "target_dan": "3rd"})
                assert job["status"] == "failed" and "does not exist" in job["error"]
                job, _ = await _run_job(ws, "downscale", {"input": str(SAMPLE_OSU)})
                assert job["status"] == "failed" and "目标" in job["error"]
        finally:
            await s.server.stop()

    asyncio.run(_run())


def test_profiler_and_sync_actions_without_a_library(tmp_path):
    async def _run():
        s = _Server(tmp_path)
        await s.server.start()
        try:
            async with s.ws() as ws:
                await ws.recv()
                job, _ = await _run_job(ws, "profile_player", {"player": "nobody", "horizon_days": "7"})
                assert job["status"] == "done"
                assert job["result"]["profile"]["total_matches"] == 0
                assert job["result"]["profile"]["horizon_days"] == 7.0

                job, _ = await _run_job(ws, "sync_once", {"dry_run": True})
                assert job["status"] == "failed" and "not found" in job["error"]
                job, _ = await _run_job(ws, "import_replays", {"player": "nobody"})
                assert job["status"] == "failed" and "not found" in job["error"]
                job, _ = await _run_job(ws, "profile_replay", {"source": "files", "replay": str(tmp_path / "x.osr"), "beatmap": str(SAMPLE_OSU)})
                assert job["status"] == "failed" and "回放文件不存在" in job["error"]
        finally:
            await s.server.stop()

    asyncio.run(_run())


def _perfect_replay(osu_path: Path, player: str) -> bytes:
    """An .osr of `player` hitting every note of `osu_path` on time."""
    import hashlib

    from proj7k.parser import parse_osu_7k
    from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr

    beatmap = parse_osu_7k(str(osu_path))
    events = []
    for ho in beatmap.hit_objects:
        events.append((ho.time, 1, ho.column))
        events.append(((ho.end_time if ho.end_time else ho.time + 30.0), 0, ho.column))
    keys, frames = 0, [ReplayFrame(time_ms=0.0, keys=0)]
    for t, down, col in sorted(events):
        keys = keys | (1 << col) if down else keys & ~(1 << col)
        frames.append(ReplayFrame(time_ms=float(t), keys=keys))
    return serialize_osr(OSRReplay(
        mode=3, game_version=20240101, beatmap_hash=hashlib.md5(osu_path.read_bytes()).hexdigest(),
        player_name=player, replay_hash="dash123", c300g=len(beatmap.hit_objects), c300=0, c200=0, c100=0, c50=0,
        miss=0, total_score=1000000, max_combo=len(beatmap.hit_objects), perfect=True, mods=0,
        timestamp_ticks=638000000000000000, action_frames=frames,
    ))


def test_profile_replay_action_writes_a_servable_viewer(tmp_path):
    from proj7k.parser import parse_osu_7k

    song = tmp_path / "song"
    song.mkdir()
    osu = song / "sample.osu"
    osu.write_bytes(SAMPLE_OSU.read_bytes())
    audio_name = parse_osu_7k(str(osu)).audio_filename or "audio.mp3"
    (song / audio_name).write_bytes(b"ID3fake-audio")
    osr = song / "play.osr"
    osr.write_bytes(_perfect_replay(osu, "DashPlayer"))

    async def _run():
        s = _Server(tmp_path)
        await s.server.start()
        try:
            async with s.ws() as ws:
                await ws.recv()
                job, _ = await _run_job(ws, "profile_replay", {
                    "source": "files", "replay": str(osr), "beatmap": f'"{osu}"', "recommend": "both", "bundle": True,
                })
                assert job["status"] == "done", job["error"]
                result = job["result"]
                assert result["report"]["player_name"] == "DashPlayer"
                assert result["report"]["hash_matched"] is True
                assert result["report"]["miss_count"] == 0
                assert len(result["report"]["skill_radar"]["dimensions"]) == 8
                assert "aligned_hits" not in result["report"]  # the per-note detail stays out of the frames
                assert result["saved"] is False  # a few seconds of play: the archive's noise filter drops it
                assert "bundle_notice" in result  # a clean play has no failure point to cut around
                assert isinstance(result["recommendations"], list)

                view = next(a for a in job["artifacts"] if a["kind"] == "html")
                audio = next(a for a in job["artifacts"] if a["label"] == "音频")
                status, _, page = await s.get(view["url"])
                assert status == 200 and audio["name"].encode("utf-8") in page
                status, _, body = await s.get(audio["url"])
                assert status == 200 and body == b"ID3fake-audio"

                job, _ = await _run_job(ws, "profile_player", {"player": "DashPlayer", "all_time": True})
                assert job["status"] == "done" and job["result"]["profile"]["window_mode"] == "all_time"
        finally:
            await s.server.stop()

    asyncio.run(_run())


def test_cli_config_follows_lazer_dir(tmp_path):
    args = build_parser().parse_args(["--lazer-dir", str(tmp_path), "--db", str(tmp_path / "p.db"), "--no-open"])
    cfg = build_config(args)
    assert cfg.realm_path == tmp_path / "client.realm"
    assert cfg.lazer_files_dir == tmp_path / "files"
    assert cfg.lazer_lock_path == tmp_path / "client.realm.lock"
    assert cfg.db_path == tmp_path / "p.db"
    args = build_parser().parse_args(["--realm", str(tmp_path / "r" / "client.realm")])
    assert build_config(args).lazer_files_dir == tmp_path / "r" / "files"
