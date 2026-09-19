# 0018: Macro Profile Aggregation & SQLite Persistence

## Parent

#32

## What to build

A player can track their skill progression over time across different time windows. The profiler persists lightweight match feature snapshots (timestamp, beatmap hash, 8-dim strain capacities, UR, fatal failure point) into a local SQLite database (`~/.proj7k/profiler.db`), automatically isolated by player username. The system applies noise filtering to discard short warmup or retry-spam matches (<30s or <50% completion) while preserving fatal peak strains from failed runs. Players can query their 30-day Recent Rolling Form (`--horizon-days 30`) or All-time Peak Profile (`--all-time`) via CLI.

## Acceptance criteria

- [ ] Implement SQLite persistence layer creating lightweight match snapshots indexed by player identity and timestamp.
- [ ] Apply noise filter: discard aborted runs with played duration < 30s or completion < 50%, while extracting fatal-point peak strains from Failed matches.
- [ ] Implement rolling time-window aggregator supporting 30-day Recent Rolling Form (`--horizon-days 30`) and All-time Peak Profile (`--all-time`).
- [ ] CLI commands to ingest batches of replays and query/render macro profiles with historical trend comparisons.
- [ ] Black-box tests verifying SQLite persistence, multi-player isolation, noise filtering, and temporal window aggregations.

## Blocked by

- #35 (`docs/tickets/0017-strain-error-response-engine-and-8dim-dan-radar.md`)
