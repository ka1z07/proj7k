"""
Spec §14 acceptance tests that need no calibration: T3, T4, T5, T6, T10.

T1 (strict ladder monotonicity) is not here: v0.2 fails it, and the ratchet that records how
badly lives with the guard (ADR-0018). T2, T7 and T8 are reports, not gates, in this engine.
"""

import random

import numpy as np
import pytest

from engine_support import concat_self, mirror, t10_chart, valid_insert
from proj7k.engine import SKILLS, evaluate_notes
from proj7k.engine.params import DEFAULT

#: Spec §12's global time scale. The engine no longer carries the global accumulator it belonged to
#: (it reaches no output in v0.2), but T4 still states its rest in these units.
TAU_G = 40.0


def test_t3_mirror_invariance(engine_charts, engine_profiles, chart_keys):
    """§14 T3: mirrored chart, D / D_k / pi_k relative change at most 1e-6."""
    worst = 0.0
    for key in chart_keys:
        a, b = engine_profiles[key], evaluate_notes(mirror(engine_charts[key]))
        pairs = [(a.total_D, b.total_D)]
        pairs += [(a.skills[k].D, b.skills[k].D) for k in SKILLS]
        pairs += [(a.skills[k].dominance, b.skills[k].dominance) for k in SKILLS]
        for x, y in pairs:
            worst = max(worst, abs(x - y) / max(abs(x), 1e-12) if x else abs(y))
    assert worst <= 1e-6


def test_t4_size_independence(engine_charts, engine_profiles, chart_keys):
    """§14 T4: a chart concatenated with itself (rest 5 tau_g), N >= 20 N0: |d ln D| <= 0.01."""
    big = [k for k in chart_keys if engine_profiles[k].diagnostics.n_events >= 20 * DEFAULT.N0]
    assert len(big) >= 20, "the benchmark corpus must still contain enough large charts for T4"
    worst = max(
        abs(np.log(evaluate_notes(concat_self(engine_charts[k], 5 * TAU_G)).total_D / engine_profiles[k].total_D))
        for k in big
    )
    assert worst <= 0.01


def test_t5_single_point_saturation(engine_charts, engine_profiles, chart_keys):
    """
    §14 T5: one inserted event moves ln D by O(1/N). The spec gives no constant, so this pins the
    scale N * |d ln D| of v0.2 (random insertion max 10.0, a 20 ms jack insertion max 4.9 on the
    prototype's own draws) against the larger of the two v0.1 readings.
    """
    rnd = random.Random(0)
    worst = {"random": 0.0, "adversarial": 0.0}
    for key in chart_keys:
        a = engine_profiles[key]
        notes = engine_charts[key]
        span = (min(t for _, t, _ in notes), max(t for _, t, _ in notes))
        picks = {
            "random": valid_insert(notes, rnd, span),
        }
        c, t, e = rnd.choice([n for n in notes if n[2] is None] or notes)
        picks["adversarial"] = (c, (e if e is not None else t) + 0.020, None)
        for kind, ins in picks.items():
            b = evaluate_notes(notes + [ins])
            worst[kind] = max(worst[kind], a.diagnostics.n_events * abs(np.log(b.total_D / a.total_D)))
    assert worst["random"] <= 12.3
    assert worst["adversarial"] <= 8.7


def test_t6_attribution_and_dominance_conserve(engine_profiles):
    """§14 T6: sum_k a_ik = 1 for every event, sum_k pi_k = 1, to 1e-9."""
    for key, p in engine_profiles.items():
        assert p.diagnostics.attribution_max_error <= 1e-9, key
        assert p.diagnostics.dominance_sum_error <= 1e-9, key
        assert sum(p.skills[k].dominance for k in SKILLS) == pytest.approx(1.0, abs=1e-9), key


def test_t10_dominance_resists_size_bias():
    """§14 T10: a long easy stream and a short hard jack section: the dominant skill is rc_jack."""
    p = evaluate_notes(t10_chart())
    assert p.dominant_skill == "rc_jack"
    assert p.skills["rc_jack"].coverage < 0.3 < p.skills["rc_jack"].dominance


def test_an_empty_chart_is_refused():
    with pytest.raises(ValueError):
        evaluate_notes([])
