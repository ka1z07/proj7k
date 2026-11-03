"""Downscaling to a star range per pool (ADR-0051, `downscaler.pools`)."""

import pytest

from proj7k.downscaler.cli import parse_ranges
from proj7k.downscaler.pipeline import DownscaleOptions, downscale_beatmap
from proj7k.downscaler.pools import PoolRangePruner, misses, pool_stars, ranges_of
from proj7k.downscaler.pruner import ExcessLossPruner
from proj7k.downscaler.validator import DualGateValidator
from proj7k.field import trace_beatmap
from proj7k.generator import mix as mix_mod
from proj7k.parser import parse_osu_7k

#: Regular Stream Stellium (12.88★) brought to Regular Stream Gamma's pools (9.19★; ADR-0056 skill stars, ±0.5★).
#: The lean on deletion order alone takes rc_stamina ~0.3★ under its floor here (before ADR-0056, when the pools
#: read as the total, it took rc_tech ~1★ under; 2026-11-01, 48 benchmark pairs: all pools in 41 vs 48).
SOURCE, TOTAL = 3865216, 9.19
RANGES = {"rc_stamina": (8.35, 9.35), "rc_jack": (7.76, 8.76), "rc_tech": (6.44, 7.44)}


@pytest.fixture(scope="module")
def chart(benchmark_corpus):
    return parse_osu_7k(benchmark_corpus[SOURCE])


def test_ranges_of_a_mix_keeps_only_the_ranges():
    assert ranges_of({"rc_jack": (None, 4.5), "rc_speed": 0.5, "ln_tech": [3, 5]}) == {
        "rc_jack": (None, 4.5), "ln_tech": (3.0, 5.0)}
    assert ranges_of(None) == {}
    assert parse_ranges("rc_jack=..4.5,rc_speed=3.5..5") == {"rc_jack": (None, 4.5), "rc_speed": (3.5, 5.0)}
    assert parse_ranges("") is None
    with pytest.raises(ValueError):
        parse_ranges("rc_jack=0.5")   # a leaning, not a range


def test_misses_reads_over_and_under():
    stars = {"rc_jack": 5.0, "rc_speed": 3.0, "ln_tech": 4.0}
    assert misses(stars, {"rc_jack": (None, 4.5), "rc_speed": (3.5, None), "ln_tech": (3, 5)}) == pytest.approx(
        {"rc_jack": 0.5, "rc_speed": -0.5})


def test_every_pool_lands_in_its_range_where_the_lean_misses_a_floor(chart):
    validator = DualGateValidator(preserve_technique=False)
    res = PoolRangePruner(RANGES, validator=validator).prune(chart, TOTAL)
    f = trace_beatmap(res.downscaled_beatmap)
    assert not misses(pool_stars(f), RANGES), pool_stars(f)
    assert abs(f.profile.total_stars - TOTAL) <= 0.05 * TOTAL + 0.01
    assert res.converged and res.total_notes_removed > 0

    # what the generator did before: the total's pruner with the ranges as a deletion order
    lean = ExcessLossPruner(validator=validator, skill_weights=mix_mod.skill_weights(RANGES, trace_beatmap(chart).profile))
    old = trace_beatmap(lean.prune(chart, TOTAL).downscaled_beatmap)
    assert misses(pool_stars(old), RANGES).get("rc_stamina", 0.0) < -0.2


def test_a_ceiling_alone_brings_that_pool_down_and_keeps_the_rest(chart):
    before = pool_stars(trace_beatmap(chart))
    ceiling = round(before["rc_jack"] - 1.0, 2)
    res = downscale_beatmap(chart, DownscaleOptions(target_dan=None, target_sr=before["rc_jack"] + 2.0,
                                                    preserve_technique=False,
                                                    pool_ranges={"rc_jack": (None, ceiling)}))
    after = pool_stars(trace_beatmap(res.downscaled_beatmap))
    assert after["rc_jack"] <= ceiling + 0.02
    assert res.notes_removed > 0


def test_a_floor_out_of_reach_is_reported_not_forced(chart):
    before = pool_stars(trace_beatmap(chart))
    ranges = {"rc_speed": (before["rc_speed"] + 3.0, None)}
    res = PoolRangePruner(ranges, validator=DualGateValidator(preserve_technique=False)).prune(chart, TOTAL)
    assert not res.converged
    assert any("rc_speed" in w for w in res.warnings)
