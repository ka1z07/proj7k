# proj7k desktop app: put a "proj7k" shortcut on the desktop and in the Start menu (ADR-0034).
# The shortcut runs `pythonw -m proj7k.desktop` from this checkout's .venv: no console window; it opens the
# dashboard in its own app window and starts the dashboard first if it is not running.
#   powershell -ExecutionPolicy Bypass -File deploy\desktop\install-shortcut.ps1
param([switch]$NoDesktop)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command pythonw).Source }
$python = Join-Path (Split-Path $pythonw) 'python.exe'

$iconDir = Join-Path $env:USERPROFILE '.proj7k\desktop'
New-Item -ItemType Directory -Force $iconDir | Out-Null
$icon = Join-Path $iconDir 'proj7k.ico'
$env:PYTHONPATH = Join-Path $root 'src'
& $python -m proj7k.desktop.icon $icon | Out-Null

# A shortcut carries no environment, so it goes through cmd to set PYTHONPATH (minimised, then pythonw takes over).
$targets = @(Join-Path ([Environment]::GetFolderPath('Programs')) 'proj7k.lnk')
if (-not $NoDesktop) { $targets += Join-Path ([Environment]::GetFolderPath('Desktop')) 'proj7k.lnk' }
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $targets) {
  $lnk = $shell.CreateShortcut($path)
  $lnk.TargetPath = Join-Path $root 'proj7k.cmd'
  $lnk.WorkingDirectory = $root
  $lnk.IconLocation = "$icon,0"
  $lnk.WindowStyle = 7
  $lnk.Description = 'proj7k: 7K player home, play history, library and practice tools'
  $lnk.Save()
  Write-Host "shortcut: $path"
}
