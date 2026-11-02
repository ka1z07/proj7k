# ADR-0053: Progress page, hints after a bad run, and a stream overlay

Status: accepted (2026-11-02)

## Context

From the usability plan (ADR-0052) Kai picked three player features: progress curves with a target dan, a suggestion
after a failed play, and an in-game / OBS overlay.

## Decision

- **Progress page** (`/progress`, side bar → 进步). `player.progress_series` reruns the profiler's own fit
  (`aggregate_macro_profile`, ADR-0028) over the 30 days of replays ending at points at least a week apart (at most
  40, the last at the newest replay), so every point means the same as the profiler page's 30-day level. Served by
  `/api/me/progress`, cached on the profiler database's mtime. The target dan is a setting (`goal_dan`, ADR-0052);
  the page shows the overall gap, a pace estimate from the last eight points, each skill's gap with a "find charts"
  link into the library, and per-skill small multiples. Without replays it falls back on each week's hardest clear.
- **Hint after a bad run.** The shell polls `/api/me/latest` (five newest plays, small) every 20 s while connected;
  the first read only marks where "new" starts. A new failed or D-rank play shows a note with where it broke and on
  what (from the profiler when the replay was read, else the chart's main skill) and three actions: a practice chart a
  tier lower (the downscaler already suggests one tier below a picked chart), diagnose the replay (profiler with
  `run`), view the play. Nothing runs on its own: a job starts only from a click. Setting `fail_hint` turns it off.
- **Overlay** (`/overlay`): a see-through page for an OBS browser source or a small window. It follows the game's
  chart over the dashboard WebSocket and shows stars, dan, main technique, the chart's eight skills with the
  player's level marked (yellow where the chart is above it), and the personal best. Options ride the URL
  (layout card/bar, parts, size, background, hide when idle, language); the settings page builds and previews it.
  It does not draw over a fullscreen game itself: on one monitor it is for streaming, or for borderless/windowed play.

## Consequences

- The progress fit costs one profile aggregation per point; it is recomputed only when the profiler database changes.
- The bad-run hint sees plays only once osu!lazer has written them to its database and the library re-reads it.
