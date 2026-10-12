"""The profile reads a player's level across plays, not the best single play (ADR-0028)."""

import math
from pathlib import Path

import pytest

from proj7k.dan import estimate_canonical_dan
from proj7k.engine.params import DEFAULT
from proj7k.engine.scale import stars_of
from proj7k.field import trace_beatmap
from proj7k.parser import parse_osu_7k
from proj7k.profiler.aggregate import MIN_FIT_PLAYS, aggregate_macro_profile, level_across_plays
from proj7k.profiler.matcher import AlignedHit, HitAlignmentResult, HitJudgment
from proj7k.profiler.response import analyze_strain_response
from proj7k.profiler.storage import SNAPSHOT_ENGINE, MatchSnapshot, ProfilerStorage

EPS = DEFAULT.eps_rc


def _rate(D: float, level: float, slope: float = 3.0) -> float:
    """A player at `level` loses this share of a chart at D: eps exactly at D = level."""
    logit_eps = math.log(EPS / (1 - EPS))
    return 1.0 / (1.0 + math.exp(-(logit_eps + slope * (math.log(D) - math.log(level)))))


def test_the_fit_reads_where_the_player_loses_the_tolerance():
    level = 14.0
    points = [(D, 1000.0, 1000.0 * _rate(D, level)) for D in (8, 10, 12, 14, 16, 18, 20)]
    assert level_across_plays(points, EPS) == pytest.approx(level, rel=0.02)


def test_the_fit_needs_enough_plays_and_a_positive_slope():
    few = [(D, 1000.0, 20.0) for D in (8, 10, 12, 14)][: MIN_FIT_PLAYS - 1]
    assert level_across_plays(few, EPS) is None
    easier_costs_more = [(D, 1000.0, 1000.0 * _rate(30.0 - D, 14.0)) for D in (8, 10, 12, 14, 16)]
    assert level_across_plays(easier_costs_more, EPS) is None


def test_the_fit_does_not_read_past_the_hardest_chart_played():
    # a player who loses far less than the tolerance on every chart: the level is held at the hardest one
    points = [(D, 1000.0, 1000.0 * _rate(D, 40.0)) for D in (8, 9, 10, 11, 12)]
    assert level_across_plays(points, EPS) == pytest.approx(12.0)


def _snapshot(i: int, D: float, lost_rate: float, play_stars: float) -> MatchSnapshot:
    events = 1000.0
    return MatchSnapshot(
        player_name="P", timestamp=1_700_000_000.0 + i, beatmap_hash=f"b{i}", replay_hash=f"r{i}",
        play_duration_s=120.0, completion_rate=1.0, is_valid_play=True, is_failed=False, overall_ur=100.0,
        capacities={"jack": {"effective_capacity": 0.0, "star_rating": play_stars, "dan_tier": "", "tested": True,
                             "broke_down": False, "chart_level": D, "events": events, "lost": events * lost_rate}},
        fatal_time_ms=None, fatal_column=None, fatal_peak_strains={},
        summary={"engine": SNAPSHOT_ENGINE, "ingest": 3,
                 "overall": {"star_rating": play_stars, "chart_level": D, "events": events,
                             "lost": events * lost_rate, "eps": EPS}},
        created_at=0.0,
    )


def test_one_lucky_play_does_not_set_the_profile(tmp_path: Path):
    level = 14.0
    with ProfilerStorage(db_path=tmp_path / "p.db") as storage:
        for i, D in enumerate((8, 10, 12, 14, 16, 18, 20)):
            storage.save_snapshot(_snapshot(i, D, _rate(D, level), play_stars=stars_of(level)))
        # one play whose own reading is far above the rest
        storage.save_snapshot(_snapshot(99, 20.0, _rate(20.0, 40.0), play_stars=stars_of(40.0)))
        profile = aggregate_macro_profile(storage, "P", horizon_days=None)

    jack = profile.dimensions["jack"]
    assert jack.method == "fit"
    assert jack.star_rating < stars_of(40.0) - 1.0
    assert jack.star_rating == pytest.approx(stars_of(level), abs=0.6)
    assert profile.overall_star_rating == pytest.approx(jack.star_rating, abs=0.05)  # same points, same fit


def test_the_overall_never_reads_above_every_skill_without_an_overall_reading(tmp_path: Path):
    """Hand-built snapshots carry no overall reading: the overall is the strongest skill, not a blend above it."""
    with ProfilerStorage(db_path=tmp_path / "p.db") as storage:
        caps = {k: {"effective_capacity": 15.0, "star_rating": 7.0, "dan_tier": "", "tested": True}
                for k in ("jack", "stream", "speed", "tech")}
        storage.save_snapshot(MatchSnapshot(
            player_name="P", timestamp=1_700_000_000.0, beatmap_hash="b", replay_hash="r", play_duration_s=60.0,
            completion_rate=1.0, is_valid_play=True, is_failed=False, overall_ur=100.0, capacities=caps,
            fatal_time_ms=None, fatal_column=None, fatal_peak_strains={}, summary={"engine": SNAPSHOT_ENGINE},
            created_at=0.0,
        ))
        profile = aggregate_macro_profile(storage, "P", horizon_days=None)
    assert profile.overall_star_rating == pytest.approx(7.0)


def _jack_play(tmp_path: Path, lost_share: float):
    """One long jack on column 0, the first `lost_share` of it missed and the rest hit clean."""
    lines = ["osu file format v14", "", "[General]", "Mode: 3", "", "[Difficulty]", "CircleSize: 7",
             "OverallDifficulty: 8", "", "[TimingPoints]", "0,400,4,2,0,100,1,0", "", "[HitObjects]"]
    lines += [f"36,192,{1000 + i * 140},1,0,0:0:0:0:" for i in range(240)]
    path = tmp_path / "jack.osu"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    beatmap = parse_osu_7k(str(path))
    field = trace_beatmap(beatmap)
    lost = int(round(lost_share(field) * len(field.d)))
    hits = [
        AlignedHit(column=ho.column, target_time=ho.time, hit_time=None if i < lost else ho.time + 3.0,
                   offset_ms=None if i < lost else 3.0,
                   judgment=HitJudgment.MISS if i < lost else HitJudgment.MAX, note_type=ho.note_type)
        for i, ho in enumerate(beatmap.hit_objects)
    ]
    alignment = HitAlignmentResult(aligned_hits=hits, total_hits=len(hits), miss_count=lost)
    return beatmap, field, alignment


def test_a_plays_overall_is_the_engines_total_read_the_other_way(tmp_path: Path):
    beatmap, field, alignment = _jack_play(tmp_path, lambda f: f.eps_total)
    report = analyze_strain_response(alignment, beatmap, field=field)

    assert report.overall is not None
    assert report.overall.chart_level == pytest.approx(field.total_D)
    # losing about the total's tolerance reads about the chart's own total
    assert report.overall.level == pytest.approx(field.total_D, rel=0.15)
    assert report.overall_dan == estimate_canonical_dan(report.overall.star_rating)
