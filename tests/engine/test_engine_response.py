"""
Spec §14 T11: the dominance distribution responds to controlled edits, independent of any slot label.

Each edit changes one construct of a benchmark chart; the dominance of the skill it concerns must
move the stated way on at least five of six charts. Dominance answers "what determines the
difficulty" (project ruling, 2026-10-02), so this is its score; the slot labels are a diagnostic.
"""

import random

import pytest

from engine_support import EDITS, SKILLS_LN, T11_TIERS, lnify
from proj7k.engine import evaluate_notes

NEEDED = 5


def _dominance(notes):
    p = evaluate_notes(notes)
    return {k: s.dominance for k, s in p.skills.items()}


@pytest.mark.parametrize("name", list(EDITS))
def test_t11_edit_moves_dominance_the_expected_way(name, engine_charts):
    edit, pool, skill, sign = EDITS[name]
    deltas = []
    for tier in T11_TIERS:
        notes = engine_charts[(pool, tier)]
        base = _dominance(notes)[skill]
        edited = _dominance(edit(notes, random.Random(7)))[skill]
        deltas.append(edited - base)
    right = sum(d * sign > 0 for d in deltas)
    assert right >= NEEDED, f"{name}: d pi({skill}) = {[round(d, 3) for d in deltas]}, expected {'up' if sign > 0 else 'down'}"


def test_t11_turning_rice_into_holds_raises_the_ln_family(engine_charts):
    deltas = []
    for tier in T11_TIERS:
        notes = engine_charts[("Regular Stream", tier)]
        base = sum(_dominance(notes)[k] for k in SKILLS_LN)
        edited = sum(_dominance(lnify(notes, random.Random(7)))[k] for k in SKILLS_LN)
        deltas.append(edited - base)
    assert sum(d > 0 for d in deltas) >= NEEDED, [round(d, 3) for d in deltas]
