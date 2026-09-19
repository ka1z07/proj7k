# 0017: Strain-Error Response Engine & 8-Dim Dan Radar

## Parent

#32

## What to build

A player can run the profiler to determine their objective skill level across all 8 technique dimensions (Jack, Tech, Speed, Stream, LN General, LN Tech, LN Inverse, LN Release). The system aligns discrete hit deviations against the continuous strain curves $S_d(t)$ computed by `proj7k.strain`, identifies the critical inflection threshold where hit error variance spikes or misses cluster, computes the player's 8-dimensional Effective Strain Capacity, and maps each capacity to the 15-level Jinjin Dan progression hierarchy (`Canonical Dan Progression Hierarchy`) to produce a canonical skill radar.

## Acceptance criteria

- [ ] Align timestamped hit offsets point-to-point with `proj7k.strain` instantaneous strain values $S_d(t)$ across all 8 technique dimensions.
- [ ] Bin hit errors by strain intensity and fit error variance / miss density response curves to determine the Effective Strain Capacity inflection point per dimension.
- [ ] Map each 8-dimensional capacity value to the canonical 15-level Jinjin Dan progression hierarchy (0th to Stellium) and continuous star rating.
- [ ] Format and display the resulting 8-dimension capability radar and tier breakdown in CLI stdout and JSON output.
- [ ] Black-box tests verifying accurate strain-error inflection detection and Dan tier mapping on controlled synthetic replay/beatmap pairs.

## Blocked by

- #34 (`docs/tickets/0016-micro-pathology-diagnostic-pipeline.md`)
