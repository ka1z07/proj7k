"""
Shared physical constants of the difficulty engine.

Every physical threshold consumed by more than one engine stage is defined here exactly once
and referenced from both sites. Where two stages legitimately need different values, both
constants live side by side here with the reason they differ written next to them — the point
is that no stage carries a private literal that can silently drift from the other's.
"""

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
# Micro-speed burst
# ---------------------------------------------------------------------------
#: Single-finger burst threshold (CONTEXT.md 微观爆发神经应变: Δt < 110 ms, i.e. single-hand
#: 16ths above 136 BPM). Shared by the strain side (speed burst term) and the radar side
#: (speed driver) as the default of their respective options.
SPEED_BURST_INTERVAL_MS: float = 110.0
