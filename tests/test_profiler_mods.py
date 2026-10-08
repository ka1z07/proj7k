"""The profiler honours a replay's mods: lazer's own mod list, the legacy bitmask, Mirror and rate mods."""

import hashlib
from pathlib import Path

import pytest

from proj7k.profiler.aggregate import aggregate_macro_profile
from proj7k.profiler.cli import run_ingestion
from proj7k.profiler.matcher import HitJudgment
from proj7k.profiler.mods import resolve_play_mods
from proj7k.profiler.osr import OSRReplay, ReplayFrame, parse_osr, serialize_osr
from proj7k.profiler.storage import (
    SNAPSHOT_ENGINE,
    MatchSnapshot,
    ProfilerStorage,
    snapshot_is_current,
)


def test_lazer_mods_carry_the_actual_rate():
    pm = resolve_play_mods(64, [{"acronym": "DT", "settings": {"speed_change": 1.1}}])
    assert pm.clock_rate == pytest.approx(1.1)
    assert pm.acronyms == ["DT1.1x"]
    assert pm.supported

    assert resolve_play_mods(256, [{"acronym": "HT"}]).clock_rate == pytest.approx(0.75)
    assert resolve_play_mods(0, [{"acronym": "DC", "settings": {"speed_change": 0.85}}]).clock_rate == pytest.approx(0.85)


def test_lazer_mods_windows_mirror_and_unsupported():
    pm = resolve_play_mods(0, [{"acronym": "HR"}, {"acronym": "MR"}])
    assert pm.window_multiplier == pytest.approx(1 / 1.4)
    assert pm.mirror and pm.supported
    assert resolve_play_mods(0, [{"acronym": "EZ"}]).window_multiplier == pytest.approx(1.4)
    assert resolve_play_mods(0, [{"acronym": "DA", "settings": {"overall_difficulty": 5}}]).od_override == 5.0
    for acronym in ("RD", "HO", "IN", "AT", "WU", "4K"):
        assert resolve_play_mods(0, [{"acronym": acronym}]).unsupported == [acronym]
    assert resolve_play_mods(0, [{"acronym": "HD"}, {"acronym": "NF"}]).supported


def test_legacy_bitmask_fallback():
    assert resolve_play_mods(64).clock_rate == 1.5
    assert resolve_play_mods(64 | 512).clock_rate == 1.5
    assert resolve_play_mods(64 | 512).acronyms == ["NC"]
    assert resolve_play_mods(256).clock_rate == 0.75
    assert resolve_play_mods(1 << 30).mirror
    assert resolve_play_mods(16).window_multiplier == pytest.approx(1 / 1.4)
    assert resolve_play_mods(1 << 21).unsupported == ["RD"]
    assert resolve_play_mods(1 << 15).unsupported == ["4K"]
    assert resolve_play_mods(1 << 18).supported  # 7K on a 7K chart changes nothing


def _replay(md5: str, frames, mods=0, lazer_mods=None) -> OSRReplay:
    return OSRReplay(
        beatmap_hash=md5, player_name="P", replay_hash="r", mods=mods, timestamp_ticks=638000000000000000,
        action_frames=frames, lazer_mods=lazer_mods,
    )


def test_osr_round_trips_lazer_score_info():
    replay = _replay("x", [ReplayFrame(0.0, 0)], mods=64, lazer_mods=[{"acronym": "DT", "settings": {"speed_change": 1.2}}])
    parsed = parse_osr(serialize_osr(replay))
    assert parsed.lazer_mods == [{"acronym": "DT", "settings": {"speed_change": 1.2}}]
    assert parse_osr(serialize_osr(_replay("x", [ReplayFrame(0.0, 0)]))).lazer_mods is None


@pytest.fixture
def chart(tmp_path: Path):
    """Four rice notes on column 0 (x=36), one every 500ms, OD 8 (W_300 = 40ms)."""
    content = "osu file format v14\n\n[General]\nMode: 3\n\n[Difficulty]\nCircleSize: 7\nOverallDifficulty: 8\n\n[HitObjects]\n"
    content += "".join(f"36,192,{1000 + 500 * i},1,0,0:0:0:0:\n" for i in range(4))
    path = tmp_path / "chart.osu"
    path.write_bytes(content.encode("utf-8"))
    return path, hashlib.md5(content.encode("utf-8")).hexdigest()


def _taps(column: int, offset_ms: float):
    frames = [ReplayFrame(0.0, 0)]
    for i in range(4):
        t = 1000 + 500 * i + offset_ms
        frames += [ReplayFrame(t, 1 << column), ReplayFrame(t + 40, 0)]
    return frames


def test_mirror_play_is_aligned_against_the_flipped_chart(tmp_path: Path, chart):
    path, md5 = chart
    osr = tmp_path / "mr.osr"
    # Under Mirror the notes of column 0 are played on column 6.
    osr.write_bytes(serialize_osr(_replay(md5, _taps(6, 0.0), mods=1 << 30, lazer_mods=[{"acronym": "MR"}])))
    report = run_ingestion(osr, path)
    assert report.miss_count == 0
    assert report.ghost_tap_count == 0
    assert report.judgment_counts[HitJudgment.MAX] == 4


def test_custom_rate_widens_windows_and_sets_the_clock(tmp_path: Path, chart):
    path, md5 = chart
    osr = tmp_path / "dt.osr"
    # +50ms of song time at 1.35x is 37ms of real time: PERFECT (W_300 = 40ms real = 54ms song).
    lazer = [{"acronym": "DT", "settings": {"speed_change": 1.35}}]
    osr.write_bytes(serialize_osr(_replay(md5, _taps(0, 50.0), mods=64, lazer_mods=lazer)))
    report = run_ingestion(osr, path)
    assert report.clock_rate == pytest.approx(1.35)
    assert report.judgment_counts[HitJudgment.PERFECT] == 4


def test_unsupported_mod_play_is_not_stored(tmp_path: Path, chart):
    path, md5 = chart
    osr = tmp_path / "rd.osr"
    osr.write_bytes(serialize_osr(_replay(md5, _taps(0, 0.0), mods=1 << 21, lazer_mods=[{"acronym": "RD"}])))
    report = run_ingestion(osr, path)
    assert report.play_mods.unsupported == ["RD"]
    report.play_duration_s, report.completion_rate = 120.0, 1.0  # past the noise filter
    with ProfilerStorage(db_path=tmp_path / "p.db") as storage:
        assert storage.save_report_with_filter(report) is None


def _snapshot(replay_hash: str, summary) -> MatchSnapshot:
    return MatchSnapshot(
        player_name="P", timestamp=1_700_000_000.0, beatmap_hash="b", replay_hash=replay_hash,
        play_duration_s=120.0, completion_rate=1.0, is_valid_play=True, is_failed=False, overall_ur=100.0,
        capacities={"stream": {"effective_capacity": 5.0, "star_rating": 6.0, "dan_tier": "x", "tested": True,
                               "broke_down": False, "chart_level": 6.0}},
        fatal_time_ms=None, fatal_column=None, fatal_peak_strains={}, summary=summary, created_at=0.0,
    )


def test_mod_plays_read_before_mods_were_honoured_are_stale(tmp_path: Path):
    assert snapshot_is_current({"mods": 0})
    assert snapshot_is_current({"mods": 8})  # HD changes nothing the profiler reads
    assert not snapshot_is_current({"mods": 64})
    assert not snapshot_is_current({"mods": 1 << 30})
    assert snapshot_is_current({"mods": 64, "ingest": 2})

    with ProfilerStorage(db_path=tmp_path / "p.db") as storage:
        storage.save_snapshot(_snapshot("nm", {"mods": 0, "engine": SNAPSHOT_ENGINE}))
        storage.save_snapshot(_snapshot("dt", {"mods": 64, "engine": SNAPSHOT_ENGINE}))
        assert storage.is_current_replay("nm")
        assert storage.has_replay("dt") and not storage.is_current_replay("dt")
        profile = aggregate_macro_profile(storage, "P", horizon_days=None)
        assert profile.total_matches == 1
        assert profile.stale_excluded == 1
        assert storage.delete_replay("dt") == 1
        assert not storage.has_replay("dt")


def test_import_rereads_stale_mod_plays(tmp_path: Path, chart, monkeypatch):
    path, md5 = chart
    files = tmp_path / "files"
    realm = tmp_path / "client.realm"
    realm.write_bytes(b"")

    def store(name: str, data: bytes) -> str:
        h = hashlib.sha256(data).hexdigest()
        (files / h[0] / h[:2]).mkdir(parents=True, exist_ok=True)
        (files / h[0] / h[:2] / h).write_bytes(data)
        return h

    frames = _taps(0, 0.0) + [ReplayFrame(40_000.0, 0)]  # long enough to pass the noise filter
    b_hash = store("chart", path.read_bytes())
    r_hash = store("replay", serialize_osr(_replay(md5, frames, mods=64, lazer_mods=[{"acronym": "DT", "settings": {"speed_change": 1.1}}])))

    class FakeBridge:
        def __init__(self, **_):
            pass

        def dump_7k_scores(self, realm_path=None, user=None):
            return [{"player_name": "P", "beatmap_file_hash": b_hash, "replay_file_hash": r_hash}]

    monkeypatch.setattr("proj7k.lazer.bridge.RealmBridgeClient", FakeBridge)
    from proj7k.profiler.cli import run_replay_import

    db = tmp_path / "p.db"
    with ProfilerStorage(db_path=db) as storage:
        storage.save_snapshot(_snapshot(r_hash, {"mods": 64, "engine": SNAPSHOT_ENGINE}))

    stats = run_replay_import("P", realm_path=realm, files_dir=files, db_path=db)
    assert stats["reingested"] == 1 and stats["already_exists"] == 0
    with ProfilerStorage(db_path=db) as storage:
        assert storage.is_current_replay(r_hash)
        [snap] = storage.get_snapshots("P")
        assert snap.summary["clock_rate"] == pytest.approx(1.1)

    assert run_replay_import("P", realm_path=realm, files_dir=files, db_path=db)["already_exists"] == 1
