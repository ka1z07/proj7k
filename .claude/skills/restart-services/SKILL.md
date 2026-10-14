---
name: restart-services
description: Restart the proj7k dashboard (port 7770) and the chart catalog website (port 7780) on Kai's Windows machine. Use when asked to restart, relaunch, bring back up or "start the servers/dashboard/catalog site", or when either page stops responding.
---

# Restart the dashboard and catalog site

Both services run from the main checkout (`C:\Users\Administrator\Documents\proj7k`), not from a worktree.
Exception: when the boot task `proj7k catalog` exists (it does on Kai's machine; it serves the public site
https://7k.kaiz.dev from its own checkout), the script restarts the catalog through that task instead, so a
`git pull` of the main checkout does not change what the public site runs.

1. Make sure the checkout is on `main` and up to date if the user wants the latest code (`git pull --ff-only origin main`).
2. Run the script from the repo root (PowerShell):

   ```powershell
   .\restart-services.cmd
   ```

   Pass `-DashboardOnly` or `-CatalogOnly` to restart just one. The script kills whatever listens on 7770/7780
   (and the venv launcher processes behind them), relaunches both hidden and detached via `Win32_Process.Create`
   so they outlive this session, and waits for the ports to open. It never touches a running `proj7k.catalog crawl`.

3. Verify:
   - `Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7780/beatmapsets` returns 200.
   - `Invoke-WebRequest -UseBasicParsing http://127.0.0.1:7770/api/current` returns 200 and, if osu!lazer is open,
     the chart currently selected in game.

If a port does not open, read the log the script names (`%USERPROFILE%\.cache\proj7k\logs\dashboard.log` or
`catalog.log`) and report the error rather than retrying blindly.
