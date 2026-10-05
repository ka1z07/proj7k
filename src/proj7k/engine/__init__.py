"""
The intrinsic-difficulty engine of `osu-mania-7k-difficulty-spec.md` (v0.2), which replaces the
driver/strain engine for total stars and the eight skills (ADR-0017, ADR-0018).

    from proj7k.engine import evaluate_osu
    profile = evaluate_osu(osu_file_content)
    profile.total_stars, profile.dominant_skill, profile.to_dict()

Modules, from input to output: `events` (a chart as rows and events), `demand` (per-event demand and
the hand accumulator), `solver` (difficulty as a threshold), `attribution` (skill membership and
dominance), `scale` (stars from the anchor file), `evaluate` (the public entry and result types),
`params` (every constant) and `version` (the token that follows them).
"""

from proj7k.engine.evaluate import (
    Diagnostics,
    DifficultyProfile,
    SkillReading,
    evaluate_notes,
    evaluate_osu,
)
from proj7k.engine.skills import SKILLS
from proj7k.engine.version import engine_version

__all__ = [
    "Diagnostics",
    "DifficultyProfile",
    "SKILLS",
    "SkillReading",
    "engine_version",
    "evaluate_notes",
    "evaluate_osu",
]
