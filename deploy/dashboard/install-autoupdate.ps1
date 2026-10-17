# proj7k: install (or with -Uninstall remove) the dashboard autoupdate on this Windows machine:
#   - git hooks post-merge / post-checkout / post-rewrite in this checkout, which run autoupdate.ps1;
#   - the scheduled task "proj7k dashboard autoupdate" under the current user, which runs autoupdate.ps1 -Pull
#     every -IntervalMinutes while that user is logged in (no password stored).
# Re-running is safe. Hooks that someone else wrote are left alone. See README.md.
param(
  [switch]$Uninstall,
  [int]$IntervalMinutes = 5
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script = Join-Path $root 'deploy\dashboard\autoupdate.ps1'
$taskName = 'proj7k dashboard autoupdate'
$marker = '# proj7k dashboard autoupdate'

$hooksDir = (& git -C $root rev-parse --path-format=absolute --git-path hooks).Trim()
New-Item -ItemType Directory -Force $hooksDir | Out-Null
$hook = @"
#!/bin/sh
$marker (installed by deploy/dashboard/install-autoupdate.ps1; never blocks git)
hook=`$(basename "`$0")
# post-checkout: only a branch switch that moved HEAD. post-rewrite: only a rebase (pull --rebase), not amend.
[ "`$hook" = post-checkout ] && { [ "`$3" = 1 ] && [ "`$1" != "`$2" ] || exit 0; }
[ "`$hook" = post-rewrite ] && { [ "`$1" = rebase ] || exit 0; }
# Linked worktrees share these hooks; only the main working tree serves the dashboard.
[ "`$(git rev-parse --git-dir)" = "`$(git rev-parse --git-common-dir)" ] || exit 0
script="`$(git rev-parse --show-toplevel)/deploy/dashboard/autoupdate.ps1"
[ -f "`$script" ] || exit 0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "`$(cygpath -w "`$script")" -Reason "`$hook" || true
exit 0
"@ -replace "`r`n", "`n"

foreach ($name in 'post-merge', 'post-checkout', 'post-rewrite') {
  $path = Join-Path $hooksDir $name
  $ours = (Test-Path $path) -and ((Get-Content $path -Raw) -match [regex]::Escape($marker))
  if ($Uninstall) {
    if ($ours) { Remove-Item $path; Write-Host "removed hook $path" }
    continue
  }
  if ((Test-Path $path) -and -not $ours) { Write-Warning "$path exists and is not ours; left alone"; continue }
  [IO.File]::WriteAllText($path, $hook, (New-Object Text.UTF8Encoding $false))
  Write-Host "installed hook $path"
}

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
  if ($Uninstall) { Write-Host "removed scheduled task '$taskName'" }
}
if ($Uninstall) { exit 0 }

# conhost --headless keeps the console window from flashing up every few minutes.
$action = New-ScheduledTaskAction -Execute 'conhost.exe' -WorkingDirectory $root -Argument (
  "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden " +
  "-File `"$script`" -Pull -Reason scheduled")
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
  -Settings $settings -Description "Fast-forwards $root to origin/main and restarts the proj7k dashboard (7770)." | Out-Null
Write-Host "registered scheduled task '$taskName' (every $IntervalMinutes min; log: %USERPROFILE%\.cache\proj7k\logs\autoupdate.log)"
