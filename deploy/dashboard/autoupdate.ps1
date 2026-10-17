# proj7k: keep the dashboard (port 7770) on the latest main of this checkout.
# Two callers (install with install-autoupdate.ps1, see README.md):
#   - git hooks (post-merge, post-checkout, post-rewrite) in this checkout: any pull that moves main restarts
#     the dashboard.
#   - a scheduled task with -Pull every few minutes: fetches origin/main and fast-forwards when the checkout is
#     on main, has no uncommitted changes to tracked files and has not diverged; then restarts the same way.
# Only the dashboard is restarted (restart-services.ps1 -DashboardOnly); the catalog site is never touched.
# Dashboard jobs live only in memory, so while one is queued or running the restart is deferred (a marker
# file remembers it) and the next scheduled run retries. Log: %USERPROFILE%\.cache\proj7k\logs\autoupdate.log.
param(
  [switch]$Pull,
  [string]$Reason = 'manual',
  [int]$DashboardPort = 7770
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cache = Join-Path $env:USERPROFILE '.cache\proj7k'
$logs = Join-Path $cache 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$logFile = Join-Path $logs 'autoupdate.log'
$pending = Join-Path $cache 'dashboard-restart-pending'

function Log($msg) {
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Reason] $msg"
  Add-Content -Path $logFile -Value $line -Encoding utf8
  Write-Host $line
}

function Invoke-Git { & git.exe -C $root @args; if ($LASTEXITCODE -ne 0) { throw "git $args failed ($LASTEXITCODE)" } }

function Test-DashboardBusy {
  try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$DashboardPort/api/jobs" -TimeoutSec 5
  } catch { return $false }   # not answering: nothing to lose by restarting
  $busy = @($r.jobs | Where-Object { $_.status -in 'queued', 'running' })
  if ($busy.Count) { return "$($busy.Count) job(s) running: $(($busy | ForEach-Object { $_.id }) -join ', ')" }
  return $false
}

# The hook fired by our own fast-forward below (we hold the lock and restart ourselves).
if (-not $Pull -and $env:PROJ7K_AUTOUPDATE) { exit 0 }

# One run at a time: a hook firing during a scheduled run (or two pulls back to back) waits its turn.
$mutex = New-Object System.Threading.Mutex($false, 'Global\proj7k-dashboard-autoupdate')
try { $locked = $mutex.WaitOne([TimeSpan]::FromMinutes(2)) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
if (-not $locked) { Log 'another run is still going; skipped'; exit 0 }
try {
  $branch = (& git -C $root symbolic-ref --short -q HEAD)
  if ($branch -ne 'main') {
    if ($Pull) { Log "checkout is on '$branch', not main; not pulling" }
    exit 0
  }

  if ($Pull) {
    $dirty = Invoke-Git status --porcelain --untracked-files=no
    if ($dirty) {
      Log 'uncommitted changes to tracked files; not pulling'
    } else {
      Invoke-Git fetch -q origin main | Out-Null
      $old = (Invoke-Git rev-parse HEAD).Trim()
      $new = (Invoke-Git rev-parse origin/main).Trim()
      if ($old -ne $new) {
        & git -C $root merge-base --is-ancestor $old $new
        if ($LASTEXITCODE -eq 0) {
          $env:PROJ7K_AUTOUPDATE = '1'   # our own merge: the post-merge hook leaves the restart to us
          Invoke-Git merge -q --ff-only origin/main | Out-Null
          Remove-Item Env:PROJ7K_AUTOUPDATE
          Log "fast-forwarded main $($old.Substring(0,7))..$($new.Substring(0,7))"
          New-Item -ItemType File -Force $pending | Out-Null
        } else {
          Log "local main has commits origin/main does not; not pulling"
        }
      }
    }
  } else {
    New-Item -ItemType File -Force $pending | Out-Null
  }

  if (-not (Test-Path $pending)) { exit 0 }
  $busy = Test-DashboardBusy
  if ($busy) { Log "restart deferred: $busy"; exit 0 }

  $head = (Invoke-Git rev-parse --short HEAD).Trim()
  Log "restarting dashboard at $head"
  $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'restart-services.ps1') -DashboardOnly -DashboardPort $DashboardPort
  $code = $LASTEXITCODE
  $out | ForEach-Object { Log "  $_" }
  if ($code -eq 0) { Remove-Item -Force $pending } else { Log "restart failed (exit $code); will retry next run" }
} catch {
  Log "error: $_"
  exit 1
} finally {
  $mutex.ReleaseMutex()
}
