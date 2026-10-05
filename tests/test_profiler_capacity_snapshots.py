"""Snapshots and aggregates on the engine's unit (ADR-0020 decisions 3 and 4)."""

import hashlib
from pathlib import Path

import pytest

from proj7k.engine.scale import stars_of
from proj7k.profiler.aggregate import aggregate_macro_profile
from proj7k.profiler.cli import run_ingestion
from proj7k.profiler.osr import OSRReplay, ReplayFrame, serialize_osr
from proj7k.profiler.storage import SNAPSHOT_ENGINE, MatchSnapshot, ProfilerStorage, build_snapshot_from_report
from proj7k.parser import parse_osu_7k

NOW = 1_700_000_000.0


def _snapshot(engine, stars, failed=False, fatal=None, replay_hash="h"):
    return MatchSnapshot(
        player_name="P", timestamp=NOW, beatmap_hash="b", replay_hash=replay_hash, play_duration_s=60.0,
        completion_rate=1.0, is_valid_play=True, is_failed=failed, overall_ur=100.0,
        capacities={"jack": {"effective_capacity": 9.0, "star_rating": stars, "dan_tier": "5th", "tested": True}},
        fatal_time_ms=1000.0 if fatal else None, fatal_peak_strains=fatal or {},
        summary={"engine": engine} if engine else {},
    )


def test_legacy_snapshots_are_counted_and_left_out(tmp_path: Path):
    storage = ProfilerStorage(db_path=tmp_path / "p.db")
    storage.save_snapshot(_snapshot(None, 9.9, replay_hash="old"))        # written by the legacy engine
    storage.save_snapshot(_snapshot(SNAPSHOT_ENGINE, 5.5, replay_hash="new"))

    profile = aggregate_macro_profile(storage, "P", horizon_days=None)

    assert profile.legacy_excluded == 1
    assert profile.total_matches == 1
    assert profile.dimensions["jack"].star_rating == pytest.approx(5.5)   # the legacy 9.9 did not leak in
    assert profile.to_dict()["legacy_excluded"] == 1


def test_a_failed_run_contributes_the_stars_of_the_demand_it_broke_under(tmp_path: Path):
    storage = ProfilerStorage(db_path=tmp_path / "p.db")
    storage.save_snapshot(_snapshot(SNAPSHOT_ENGINE, 4.0, failed=True, fatal={"jack": 14.0}))

    profile = aggregate_macro_profile(storage, "P", horizon_days=None)

    assert profile.dimensions["jack"].star_rating == pytest.approx(max(4.0, stars_of(14.0)))


def test_a_snapshot_of_a_new_play_is_stamped_and_carries_the_fatal_demand(tmp_path: Path):
    xs = [36, 109, 182, 256, 329, 402, 475]
    lines = ["osu file format v14", "", "[General]", "Mode: 3", "", "[Difficulty]", "CircleSize: 7",
             "OverallDifficulty: 8", "", "[TimingPoints]", "0,400,4,2,0,100,1,0", "", "[HitObjects]"]
    lines += [f"{xs[0]},192,{1000 + i * 140},1,0,0:0:0:0:" for i in range(240)]
    osu = tmp_path / "jack.osu"
    osu.write_text("\n".join(lines) + "\n", encoding="utf-8")
    beatmap = parse_osu_7k(str(osu))

    frames = [ReplayFrame(0.0, 0)]
    for i, ho in enumerate(beatmap.hit_objects):
        if i < 120:                       # clean, then the player stops: the replay ends mid-song
            frames += [ReplayFrame(ho.time + 2.0, 1), ReplayFrame(ho.time + 40.0, 0)]
    replay = OSRReplay(
        mode=3, game_version=20240101, beatmap_hash=hashlib.md5(osu.read_bytes()).hexdigest(), player_name="P",
        replay_hash="r1", c300g=120, miss=120, life_bar="0|1.0,17000|0.0", timestamp_ticks=638000000000000000,
        action_frames=frames,
    )
    osr = tmp_path / "r.osr"
    osr.write_bytes(serialize_osr(replay))

    report = run_ingestion(osr, osu)
    snap = build_snapshot_from_report(report, is_failed=True, beatmap=beatmap)

    assert snap.summary["engine"] == SNAPSHOT_ENGINE
    assert snap.capacities["jack"]["tested"] is True
    assert snap.capacities["jack"]["chart_level"] > 0
    assert snap.fatal_time_ms is not None
    assert snap.fatal_peak_strains["jack"] > 0
