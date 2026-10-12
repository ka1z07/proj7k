"""Short bursts and single-finger overload in practice charts (`downscaler.burst`)."""

import numpy as np
import pytest

from proj7k.dan import CANONICAL_DAN_SR
from proj7k.downscaler import DownscaleOptions, downscale_beatmap
from proj7k.downscaler.burst import ENVELOPES, burst_reading, cap, excess, fit_envelopes
from proj7k.field import d_of_stars, trace_beatmap, trace_notes, trace_osu
from proj7k.parser import parse_osu_7k


def test_the_envelopes_are_the_ones_the_benchmark_charts_give(benchmark_manifest, benchmark_corpus):
    fields = [trace_osu(benchmark_corpus[int(e["id"])]) for tiers in benchmark_manifest.values() for e in tiers.values()]
    fitted = fit_envelopes(fields)
    for kind, (a, b, m) in ENVELOPES.items():
        assert fitted[kind] == pytest.approx((a, b, m), abs=5e-4), kind
    # by construction nine charts in ten sit under each cap at their own level
    for kind in ENVELOPES:
        inside = np.mean([burst_reading(f).peaks()[kind] <= cap(kind, f.total_D) for f in fields])
        assert 0.88 <= inside <= 0.92


def test_a_hammered_column_reads_over_the_cap_and_an_even_stream_does_not():
    # 220 BPM 1/4, the same notes per second: once rolled over all seven columns, once with column 1 taking every
    # other note. The engine's star reads the two alike (its reading is a hand's average); the column reading does not.
    step = 60.0 / 220.0 / 4.0
    even = trace_notes([(k % 7, k * step, None) for k in range(240)])
    locked = trace_notes([((1 if k % 2 == 0 else 2 + (k // 2) % 5), k * step, None) for k in range(240)])
    assert excess(burst_reading(locked), locked.total_D)["column"] > 1.0
    assert excess(burst_reading(even), even.total_D)["column"] < 0.5


@pytest.mark.parametrize("pool,tier", [("Regular Speed", "Gamma"), ("Regular Stream", "Stellium"), ("Regular Jack", "Zenith")])
def test_practice_charts_keep_their_bursts_within_what_real_charts_at_the_target_have(benchmark_manifest, benchmark_corpus, pool, tier):
    bm = parse_osu_7k(benchmark_corpus[int(benchmark_manifest[pool][tier]["id"])])
    res = downscale_beatmap(bm, DownscaleOptions(target_dan="2nd", preserve_technique=False))
    target = CANONICAL_DAN_SR["2nd"]

    assert target * 0.95 <= res.downscaled_stars <= target * 1.05
    pr = res.pruning_result
    assert pr.bursts_within and not any("plays harder" in w for w in res.warnings)
    # what the result reports is what the chart reads
    again = excess(burst_reading(trace_beatmap(res.downscaled_beatmap)), d_of_stars(target))
    assert again == pytest.approx(pr.final_burst)
    assert set(res.to_dict()["pruning"]["final_burst"]) == {"hand", "column"}
