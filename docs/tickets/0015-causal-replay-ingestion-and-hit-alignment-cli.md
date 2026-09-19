# 0015: Causal Replay Ingestion & Hit Alignment CLI

## Parent

#32

## What to build

A player can pass a mania `.osr` replay and matching `.osu` beatmap to the profiler CLI (`python3 -m proj7k.profiler --replay <path.osr> --beatmap <path.osu>`) or pipeline entrypoint. The system parses the replay's LZMA action frames, consumes hit objects in causal order within OD judgment windows, separates valid hits from misses, isolates out-of-window and empty-track taps as "惊慌鬼键 (Panic Ghost Tap)", and outputs a summary of judgment counts and ghost tap distribution in both terminal text and structured JSON.

## Acceptance criteria

- [ ] Parse `.osr` binary format and extract player name, beatmap MD5, timestamp, mods, and mania discrete key press/release frames.
- [ ] Implement Causal Hit-Window Matcher that processes hit objects forward in time and consumes the first valid key press within `[t_target - W_judg, t_target + W_judg]`.
- [ ] Correctly classify missed hit objects when no valid key press occurs within the judgment window.
- [ ] Isolate key presses outside of any active judgment window or on empty tracks as `Panic Ghost Tap` without contaminating hit error statistics.
- [ ] CLI accepts `--replay` and `--beatmap` flags (with optional `--json`), outputting formatted judgment counts, miss counts, and ghost tap metrics.
- [ ] Comprehensive black-box test suite against synthetic and real `.osr` streams verifying hit alignment and ghost tap isolation.

## Blocked by

None (can start immediately).
