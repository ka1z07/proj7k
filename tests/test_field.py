"""The difficulty field (ADR-0020): the engine's own per-event readings, laid out in time."""

import numpy as np
import pytest

from proj7k.engine import SKILLS, evaluate_notes
from proj7k.engine.attribution import ln_context
from proj7k.engine.events import notes_from_osu
from proj7k.engine.params import DEFAULT
from proj7k.engine.scale import stars_of
from proj7k.field import d_of_stars, level_for_loss, trace_notes

CHARTS = [("Regular Jack", "5th"), ("Regular Stream", "10th"), ("LN Inverse", "7th"), ("LN Release", "3rd")]


@pytest.fixture(scope="module", params=CHARTS, ids=[f"{p}-{t}" for p, t in CHARTS])
def traced(request, benchmark_manifest, benchmark_corpus):
    pool, tier = request.param
    notes = notes_from_osu(benchmark_corpus[int(benchmark_manifest[pool][tier]["id"])])
    return notes, trace_notes(notes)


def test_profile_is_the_engines_own(traced):
    notes, field = traced
    assert field.profile == evaluate_notes(notes)
    assert field.total_D == field.profile.total_D


def test_arrays_line_up_with_events(traced):
    _, f = traced
    n = f.events.n
    for arr in (f.t, f.col, f.release, f.d, f.v, f.p, f.share):
        assert len(arr) == n
    assert f.w.shape == f.a.shape == (n, len(SKILLS))
    assert np.all(np.diff(f.t) >= 0)
    assert abs(f.share.sum() - 1.0) < 1e-9
    assert np.allclose(f.a.sum(1), 1.0)


def test_expected_loss_at_the_charts_level_is_its_tolerance(traced):
    """Solving the total at D means the unit-weight expected loss is eps (W + N0), by definition (§3.2)."""
    _, f = traced
    eps = DEFAULT.eps_rc + DEFAULT.eps_total_ln_slope * ln_context(f.events, DEFAULT).mean()
    assert f.p.sum() == pytest.approx(eps * (f.events.n + DEFAULT.N0), rel=1e-6)


def test_level_for_loss_inverts_the_engines_equation(traced):
    """A player who loses exactly the tolerance sits at the skill's D_k; the same call, the engine's own number."""
    _, f = traced
    for k, name in enumerate(SKILLS):
        wk = f.w[:, k]
        if wk.sum() < 5 or f.profile.skills[name].D <= 0:
            continue
        eps = DEFAULT.eps_rc if name.startswith("rc") else DEFAULT.eps_ln
        level = level_for_loss(f.d, wk, eps * wk.sum(), eps)
        assert level == pytest.approx(f.profile.skills[name].D, rel=1e-6), name


def test_level_rises_as_less_is_lost(traced):
    _, f = traced
    ones = np.ones(f.events.n)
    levels = [level_for_loss(f.d, ones, x * f.events.n, 0.04) for x in (0.20, 0.08, 0.04, 0.01, 0.0)]
    assert levels == sorted(levels) and levels[0] < levels[-1]


def test_level_clamps_at_the_scale_ends():
    d = np.array([5.0, 6.0, 7.0])
    w = np.ones(3)
    assert level_for_loss(d, w, 3.0, 0.04) == DEFAULT.theta_min      # lost everything
    assert level_for_loss(d, np.zeros(3), 0.0, 0.04) == 0.0          # nothing to fail at


def test_curve_conserves_risk_and_events(traced):
    _, f = traced
    c = f.curve(bin_s=2.0)
    assert c.risk.sum() == pytest.approx(f.p.sum())
    assert c.events.sum() == f.events.n
    assert c.load.max() == pytest.approx(f.d.max() / f.total_D)
    assert set(np.unique(c.skill)) <= set(range(-1, len(SKILLS)))


def test_hot_spots_are_disjoint_and_ranked(traced):
    _, f = traced
    spots = f.hot_spots(window_s=6.0, top=4)
    assert spots
    assert [s.risk for s in spots] == sorted((s.risk for s in spots), reverse=True)
    for i, a in enumerate(spots):
        assert a.skill in SKILLS and 0 < a.share <= 1
        for b in spots[i + 1:]:
            assert a.end_s <= b.start_s or b.end_s <= a.start_s


def test_star_scale_inverse():
    for s in (3.0, 6.1, 11.8):
        assert stars_of(d_of_stars(s)) == pytest.approx(s)
    assert d_of_stars(0.0) == 0.0


def test_played_notes_find_their_events(traced):
    notes, f = traced
    found = [f.press_index(c, t) for c, t, _ in notes]
    assert sum(i is not None for i in found) >= 0.98 * len(notes)
    # an LN's release is the release event of the same object, on the same column, at its tail
    for (c, t, e), i in zip(notes, found):
        r = None if i is None else f.release_index(i)
        if r is not None:
            assert f.release[r] and f.col[r] == c == f.col[i] and abs(f.t[r] - e) < 0.0025
