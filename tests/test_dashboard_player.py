"""
Tests for the dashboard's player pages (ADR-0034): the library snapshot (charts in engine stars, plays under rate
mods, profiler snapshots joined), the home / history / library / chart / play APIs, the files served out of
osu!lazer's store, picking one replay to diagnose, and the desktop app's start-up.
"""

import asyncio
import hashlib
import io
import json
import time
from pathlib import Path

from PIL import Image
import pytest

from proj7k.dashboard import player as player_api
from proj7k.dashboard.player import ChartStars, PlayerLibrary, chart_from_record
from proj7k.engine.evaluate import evaluate_osu
from proj7k.lazer.annotator import current_engine_version
from proj7k.profiler.storage import MatchSnapshot, ProfilerStorage

from test_dashboard import _Server

REPO = Path(__file__).resolve().parents[1]
SAMPLE_OSU = REPO / "docs" / "sample_7k.osu"


def _store(files_dir: Path, data: bytes) -> str:
    h = hashlib.sha256(data).hexdigest()
    target = player_api.store_path(files_dir, h)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return h


def _beatmap(md5, file_hash, name, stars, **extra):
    return {
        "md5_hash": md5, "file_hash": file_hash, "online_id": 456, "set_online_id": 123, "star_rating": stars,
        "difficulty_name": name, "title": "Song", "title_unicode": "歌", "artist": "Artist", "artist_unicode": "Artist",
        "creator": "mapper", "tags": "", "length_ms": 120000, "bpm": 180, "od": 8, "hp": 8, "status": 1,
        "date_added": "2026-01-01T00:00:00.000Z", "last_played": "", "preview_time": 1000, **extra,
    }


def _score(sid, md5, date, rank, acc, total, mods=None, replay="", stats=None):
    return {
        "id": sid, "online_id": -1, "player_name": "inawah", "user_id": 77, "country": "CN", "date": date,
        "total_score": total, "accuracy": acc, "rank": rank, "max_combo": 500, "pp": None, "mods": mods or [],
        "statistics": stats or {"perfect": 700, "great": 80, "good": 10, "ok": 4, "meh": 2, "miss": 4},
        "maximum_statistics": {}, "replay_file_hash": replay, "beatmap_md5": md5,
    }


@pytest.fixture
def lazer(tmp_path):
    """A fake osu!lazer library: one synced chart (a real .osu, cover, audio), one never synced, three plays."""
    files = tmp_path / "lazer" / "files"
    osu = SAMPLE_OSU.read_bytes()
    osu_hash = _store(files, osu)
    buf = io.BytesIO()
    Image.new("RGB", (1600, 900), (40, 80, 160)).save(buf, "PNG")
    cover_hash = _store(files, buf.getvalue())
    audio_hash = _store(files, b"ID3" + b"\0" * 2048)
    replay_hash = _store(files, b"fake replay bytes")
    other_osu_hash = _store(files, osu + b"\n// another version")
    stars = round(evaluate_osu(osu.decode("utf-8")).total_stars, 2)  # what a sync would have written
    synced = _beatmap("md5a", osu_hash, f"Hard ({stars:.2f}★ 1st Jack {current_engine_version()})", stars,
                      background_hash=cover_hash, audio_hash=audio_hash)
    unsynced = _beatmap("md5b", other_osu_hash, "Insane", 4.2)
    data = {
        "beatmaps": [synced, unsynced],
        "scores": [
            _score("s1", "md5a", "2026-10-01T10:00:00.000Z", 3, 0.9712, 950000, replay=replay_hash),
            _score("s2", "md5a", "2026-10-02T10:00:00.000Z", 4, 0.9850, 980000,
                   mods=[{"acronym": "DT", "settings": {"speed_change": 1.2}}]),
            _score("s3", "md5b", "2026-09-01T10:00:00.000Z", -1, 0.80, 400000,
                   stats={"Perfect": 300, "Miss": 40}),
        ],
    }
    return {"files": files, "data": data, "replay_hash": replay_hash, "stars": stars, "tmp": tmp_path}


def _library(lazer, **kwargs):
    return PlayerLibrary(
        lazer["tmp"] / "lazer" / "client.realm", lazer["files"], db_path=lazer["tmp"] / "profiler.db",
        cache_dir=lazer["tmp"] / "dash", loader=lambda: lazer["data"], evaluate_in_background=False, **kwargs,
    )


def test_a_synced_chart_reads_in_engine_stars_and_its_plain_name(lazer, tmp_path):
    stars = ChartStars(None)
    chart = chart_from_record(lazer["data"]["beatmaps"][0], stars, lazer["files"])
    assert chart["stars"] == lazer["stars"] and chart["stars_source"] == "engine" and chart["dominant"] == "jack"
    assert chart["version"] == "Hard" and chart["dan"] and chart["has_cover"]
    raw = chart_from_record(lazer["data"]["beatmaps"][1], stars, lazer["files"])
    assert raw["stars_source"] == "lazer" and raw["stars"] == 4.2


def test_snapshot_plays_ranks_judgements_and_rate_stars(lazer):
    snap = _library(lazer).snapshot()
    assert [p["id"] for p in snap.plays] == ["s2", "s1", "s3"]  # newest first
    s1, s2, s3 = snap.play("s1"), snap.play("s2"), snap.play("s3")
    assert s1["rank_label"] == "A" and s1["passed"] and s1["judgements"]["perfect"] == 700 and s1["notes"] == 800
    assert s3["rank"] == "F" and not s3["passed"] and s3["judgements"] == {
        "perfect": 300, "great": 0, "good": 0, "ok": 0, "meh": 0, "miss": 40}
    # DT at 1.2x: the chart as played, harder than the chart file.
    assert s2["clock_rate"] == 1.2 and s2["mods"] == ["DT1.2x"] and s2["stars"] > s1["stars"]
    assert snap.players() == [{"name": "inawah", "user_id": 77, "country": "CN", "plays": 3, "last_played": s2["date"]}]
    assert snap.default_player() == "inawah"


def test_unsynced_charts_are_read_by_the_engine_once_and_cached(lazer):
    lib = _library(lazer)
    snap = lib.snapshot()
    chart = snap.charts["md5b"]
    entry = lib.stars.evaluate(chart["file_hash"], player_api.store_path(lazer["files"], chart["file_hash"]))
    assert entry["stars"] > 0 and entry["dominant"]
    lib.stars.save()
    again = _library(lazer).snapshot().charts["md5b"]
    assert again["stars_source"] == "engine" and again["stars"] == round(entry["stars"], 2)


def test_profiler_snapshots_join_their_plays(lazer):
    with ProfilerStorage(db_path=lazer["tmp"] / "profiler.db") as storage:
        storage.save_snapshot(MatchSnapshot(
            player_name="inawah", timestamp=0.0, beatmap_hash="md5a", replay_hash=lazer["replay_hash"],
            play_duration_s=120, completion_rate=1.0, is_valid_play=True, is_failed=False, overall_ur=123.4,
            capacities={"jack": {"star_rating": 6.1, "tested": True, "broke_down": False}},
            summary={"overall": {"star_rating": 6.0}},
        ))
    snap = _library(lazer).snapshot()
    prof = snap.play("s1")["profiler"]
    assert prof["ur"] == 123.4 and prof["level_stars"] == 6.0 and prof["skills"] == {"jack": 6.1}
    assert snap.play("s2")["profiler"] is None


def test_overview_history_library_and_play_detail(lazer):
    snap = _library(lazer).snapshot()
    o = player_api.overview(snap, "inawah", now=player_api._parse_date("2026-10-05T00:00:00Z"))
    assert o["player"]["avatar"] == "/api/me/avatar/77" and o["player"]["profile_url"].endswith("/users/77/mania")
    assert o["totals"]["plays"] == 3 and o["totals"]["passed"] == 2 and o["totals"]["charts"] == 2
    assert o["totals"]["ranks"]["S"] == 1  # each chart's best: S on md5a, F on md5b
    assert o["recent"]["plays"] == 2 and o["previous"]["plays"] == 1
    assert [p["id"] for p in o["top_plays"]] == ["s2"]  # one per chart, by played stars
    assert len(o["progression"]) == 3 and o["progression"][0]["id"] == "s3"

    lib = player_api.library(snap, "inawah")
    rows = {r["md5"]: r for r in lib["charts"]}
    assert rows["md5a"]["plays"] == 2 and rows["md5a"]["best_rank"] == "S" and rows["md5a"]["best_play"] == "s2"
    assert lib["pending_stars"] == 1

    d = player_api.play_detail(snap, "s1")
    assert d["attempt_rank"] == 2 and not d["personal_best"] and len(d["attempts"]) == 2
    assert d["chart"]["links"]["catalog"] == "https://7k.kaiz.dev/beatmapsets/123#456"
    assert player_api.play_detail(snap, "nope") is None


def test_overview_level_and_next_steps(lazer):
    snap = _library(lazer).snapshot()
    now = player_api._parse_date("2026-10-05T00:00:00Z")
    # One clear is too few to estimate a level from; without replays the home page shows none.
    o = player_api.overview(snap, "inawah", now=now)
    assert o["level"] is None and o["next"] == {"almost": [], "next_dan": None}

    profile = {"all_time": {"overall_stars": 4.3, "overall_dan": "x", "skills": {}}}
    o = player_api.overview(snap, "inawah", now=now, profile=profile)
    assert o["level"]["source"] == "profile" and o["level"]["dan"] == player_api.estimate_canonical_dan(4.3)
    # md5b (4.2★) was failed within 90 days, near the level: worth another go. md5a was cleared at S.
    assert [p["md5"] for p in o["next"]["almost"]] == ["md5b"]
    tiers = list(player_api.CANONICAL_DAN_TIERS)
    assert o["next"]["next_dan"]["dan"] == tiers[tiers.index(o["level"]["dan"]) + 1]


def test_player_api_over_http(lazer):
    async def _run():
        s = _Server(lazer["tmp"])
        s.server.library = _library(lazer)
        await s.server.start()
        try:
            for path in ("/home", "/history", "/library", "/chart?md5=md5a", "/play?id=s1"):
                status, headers, _ = await s.get(path)
                assert status == 200 and headers["Content-Type"].startswith("text/html"), path

            players = json.loads((await s.get("/api/me/players"))[2])
            assert players["default"] == "inawah" and players["plays"] == 3
            overview = json.loads((await s.get("/api/me/overview"))[2])
            assert overview["totals"]["plays"] == 3 and overview["profile"] is None
            assert [p["id"] for p in json.loads((await s.get("/api/me/history?player=INAWAH"))[2])["plays"]] == ["s2", "s1", "s3"]
            assert len(json.loads((await s.get("/api/me/library"))[2])["charts"]) == 2

            chart = json.loads((await s.get("/api/me/chart/md5a"))[2])
            assert chart["engine"]["stars"] > 0 and len(chart["engine"]["skills"]) == 8
            assert chart["engine"]["timeline"]["load"] and len(chart["plays"]) == 2
            assert (await s.get("/api/me/chart/missing"))[0] == 404

            play = json.loads((await s.get("/api/me/play/s1"))[2])
            assert play["play"]["replay_hash"] == lazer["replay_hash"]

            status, headers, body = await s.get("/api/me/cover/md5a?w=320")
            assert status == 200 and headers["Content-Type"] == "image/jpeg"
            assert Image.open(io.BytesIO(body)).width == 320
            status, headers, body = await s.get("/api/me/audio/md5a", {"Range": "bytes=0-9"})
            assert status == 206 and len(body) == 10
            assert (await s.get("/api/me/cover/md5b"))[0] == 404

            status, headers, body = await s.get("/api/me/replay/s1")
            assert status == 200 and body == b"fake replay bytes" and ".osr" in headers["Content-Disposition"]
            assert (await s.get("/api/me/replay/s2"))[0] == 404  # no replay saved for that play
            assert (await s.get("/api/me/avatar/abc"))[0] == 404

            # "Watch in osu!": the replay leaves the store as an .osr and goes to the system's handler.
            async with s.ws() as ws:
                await ws.send(json.dumps({"type": "open_replay", "play": "s1"}))
                for _ in range(50):
                    if s.opened:
                        break
                    await asyncio.sleep(0.05)
            assert s.opened and s.opened[0].suffix == ".osr" and s.opened[0].read_bytes() == b"fake replay bytes"
        finally:
            await s.server.stop()

    asyncio.run(_run())


def test_a_slow_first_read_of_the_library_still_answers(lazer):
    """The player API answers inside websockets' handshake, so its open timeout must cover the library's first read."""
    from proj7k.dashboard.server import PLAYER_API_TIMEOUT_S

    def slow():
        time.sleep(1.0)
        return lazer["data"]

    async def _run(open_timeout):
        s = _Server(lazer["tmp"])
        assert s.server.open_timeout == PLAYER_API_TIMEOUT_S >= 60
        s.server.open_timeout = open_timeout
        s.server.library = PlayerLibrary(
            lazer["tmp"] / "lazer" / "client.realm", lazer["files"], loader=slow, evaluate_in_background=False)
        await s.server.start()
        try:
            return await s.get("/api/me/players")
        except OSError as e:  # the connection dropped with no response
            return e
        finally:
            await s.server.stop()

    status, _, body = asyncio.run(_run(5.0))
    assert status == 200 and json.loads(body)["plays"] == 3
    # What websockets' 10 s default did to a cold read of a large library.
    assert isinstance(asyncio.run(_run(0.3)), OSError)


def test_the_avatar_is_served_as_the_image_it_is(lazer):
    async def _run():
        s = _Server(lazer["tmp"])
        s.server.library = _library(lazer)
        cached = s.config.output_dir / "avatars" / "77.img"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"\xff\xd8\xff\xe0" + b"\0" * 64)
        await s.server.start()
        try:
            return await s.get("/api/me/avatar/77")
        finally:
            await s.server.stop()

    status, headers, _ = asyncio.run(_run())
    assert status == 200 and headers["Content-Type"] == "image/jpeg"


def test_an_unreadable_library_reports_instead_of_failing(tmp_path):
    def broken():
        raise RuntimeError("bridge exploded")

    lib = PlayerLibrary(tmp_path / "client.realm", tmp_path / "files", loader=broken, evaluate_in_background=False)
    snap = lib.snapshot()
    assert "bridge exploded" in snap.error and snap.plays == [] and snap.default_player() is None
    assert player_api.overview(snap, "")["totals"]["plays"] == 0


def test_a_picked_replay_is_resolved_by_its_hash(tmp_path):
    from proj7k.profiler.cli import resolve_lazer_replay

    files = tmp_path / "files"
    b, r1, r2 = _store(files, b"chart"), _store(files, b"replay one"), _store(files, b"replay two")
    realm = tmp_path / "client.realm"
    realm.write_bytes(b"")

    class Client:
        def dump_7k_scores(self, realm_path=None, user=None):
            return [
                {"player_name": "someone", "beatmap_file_hash": b, "replay_file_hash": r1, "date": "2026-10-02", "title": "A", "difficulty_name": "X"},
                {"player_name": "inawah", "beatmap_file_hash": b, "replay_file_hash": r2, "date": "2026-10-01", "title": "A", "difficulty_name": "X"},
                {"player_name": "inawah", "beatmap_file_hash": b, "replay_file_hash": r1 + "0", "date": "2026-10-03", "title": "A", "difficulty_name": "X"},
            ]

    found = resolve_lazer_replay(None, realm_path=realm, files_dir=files, client=Client(), replay_file_hash=r2)
    assert found["replay"].name == r2 and found["player_name"] == "inawah"
    with pytest.raises(LookupError):
        resolve_lazer_replay(None, realm_path=realm, files_dir=files, client=Client(), replay_file_hash="nope")


# --- the desktop app -------------------------------------------------------------------------------------------


def test_desktop_reuses_a_running_dashboard_and_opens_its_window(lazer, monkeypatch):
    from proj7k.desktop import app

    async def _run():
        s = _Server(lazer["tmp"])
        await s.server.start()
        try:
            assert await asyncio.to_thread(app.dashboard_running, s.port)
            started, opened = [], []
            monkeypatch.setattr(app, "start_dashboard", started.append)
            monkeypatch.setattr(app, "LOG_DIR", lazer["tmp"] / "logs")
            monkeypatch.setattr(app.webbrowser, "open", opened.append)
            code = await asyncio.to_thread(app.main, ["--port", str(s.port), "--window", "browser", "--page", "library"])
            assert code == 0 and started == [] and opened == [f"http://127.0.0.1:{s.port}/#library"]
        finally:
            await s.server.stop()

    asyncio.run(_run())


def test_desktop_starts_a_dashboard_when_none_runs(tmp_path, monkeypatch):
    from proj7k.desktop import app
    from test_dashboard import _free_port

    port = _free_port()
    assert not app.dashboard_running(port)
    started = []
    monkeypatch.setattr(app, "start_dashboard", started.append)
    monkeypatch.setattr(app, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(app, "_alert", lambda message: None)
    assert app.main(["--port", str(port), "--window", "browser", "--timeout", "0.6"]) == 1
    assert started == [port]


def test_app_window_command_and_icon(tmp_path):
    from proj7k.desktop import app
    from proj7k.desktop.icon import main as icon_main

    cmd = app.app_window_command(Path("msedge.exe"), "http://127.0.0.1:7770/#home")
    assert cmd[1] == "--app=http://127.0.0.1:7770/#home" and any(c.startswith("--user-data-dir=") for c in cmd)
    out = tmp_path / "proj7k.ico"
    assert icon_main([str(out)]) == 0
    assert Image.open(out).format == "ICO"
