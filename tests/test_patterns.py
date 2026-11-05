"""Pattern tags (ADR-0058, `proj7k.patterns`): what a chart is made of, by the community's names."""

import pytest

from proj7k.patterns import LABEL_SHARE, TAG_DEFINITIONS, TAGS, pattern_tags, pattern_tags_osu


def _rice(seq, dt=0.125, t0=1.0):
    return [(c, t0 + k * dt, None) for k, c in enumerate(seq)]


def _alternating(a, b, n=60, dt=0.125):
    out = []
    for k in range(n):
        out += [(c, 1.0 + k * dt, None) for c in (a if k % 2 == 0 else b)]
    return out


def test_every_tag_has_a_definition_and_a_share():
    report = pattern_tags(_rice([k % 7 for k in range(100)]))
    assert set(report.shares) == set(TAGS) == set(TAG_DEFINITIONS)
    assert all(0.0 <= v <= 1.0 for v in report.shares.values())
    assert report.labels == [k for k, v in sorted(report.shares.items(), key=lambda kv: -kv[1]) if v >= LABEL_SHARE]


def test_an_empty_chart_has_no_tags():
    report = pattern_tags([])
    assert report.labels == [] and report.press_rows == 0


def test_a_single_row_chart_has_no_lag_two_gap():
    assert pattern_tags([(3, 1.0, None)]).press_rows == 1


def test_streams_rolls_and_stairs():
    walk = pattern_tags(_rice([k % 7 for k in range(200)]))
    assert walk.shares["stream"] > 0.95 and walk.shares["roll"] > 0.95
    assert walk.shares["jack"] == 0.0 and walk.shares["trill"] == 0.0
    stair = pattern_tags(_rice([0, 1, 2, 5, 4, 3] * 30))
    assert stair.shares["stair"] > 0.95 and stair.shares["roll"] == 0.0


def test_trills_jumptrills_split_jumptrills_and_brackets():
    trill = pattern_tags(_rice([1, 2] * 60))
    assert trill.shares["trill"] > 0.95 and trill.shares["jumptrill"] == 0.0
    jt = pattern_tags(_alternating((1, 2), (4, 5)))
    assert jt.shares["jumptrill"] > 0.95 and jt.shares["bracket"] == 0.0 and jt.shares["split_jumptrill"] == 0.0
    bracket = pattern_tags(_alternating((0, 2), (1, 3)))
    assert bracket.shares["bracket"] > 0.95
    split = pattern_tags(_alternating((1, 5), (2, 6)))
    assert split.shares["split_jumptrill"] > 0.95 and split.shares["bracket"] > 0.95


def test_jacks_minijacks_chordjacks_and_longjacks():
    jack = pattern_tags(_rice([3] * 120, dt=0.15))
    assert jack.shares["jack"] > 0.95 and jack.shares["longjack"] > 0.95 and jack.shares["minijack"] == 0.0
    assert jack.shares["stream"] == 0.0
    cj = pattern_tags([(c, 1.0 + k * 0.2, None) for k in range(80) for c in (1, 3, 5)])
    assert cj.shares["chordjack"] > 0.95
    mini = pattern_tags(_rice([0, 0, 1, 2, 3, 3, 4, 5] * 25))
    assert mini.shares["minijack"] > 0.4 and mini.shares["jack"] == 0.0


def test_chordstreams_and_dense_chordstreams():
    cols = [(0, 2, 4), (1, 3), (2, 4, 6), (1, 5)]
    cs = pattern_tags([(c, 1.0 + k * 0.15, None) for k in range(100) for c in cols[k % 4]])
    assert cs.shares["chordstream"] == pytest.approx(0.5, abs=0.02) and cs.shares["dense_chordstream"] == 0.0
    dense = pattern_tags([(c, 1.0 + k * 0.15, None) for k in range(100) for c in ((0, 2, 4, 6), (1, 3, 5))[k % 2]])
    assert dense.shares["dense_chordstream"] > 0.9
    js = pattern_tags([(c, 1.0 + k * 0.125, None) for k in range(100) for c in ((k % 7), ((k + 3) % 7))])
    assert js.shares["jumpstream"] > 0.9


def test_graces_bursts_and_polyrhythm():
    grace = pattern_tags([n for k in range(60) for n in ((k % 7, 1.0 + k * 0.25, None), ((k + 3) % 7, 1.03 + k * 0.25, None))])
    assert grace.shares["grace"] == pytest.approx(0.5, abs=0.02)
    # a 1/4 base with 1/16 bursts of eight notes every two seconds
    notes = []
    t = 1.0
    for k in range(20):
        for m in range(8):
            notes.append(((k + m) % 7, t, None)); t += 0.25
        for m in range(8):
            notes.append(((k + m * 2) % 7, t, None)); t += 0.0625
    burst = pattern_tags(notes)
    assert burst.shares["burst"] > 0.3
    poly = pattern_tags([(k % 3, 1.0 + k * 0.125, None) for k in range(100)] + [(4 + k % 3, 1.0 + k * 0.1875, None) for k in range(66)])
    assert poly.shares["polyrhythm"] > 0.05
    assert pattern_tags(_rice([k % 7 for k in range(200)])).shares["polyrhythm"] == 0.0


def test_long_note_tags():
    inverse = pattern_tags([(k % 7, 1.0 + k * 0.25, 2.6 + k * 0.25) for k in range(80)])
    assert inverse.shares["inverse"] > 0.9 and inverse.shares["release"] > 0.9
    release = pattern_tags([(k % 7, 1.0 + k * 0.5, 1.3 + k * 0.5) for k in range(80)])
    assert release.shares["release"] > 0.9 and release.shares["inverse"] == 0.0
    shield = pattern_tags([n for k in range(60) for n in ((k % 7, 1.0 + k * 0.5, None), (k % 7, 1.125 + k * 0.5, 1.4 + k * 0.5))])
    assert shield.shares["shield"] == pytest.approx(0.5, abs=0.02) and shield.shares["hybrid"] > 0.9
    delay = pattern_tags([n for k in range(60) for n in ((k % 7, 1.0 + k * 0.5, 1.3 + k * 0.5), ((k + 3) % 7, 1.0 + k * 0.5, 1.35 + k * 0.5))])
    assert delay.shares["delay"] > 0.9
    coord = pattern_tags([n for k in range(60) for n in ((k % 7, 1.0 + k * 0.5, 1.5 + k * 0.5), ((k + 3) % 7, 1.5 + k * 0.5, None))])
    assert coord.shares["coordination"] > 0.9
    assert pattern_tags(_rice([k % 7 for k in range(100)])).long_notes == 0


def test_the_benchmark_pools_carry_their_own_tags(benchmark_corpus, benchmark_manifest):
    def labels(pool, tier):
        return pattern_tags_osu(benchmark_corpus[int(benchmark_manifest[pool][tier]["id"])]).shares

    assert labels("Regular Jack", "10th")["jack"] > 0.3
    assert labels("Regular Speed", "10th")["stream"] > 0.3
    assert labels("Regular Stream", "10th")["chordstream"] > 0.2
    assert labels("LN Inverse", "4th")["inverse"] > 0.5
    assert labels("LN Release", "10th")["release"] > labels("Regular Speed", "10th")["release"]
    assert labels("LN General", "10th")["hybrid"] > 0.5
