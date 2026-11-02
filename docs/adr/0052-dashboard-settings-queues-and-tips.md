# ADR-0052: Dashboard settings page, per-tool job queues, hover tips and live job clocks

Status: accepted (2026-11-02)

## Context

Kai asked for a usability pass over the dashboard and desktop app: more settings and customisation, batch queues for
each tool, hover tips, more professional wording, and job timers that tick live. Until now each tool page ran one job
at a time (the run button stayed disabled until it finished), forgot every choice on reload, explained options only in
long hint paragraphs, and its job card showed a duration that changed only when the server sent an update.

## Decision

- **Settings live in the browser, not the server.** One JSON object in `localStorage` (`p7.settings`), shared by the
  shell and every page (one origin), read through `P7.settings`; a change reaches open pages through the `storage`
  event. Nothing here needs to be known by the server or by another computer, and the dashboard stays stateless across
  restarts. A new 设置 page (`/settings`) holds: language, animation (follow system / off), hover tips, job alerts
  (corner note, chime, system notification while the window is behind the game), default output folders, default
  .osz packing / lazer collection, and "remember each tool's last choices".
- **Remembered forms** (`P7.rememberForm(page, fields)`): each tool lists the fields worth keeping (target mode,
  downscale mode, ladder, LN share, fast mode, …). Seeds and chart sources are never remembered: they belong to one run.
- **Per-tool queues** (`P7.jobQueue`): the run button never waits for a running job. Each submission becomes an entry
  with its own job card and results, in submission order. The server already orders jobs that share data
  (ADR-0043), so the page only has to submit. Batch forms on top: the downscaler's 阶梯 (target tier plus N tiers
  below, one job each) and the generator's 份数 (N charts, seeds counting up). The generator's sections card keeps its
  own single job card, since its operations edit one project and must not overlap.
- **Hover tips**: any element's `title` shows in the dashboard's own bubble (`.p7-tip`) after 350 ms; `title` stays the
  source of truth, so the i18n swap of static attributes and the tests that check them keep working unchanged.
- **Live clocks**: `P7.jobClock(job)` gives 排队 / 已运行 / 用时, and `P7.tick` repaints every live clock once a second
  (tool job cards, the jobs page, the side bar's job line tip).
- **Job alerts**: the shell, which sees every `job_update`, notes jobs that end after it saw them active.

## Consequences

- Settings are per window/computer; the Mac and meow keep their own.
- A queued run captures the form as it was when submitted; changing the form afterwards does not touch it.
- System notifications depend on the webview granting the Notification API; the page says so when it is missing.
