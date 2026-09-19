# 0019: Coaching Recommendations & Downscaler Practice Bundle Pipeline

## Parent

#32

## What to build

A player can complete the training feedback loop after receiving a diagnosis. The profiler provides two coaching strategies: Bottleneck Breaker (targeting the limiting skill dimension) and Specialty Push (advancing the player's strongest skill). The recommendation engine strictly avoids Dan test maps and recalls candidate practice beatmaps from the player's installed local library (via Realm skill tags and star ratings). Furthermore, when a fatal failure point is diagnosed, the pipeline extracts a High-Strain Section Slice ($[t_{\text{fatal}} - 10\text{s}, t_{\text{fatal}} + 5\text{s}]$) and invokes `proj7k.downscaler` to automatically generate a Three-Tier Targeted Practice Bundle (Recovery, Bridge, Push) packaged as standalone `.osz` files.

## Acceptance criteria

- [ ] Implement dual coaching strategies: Bottleneck Breaker (identifying lowest limiting dimension) and Specialty Push (identifying highest developed dimension).
- [ ] Scan local Realm database to recall installed candidate beatmaps matching the target skill tag and tier, with strict exclusion of Dan test maps.
- [ ] Extract High-Strain Section Slice surrounding diagnosed fatal failure timestamp ($[-10\text{s}, +5\text{s}]$) with proper audio/hitobject buffers.
- [ ] Call `proj7k.downscaler` pipeline to generate Three-Tier Progression Bundle (Recovery at current capacity, Bridge at intermediate strain, Push at original strain) as standalone `.osz` files.
- [ ] CLI arguments `--recommend` and `--bundle` integrated into `proj7k.profiler` pipeline and tested end-to-end.
- [ ] Black-box tests verifying coaching recommendation logic, Dan map exclusion, slice extraction, and downscaler bundle generation.

## Blocked by

- #36 (`docs/tickets/0018-macro-profile-aggregation-and-sqlite-persistence.md`)
