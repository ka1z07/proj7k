# proj7k: restart the dashboard (port 7770) and the chart catalog site (port 7780) from this checkout.
# Kills whatever serves those ports, then relaunches both detached (hidden, outside the caller's
# process tree) so they survive the terminal or Claude session that started them.
# A running `proj7k.catalog crawl` is never touched. Logs: %USERPROFILE%\.cache\proj7k\logs\.
# Where the boot task "proj7k catalog" exists (deploy/catalog/README.md), the catalog is restarted through
# that task instead, so the public site keeps running from the task's own checkout and settings.
param(
  [switch]$DashboardOnly,
  [switch]$CatalogOnly,
  [int]$DashboardPort = 7770,
  [int]$CatalogPort = 7780
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = (Get-Command python).Source }
$cache = Join-Path $env:USERPROFILE '.cache\proj7k'
$logs = Join-Path $cache 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null

$catalogTask = 'proj7k catalog'
$services = @()
if (-not $CatalogOnly) {
  $services += @{ Name = 'dashboard'; Port = $DashboardPort; Match = 'proj7k\.dashboard'
                  Args = "-m proj7k.dashboard --port $DashboardPort --no-open" }
}
if (-not $DashboardOnly) {
  $db = Join-Path $cache 'catalog.sqlite3'
  $services += @{ Name = 'catalog'; Port = $CatalogPort; Match = 'proj7k\.catalog\b.*\bserve\b'
                  Args = "-m proj7k.catalog --db `"$db`" serve --port $CatalogPort" }
}

function Stop-Service7k($svc) {
  $ids = @()
  $ids += Get-NetTCPConnection -State Listen -LocalPort $svc.Port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess
  # The venv's python.exe is a launcher that spawns the real interpreter; catch both by command line.
  $ids += Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match $svc.Match } |
    Select-Object -ExpandProperty ProcessId
  foreach ($id in ($ids | Where-Object { $_ } | Sort-Object -Unique)) {
    try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host "stopped $($svc.Name) pid $id" } catch {}
  }
}

function Start-Service7k($svc) {
  if ($svc.Name -eq 'catalog' -and (Get-ScheduledTask -TaskName $catalogTask -ErrorAction SilentlyContinue)) {
    Start-ScheduledTask -TaskName $catalogTask
    Write-Host "started catalog via scheduled task '$catalogTask' (its own checkout and log)"
    return
  }
  $log = Join-Path $logs "$($svc.Name).log"
  # Win32_Process.Create starts the process under WMI, outside this shell's job object, so it
  # outlives the session. It does not inherit our environment, hence the `set` in cmd.
  $cmd = "cmd.exe /c set `"PYTHONPATH=$root\src`"&& set PYTHONUTF8=1&& `"$python`" $($svc.Args) > `"$log`" 2>&1"
  $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmd; CurrentDirectory = $root; ProcessStartupInformation = $startup }
  if ($r.ReturnValue -ne 0) { throw "failed to start $($svc.Name) (Win32_Process.Create returned $($r.ReturnValue))" }
  Write-Host "started $($svc.Name) on port $($svc.Port) (log: $log)"
}

function Wait-Port($svc) {
  for ($i = 0; $i -lt 60; $i++) {
    if (Get-NetTCPConnection -State Listen -LocalPort $svc.Port -ErrorAction SilentlyContinue) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

if (-not $DashboardOnly) { Stop-ScheduledTask -TaskName $catalogTask -ErrorAction SilentlyContinue }
foreach ($svc in $services) { Stop-Service7k $svc }
Start-Sleep -Milliseconds 500
foreach ($svc in $services) { Start-Service7k $svc }
$failed = $false
foreach ($svc in $services) {
  if (Wait-Port $svc) { Write-Host "$($svc.Name) is up: http://127.0.0.1:$($svc.Port)/" }
  else { Write-Host "$($svc.Name) did not open port $($svc.Port); see $(Join-Path $logs "$($svc.Name).log")"; $failed = $true }
}
if ($failed) { exit 1 }
