"""The practice bundle and the fatal precursor on the engine's field (ADR-0020, ADR-0021)."""

import hashlib
import json
from pathlib import Path

import pytest

from replay_sim import simulate_frames
from proj7k.engine.scale import stars_of
from proj7k.engine.skills import SKILL_TECH_KEY
from proj7k.field import trace_beatmap
from proj7k.parser import parse_osu_7k
from proj7k.profiler.cli import main, run_ingestion
from proj7k.profiler.coach import RECOVERY_MAX_SHARE, generate_targeted_practice_bundle
from proj7k.profiler.osr import OSRReplay, serialize_osr


@pytest.fixture(scope="module")
def failing_play(tmp_path_factory, benchmark_manifest, benchmark_corpus):
    """A player a little under a Regular Jack 9th chart's level who stops playing 70 s in."""
    folder = tmp_path_factory.mktemp("fail")
    osu = folder / "chart.osu"
    osu.write_text(benchmark_corpus[int(benchmark_manifest["Regular Jack"]["9th"]["id"])], encoding="utf-8")
    beatmap = parse_osu_7k(str(osu))
    field = trace_beatmap(beatmap)
    frames = simulate_frames(beatmap, field, field.total_D * 0.8, seed=2, fail_at_ms=70000.0)
    replay = OSRReplay(
        mode=3, game_version=20240101, beatmap_hash=hashlib.md5(osu.read_bytes()).hexdigest(), player_name="Sim",
        replay_hash="fail", timestamp_ticks=638000000000000000, action_frames=frames,
        life_bar=f"0|1.0,{int(frames[-1].time_ms) - 500}|0.1,{int(frames[-1].time_ms)}|0.0",
    )
    osr = folder / "fail.osr"
    osr.write_bytes(serialize_osr(replay))
    return folder, osu, osr, beatmap, field


def test_the_fatal_precursor_names_the_skill_carrying_the_loss(failing_play):
    _, osu, osr, *_ = failing_play
    report = run_ingestion(osr, osu)
    pre = report.pathology.cascade_precursor

    assert pre is not None and pre.fatal_time_ms is not None
    assert pre.skill in SKILL_TECH_KEY.values()
    assert report.pathology.to_dict()["cascade_precursor"]["skill"] == pre.skill


def test_the_bundle_tiers_are_engine_stars_in_order(failing_play, tmp_path):
    _, osu, osr, beatmap, field = failing_play
    report = run_ingestion(osr, osu)
    fatal = report.pathology.cascade_precursor.fatal_time_ms
    level = report.skill_radar.dimensions["jack"].effective_capacity

    bundle = generate_targeted_practice_bundle(
        beatmap, fatal, player_capacity=level, dominant_technique=report.pathology.cascade_precursor.skill,
        output_dir=tmp_path / "b",
    )

    rec, bri, pus = (bundle.tiers[k] for k in ("recovery", "bridge", "push"))
    assert pus.star_rating == pytest.approx(bundle.original_star)
    assert rec.target_sr == pytest.approx(min(stars_of(level), bundle.original_star * RECOVERY_MAX_SHARE))
    assert rec.target_sr < bri.target_sr < pus.target_sr
    # each lower tier is made to land on its target star, and the engine reads it there
    for tier in (rec, bri):
        assert tier.star_rating <= tier.target_sr * 1.05
        assert tier.star_rating == pytest.approx(trace_beatmap(tier.beatmap).profile.total_stars)
    assert rec.star_rating < bri.star_rating < pus.star_rating
    assert len(rec.beatmap.hit_objects) < len(bri.beatmap.hit_objects) < len(pus.beatmap.hit_objects)
    assert bundle.combined_osz_path.exists()


def test_cli_bundle_from_a_failed_replay_end_to_end(failing_play, tmp_path, capsys):
    _, osu, osr, *_ = failing_play
    assert main(["-r", str(osr), "-b", str(osu), "--bundle", "--bundle-dir", str(tmp_path / "cli"), "--no-save", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    tiers = data["practice_bundle"]["tiers"]
    assert tiers["recovery"]["star_rating"] < tiers["bridge"]["star_rating"] < tiers["push"]["star_rating"]
    assert data["practice_bundle"]["original_star"] == pytest.approx(tiers["push"]["star_rating"], abs=0.01)
    assert data["skill_radar"]["dimensions"]["jack"]["tested"] is True
