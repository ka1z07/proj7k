# proj7k on Windows, in one go: Python 3.14 and Node.js (through winget when missing), this folder's .venv and its
# packages, the osu!lazer database bridge, proj7k's own window from the latest release, and the Start-menu and
# desktop shortcuts. Safe to run again: it is also how an update is finished after unzipping a new release.
#   double-click install.cmd, or: powershell -ExecutionPolicy Bypass -File deploy\install.ps1
param([switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

function Step($text) { Write-Host ''; Write-Host "==> $text" -ForegroundColor Cyan }

# winget writes PATH to the registry; this window only sees it after re-reading.
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Winget-Install($id, $name) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "$name is missing and winget is not available: install $name by hand, then run install.cmd again."
  }
  Write-Host "Installing $name with winget..."
  winget install --exact --id $id --scope user --accept-package-agreements --accept-source-agreements --silent
  if ($LASTEXITCODE -ne 0) {
    # Some packages have no per-user installer: try the machine-wide one (asks for administrator rights).
    winget install --exact --id $id --accept-package-agreements --accept-source-agreements --silent
  }
  Refresh-Path
}

# A Python 3.14 to build the .venv with: the py launcher first, then python on PATH.
function Find-Python {
  $candidates = @()
  if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @('py', '-3.14') }
  if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += , @('python') }
  foreach ($c in $candidates) {
    $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
    try {
      $ok = & $exe @rest -c "import sys; print(sys.version_info[:2] >= (3, 14))" 2>$null
      if ($ok -eq 'True') { return , $c }
    } catch {}
  }
  return $null
}

[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

Step 'Python 3.14'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
  Write-Host 'Using the existing .venv'
} else {
  $py = Find-Python
  if (-not $py) {
    Winget-Install 'Python.Python.3.14' 'Python 3.14'
    $py = Find-Python
    if (-not $py) { throw 'Python was installed but this window cannot see it yet: close it and run install.cmd again.' }
  }
  $exe = $py[0]; $rest = @($py | Select-Object -Skip 1)
  & $exe @rest -m venv (Join-Path $root '.venv')
  if ($LASTEXITCODE -ne 0) { throw 'python -m venv failed' }
}

Step 'Python packages (requirements.txt)'
& $venvPython -m pip install --disable-pip-version-check -q --upgrade pip
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install -r requirements.txt failed' }

Step 'Node.js (reads the osu!lazer database)'
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Winget-Install 'OpenJS.NodeJS.LTS' 'Node.js LTS' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw 'Node.js was installed but this window cannot see it yet: close it and run install.cmd again.'
}
Push-Location (Join-Path $root 'tools\lazer-bridge')
try {
  npm install --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'npm install (tools\lazer-bridge) failed' }
} finally { Pop-Location }

Step 'The proj7k window and shortcuts'
& (Join-Path $root 'deploy\desktop\install-tauri.ps1')

Write-Host ''
Write-Host 'Done. From now on, open proj7k from the Start menu or the desktop.' -ForegroundColor Green
if (-not $NoLaunch) { & (Join-Path $root 'proj7k.cmd') }
