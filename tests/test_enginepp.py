"""Engine pp (ADR-0061): osu!mania's pp formula at the engine's stars, and the signed-in player's top plays."""

import asyncio
import hashlib
import json

import pytest

from proj7k import enginepp
from proj7k.dashboard.topplays import TopPlays, api_judgements, is_7k_mania, summarize

from test_dashboard_player import SAMPLE_OSU, _library, lazer  # noqa: F401  (fixture)

ALL_MAX = {"perfect": 1500, "great": 0, "good": 0, "ok": 0, "meh": 0, "miss": 0}


# --- the formula -----------------------------------------------------------------------------------------------


def test_formula_is_osu_mania_pp():
    # 8 · (★ − 0.15)^2.2 · (5·acc − 4) · (1 + 0.1·min(1, hits/1500))
    assert enginepp.play_pp(5.0, ALL_MAX) == pytest.approx(8 * 4.85 ** 2.2 * 1.0 * 1.1)
    j = {"perfect": 600, "great": 100, "good": 50, "ok": 0, "meh": 0, "miss": 0}
    acc = (600 * 305 + 100 * 300 + 50 * 200) / (750 * 305)
    assert enginepp.pp_accuracy(j) == pytest.approx(acc)
    assert enginepp.play_pp(6.0, j) == pytest.approx(8 * 5.85 ** 2.2 * (5 * acc - 4) * (1 + 0.1 * 750 / 1500))


def test_stable_scores_count_max_at_320_and_mods_scale():
    j = {"perfect": 500, "great": 500}
    assert enginepp.pp_accuracy(j, legacy=True) == pytest.approx((500 * 320 + 500 * 300) / (1000 * 320))
    assert enginepp.play_pp(5, j, ["CL"]) < enginepp.play_pp(5, j)          # stable weighs MAX over 300 harder
    assert enginepp.play_pp(5, j, [{"acronym": "CL"}]) == enginepp.play_pp(5, j, legacy=True)
    assert enginepp.play_pp(5, ALL_MAX, ["NF"]) == pytest.approx(0.75 * enginepp.play_pp(5, ALL_MAX))
    assert enginepp.play_pp(5, ALL_MAX, ["EZ", "NF"]) == pytest.approx(0.375 * enginepp.play_pp(5, ALL_MAX))
    assert enginepp.play_pp(5, ALL_MAX, ["DT1.2x"]) == enginepp.play_pp(5, ALL_MAX)   # the rate is in the stars
    assert enginepp.play_pp(5, {"perfect": 70, "miss": 30}) == 0.0         # under 80% pays nothing
    assert enginepp.play_pp(0, ALL_MAX) == 0.0 and enginepp.play_pp(5, {}) == 0.0


def test_weighting_and_one_play_per_chart():
    assert enginepp.weighted_total([100, 200]) == pytest.approx(200 + 100 * 0.95)
    assert enginepp.weighted_total([1.0] * 150) == pytest.approx(sum(0.95 ** i for i in range(100)))
    best = enginepp.best_per_chart([{"md5": "a", "pp": 3}, {"md5": "a", "pp": 5}, {"md5": "b", "pp": 4}])
    assert [(p["md5"], p["pp"]) for p in best] == [("a", 5), ("b", 4)]


def test_api_scores_read_in_both_formats():
    assert api_judgements({"perfect": 3, "great": 2, "miss": 1, "ignore_hit": 9})["perfect"] == 3
    legacy = api_judgements({"count_geki": 5, "count_300": 4, "count_katu": 3, "count_100": 2, "count_50": 1, "count_miss": 0})
    assert legacy == {"perfect": 5, "great": 4, "good": 3, "ok": 2, "meh": 1, "miss": 0}
    assert is_7k_mania({"beatmap": {"mode": "mania", "cs": 7, "convert": False}})
    assert not is_7k_mania({"beatmap": {"mode": "mania", "cs": 4}})
    assert not is_7k_mania({"beatmap": {"mode": "mania", "cs": 7, "convert": True}})


# --- the player's top plays ------------------------------------------------------------------------------------


class FakeAccount:
    def __init__(self, scores, signed_in=True, files=None):
        self.scores, self.signed_in, self.files = scores, signed_in, files or {}
        self.downloads = []

    def status(self):
        if not self.signed_in:
            return {"signed_in": False, "login_url": "https://7k.example/oauth/start"}
        return {"signed_in": True, "user": self.user(), "profile": {"variants": {"7k": {"pp": 4321.0}}}}

    def user(self):
        return {"id": 77, "username": "inawah"} if self.signed_in else {}

    def best_scores(self):
        if isinstance(self.scores, Exception):
            raise self.scores
        return self.scores

    def beatmap_file(self, beatmap_id):
        self.downloads.append(beatmap_id)
        return self.files[beatmap_id]


def _api_score(sid, md5, beatmap_id, stats, mods=(), cs=7, mode="mania", status="ranked", legacy=None, pp=100.0):
    return {
        "id": sid, "accuracy": 0.97, "rank": "S", "pp": pp, "ended_at": "2026-10-03T10:00:00Z", "legacy_score_id": legacy,
        "mods": [{"acronym": m} for m in mods], "statistics": stats,
        "beatmap": {"id": beatmap_id, "checksum": md5, "cs": cs, "mode": mode, "convert": False, "version": "Online",
                    "difficulty_rating": 4.5, "status": status, "beatmapset_id": 9},
        "beatmapset": {"id": 9, "title": "Online Song", "artist": "A", "creator": "m"},
    }


STATS = {"perfect": 700, "great": 80, "good": 10, "ok": 4, "meh": 2, "miss": 4}


def test_top_plays_merge_osu_and_this_machine(lazer):  # noqa: F811
    library = _library(lazer)
    other = SAMPLE_OSU.read_bytes() + b"\n// downloaded copy"
    lazer["data"]["scores"][0]["online_id"] = 501   # s1 was submitted: osu! lists it too
    account = FakeAccount([
        _api_score(501, "md5a", 456, STATS),                                   # = local play s1
        _api_score(502, hashlib.md5(other).hexdigest(), 999, STATS, pp=80.0),  # not in the library: downloaded
        _api_score(503, "x4k", 4, STATS, cs=4),                                # 4K: not counted
        _api_score(504, "xrd", 5, STATS, mods=("RD",)),                        # Random: skipped
    ], files={999: other})
    tp = TopPlays(account, library, cache_dir=lazer["tmp"] / "dash", background=False)
    out = tp.status()
    assert out["signed_in"] and not out["running"] and out["official_7k_pp"] == 4321.0
    n = out["notes"]
    assert n["online_scores"] == 4 and n["online_7k"] == 3 and n["skipped_mods"] == 1 and n["online_unread"] == 0
    assert account.downloads == [999] and (lazer["tmp"] / "dash" / "osu_files" / "999.osu").read_bytes() == other

    # One play per chart: on md5a the DT play (s2, faster chart, same judgements) beats s1, which osu! also listed.
    assert len(out["plays"]) == 2
    md5a = next(p for p in out["plays"] if p["md5"] == "md5a")
    assert md5a["play_id"] == "s2" and md5a["source"] == "local"
    assert any(p["source"] == "both" and p["play_id"] == "s1" for p in tp._result["plays"])
    assert [p["pp"] for p in out["plays"]] == sorted((p["pp"] for p in out["plays"]), reverse=True)
    assert out["total_pp"] == pytest.approx(out["plays"][0]["pp"] + 0.95 * out["plays"][1]["pp"], abs=0.02)
    assert out["plays"][0]["weight"] == 1.0 and out["plays"][1]["url"].startswith("https://osu.ppy.sh/b/")

    # The DT play is scored at the stars of the faster chart.
    dt = next(p for p in tp._result["plays"] if p.get("play_id") == "s2")
    plain = next(p for p in tp._result["plays"] if p["md5"] == "md5a" and p.get("play_id") != "s2")
    assert dt["stars"] > plain["stars"] and dt["mods"] and dt["source"] == "local"

    # Cached: asking again doesn't recompute; downloaded charts aren't fetched twice on a refresh.
    assert tp.status()["computed_at"] == out["computed_at"]
    tp.status(refresh=True)
    assert account.downloads == [999]


def test_unranked_charts_count_only_when_asked(lazer):  # noqa: F811
    lazer["data"]["beatmaps"][0]["status"] = 4   # loved
    tp = TopPlays(FakeAccount([]), _library(lazer), background=False)
    ranked = tp.status()
    assert ranked["plays"] == [] and ranked["total_pp"] == 0 and ranked["excluded_unranked"] == 2
    every = summarize(tp._result, "all")
    assert len(every["plays"]) == 1 and every["total_pp"] > 0


def test_offline_falls_back_on_this_machine_and_signed_out_asks_to_sign_in(lazer):  # noqa: F811
    tp = TopPlays(FakeAccount(OSError("offline")), _library(lazer), background=False)
    out = tp.status()
    assert out["notes"]["online_error"] == "offline" and out["sources"]["local"] == 1
    signed_out = TopPlays(FakeAccount([], signed_in=False), _library(lazer), background=False).status()
    assert signed_out == {"signed_in": False, "login_url": "https://7k.example/oauth/start"}


def test_engine_pp_route_and_page(lazer):  # noqa: F811
    from test_dashboard import _Server

    async def _run():
        s = _Server(lazer["tmp"])
        s.server.library = _library(lazer)
        s.server.top_plays = TopPlays(FakeAccount([]), s.server.library, background=False)
        await s.server.start()
        try:
            status, headers, _ = await s.get("/pp")
            assert status == 200 and headers["Content-Type"].startswith("text/html")
            out = json.loads((await s.get("/api/me/enginepp?scope=all"))[2])
            assert out["signed_in"] and out["scope"] == "all" and out["plays"][0]["play_id"] in ("s1", "s2")
            assert json.loads((await s.get("/api/me/enginepp/refresh"))[2])["scope"] == "ranked"
        finally:
            await s.server.stop()

    asyncio.run(_run())
