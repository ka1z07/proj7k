"""
Shared physical constants of the difficulty engine.

Every physical threshold consumed by more than one engine stage is defined here exactly once
and referenced from both sites. Where two stages legitimately need different values, both
constants live side by side here with the reason they differ written next to them — the point
is that no stage carries a private literal that can silently drift from the other's.

`CALIBRATION_CONSTANTS` names this module's constants for the methodology fingerprint (read via
`calibration.block_fingerprint_constants`), and the literal-coverage guard asserts it names
*all* of them: a constant added here without being listed is a constant that could move a star
rating without moving the engine version, which is the staleness ADR-0014 exists to prevent
(issue #48).
"""

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Jack (same-lane consecutive striking)
# ---------------------------------------------------------------------------
#: Strain-side jack penalty window. Same-lane consecutive hits closer than this accumulate a
#: graded `(T - dt) / T` penalty inside the continuous strain accumulator.
JACK_INTERVAL_PENALTY_MS: float = 160.0

#: Radar-side chordjack criterion T_jack (ADR-0007): under the discrete step-distance
#: criterion a same-lane note struck one chord step later (Δk = 1) and within this window is
#: classified as a stagnation (jack) note.
#:
#: Not the same constant as JACK_INTERVAL_PENALTY_MS, deliberately: the strain accumulator
#: grades *physiological re-strike load* on one finger, while this classifies *pattern* — a
#: note is a jack only if no other lane was struck in between, at which point a considerably
#: slower re-strike still counts (ADR-0007 explicitly replaced the pure absolute 160 ms window
#: with this Δk-gated criterion). Collapsing the two would move every calibrated star rating
#: on both sides, so it belongs to the calibration rewrite, not to this convergence.
CHORDJACK_STEP_INTERVAL_MS: float = 220.0

# ---------------------------------------------------------------------------
# Antiphase articulation (one lane pressing as another releases)
# ---------------------------------------------------------------------------
#: Strain-side antiphase onset tolerance, in seconds: a release in one lane and a press in
#: another within this gap count as one antiphase articulation event when building the
#: cognitive-impedance term of the dual-hand accumulator.
ANTIPHASE_ONSET_WINDOW_S: float = 0.025

#: Radar-side bracket phase inversion window, in milliseconds: the maximum gap between two
#: consecutive chord steps for an outer/inner → middle finger grip inversion to count as a
#: bracket phase inversion (CONTEXT.md 括号拓扑相变: inversion across adjacent discrete
#: steps, τ_bracket ≈ high-frequency switching).
#:
#: Not the same constant as ANTIPHASE_ONSET_WINDOW_S, deliberately: feature extraction
#: classifies antiphase at the exact release/press tick (the domain definition is "the instant
#: one track is pressed is the instant another is released"), the strain accumulator tolerates
#: a short onset jitter while grading finger load, and this one spans a whole chord step to
#: detect a grip change. Unifying them would change r_stream and the LN release score, i.e.
#: re-calibrate the engine.
BRACKET_PHASE_INVERSION_WINDOW_MS: float = 120.0

# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------
#: Tempo assumed for a chart that carries no uninherited timing point. Read by both the parser
#: (single BPM source) and the feature extractor, which previously carried the same literal in
#: two signatures.
DEFAULT_BPM: float = 150.0


# ---------------------------------------------------------------------------
# Judgment window
# ---------------------------------------------------------------------------
#: osu!mania's 300 judgement window, in milliseconds. It was carried as three separate literals
#: — the strain options default, the radar options default, and the default of
#: `strain.compute_judgment_overlap_buffer` — so a change to one could silently leave the other
#: two behind while every stage still looked self-consistent.
JUDGMENT_WINDOW_MS: float = 38.0


# ---------------------------------------------------------------------------
# Micro-speed burst
# ---------------------------------------------------------------------------
#: Single-finger burst threshold (CONTEXT.md 微观爆发神经应变: Δt < 110 ms, i.e. single-hand
#: 16ths above 136 BPM). Shared by the strain side (speed burst term) and the radar side
#: (speed driver) as the default of their respective options.
SPEED_BURST_INTERVAL_MS: float = 110.0

#: Fastest inter-note gap that still counts as a micro-burst: below this the pair is read as a
#: chord or a duplicate rather than a strike. Shared by `strain.compute_micro_speed_burst` and
#: the radar's speed driver, which must agree on which pairs are bursts at all.
SPEED_BURST_MIN_INTERVAL_MS: float = 5.0

#: Reference interval and exponent of the burst magnitude law
#: `((T_burst - Δt) / REFERENCE) ** EXPONENT`. Both sites scale burst strain with the same law
#: on different totals — the strain side sums it across a hand window, the radar side rates it
#: per second of chart — so the law's shape is shared even though the accumulation is not.
SPEED_BURST_REFERENCE_MS: float = 50.0
SPEED_BURST_EXPONENT: float = 1.35


#: Names of every calibration constant above, for the methodology fingerprint. The
#: literal-coverage guard keeps this list complete: a constant defined here but missing from it
#: would move a star rating without moving the engine version.
CALIBRATION_CONSTANTS: Tuple[str, ...] = (
    "DEFAULT_BPM",
    "JACK_INTERVAL_PENALTY_MS",
    "CHORDJACK_STEP_INTERVAL_MS",
    "ANTIPHASE_ONSET_WINDOW_S",
    "BRACKET_PHASE_INVERSION_WINDOW_MS",
    "JUDGMENT_WINDOW_MS",
    "SPEED_BURST_INTERVAL_MS",
    "SPEED_BURST_MIN_INTERVAL_MS",
    "SPEED_BURST_REFERENCE_MS",
    "SPEED_BURST_EXPONENT",
)

