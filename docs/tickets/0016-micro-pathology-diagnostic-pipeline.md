# 0016: Micro-Pathology Diagnostic Pipeline

## Parent

#32

## What to build

A player can run the profiler to analyze a completed replay and view an in-depth biomechanical and reading pathology report. The diagnostic pipeline computes per-track timing variance (UR) and mean hit error to detect bimanual and inter-finger asymmetry, fits linear drift slope across continuous $\Delta k=1$ stagnation intervals (Jack Stagnation Drift) to detect tendon fatigue, decouples LN press and release windows to quantify early panic releases and sticky holds, and pinpoints the 500ms precursor pattern preceding the first fatal cascade failure break.

## Acceptance criteria

- [ ] Compute per-track timing error mean, standard deviation, and Unstable Rate (UR), and calculate left-hand vs. right-hand load asymmetry ratio.
- [ ] Implement Jack Stagnation Drift analyzer that fits linear regression on hit offsets during $\Delta k=1$ stagnation intervals, alerting if drift slope indicates exhaustion.
- [ ] Decouple LN head press and tail release events, calculating hold stickiness rate (late releases) and panic release rate (early releases).
- [ ] Identify fatal combo breaks (Miss/Bad) and extract the preceding 500ms motif topology (bracket inversion, inverse LN density, etc.) as the Cascade Failure Precursor.
- [ ] Integrate pathology diagnostics into CLI report output (tabular text and JSON schema).
- [ ] Black-box tests verifying pathology calculations on synthetic replays simulating known pathologies (asymmetry, drift, sticky release, cascade break).

## Blocked by

- #33 (`docs/tickets/0015-causal-replay-ingestion-and-hit-alignment-cli.md`)
