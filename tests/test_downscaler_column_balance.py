"""The downscaler keeps the original's column shares (ADR-0044)."""

import numpy as np
import pytest

from proj7k.downscaler import DownscaleOptions, downscale_beatmap
from proj7k.downscaler.pruner import column_retention
from proj7k.downscaler.skeleton import MetricSkeletonDetector, _select_skeleton_anchor
from proj7k.parser import Beatmap7K, HitObject, NoteType, TimingPoint, parse_osu_7k


def _shares(bm):
    return np.bincount([ho.column for ho in bm.hit_objects], minlength=7) / len(bm.hit_objects)


@pytest.mark.parametrize("pool,tier", [("Regular Tech", "Azimuth"), ("Regular Jack", "10th"), ("LN General", "Gamma")])
@pytest.mark.parametrize("free", [False, True])
def test_a_practice_chart_keeps_the_originals_column_shares(benchmark_manifest, benchmark_corpus, pool, tier, free):
    # before ADR-0044 these three lost a third of their middle-finger notes more than the rest (L1 0.28-0.33)
    bm = parse_osu_7k(benchmark_corpus[int(benchmark_manifest[pool][tier]["id"])])
    res = downscale_beatmap(bm, DownscaleOptions(target_dan="2nd", preserve_technique=not free))
    assert res.notes_removed > 0.3 * len(bm.hit_objects)
    assert np.abs(_shares(res.downscaled_beatmap) - _shares(bm)).sum() <= 0.07


def test_a_chart_heavy_on_two_columns_stays_heavy_on_them(benchmark_manifest, benchmark_corpus):
    bm = parse_osu_7k(benchmark_corpus[int(benchmark_manifest["Regular Stream"]["10th"]["id"])])
    rng = np.random.default_rng(0)
    weight = np.array([1, 4, 1, 1, 1, 4, 1], float)
    by_time = {}
    for ho in bm.hit_objects:
        by_time.setdefault(ho.time, []).append(ho)
    for group in by_time.values():
        for ho, c in zip(group, rng.choice(7, size=len(group), replace=False, p=weight / weight.sum())):
            ho.column = int(c)
    before = _shares(bm)
    res = downscale_beatmap(bm, DownscaleOptions(target_dan="2nd", preserve_technique=False))
    after = _shares(res.downscaled_beatmap)
    assert set(np.argsort(after)[-2:]) == {1, 5}
    assert np.abs(after - before).sum() <= 0.08


def test_column_retention_is_each_columns_kept_share_over_the_charts():
    ref = Beatmap7K(hit_objects=[HitObject(column=c, time=float(i), note_type=NoteType.RICE) for i, c in enumerate([0, 0, 1, 1, 2, 2, 3, 3])])
    cur = Beatmap7K(hit_objects=[ho for ho in ref.hit_objects if not (ho.column == 0 and ho.time == 0.0)][:6])
    r = column_retention(cur, ref)   # kept: col0 1/2, col1 2/2, col2 2/2, col3 1/2 of 6/8 overall
    assert r[:4] == pytest.approx([0.5 / 0.75, 1 / 0.75, 1 / 0.75, 0.5 / 0.75])
    assert r[4:] == pytest.approx([1.0, 1.0, 1.0])   # columns the reference never used are in step


def test_a_chords_anchor_favours_no_column_and_follows_the_weight():
    chords = [[HitObject(column=a, time=t * 100.0, note_type=NoteType.RICE), HitObject(column=b, time=t * 100.0, note_type=NoteType.RICE)]
              for t, (a, b) in enumerate((a, b) for a in range(7) for b in range(a + 1, 7) for _ in range(20))]
    counts = np.bincount([_select_skeleton_anchor(ch).column for ch in chords], minlength=7)
    # every column is in 6 of the 21 pairs; a neutral pick gives each about 1/7 of the anchors
    assert counts.max() / counts.sum() < 0.2
    weight = [1.0, 1.0, 1.0, 0.5, 1.0, 1.0, 1.0]
    assert all(_select_skeleton_anchor(ch, weight).column == 3 for ch in chords if 3 in {h.column for h in ch})
    bm = Beatmap7K(hit_objects=[h for ch in chords for h in ch],
                   timing_points=[TimingPoint(time=0.0, beat_length=100.0, meter=4, uninherited=True)])
    assert MetricSkeletonDetector(bm, column_weight=weight).column_weight == weight
