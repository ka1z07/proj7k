# proj7k desktop app: install proj7k's own window (desktop/tauri, ADR-0037) from the `desktop-latest` release.
# `python -m proj7k.desktop` (proj7k.cmd, the shortcut) then opens it instead of an Edge app window. Run it again to
# update; the window only needs updating when desktop/tauri changes, the pages inside it update with the dashboard.
#   powershell -ExecutionPolicy Bypass -File deploy\desktop\install-tauri.ps1
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dir = Join-Path $env:LOCALAPPDATA 'proj7k\desktop'
New-Item -ItemType Directory -Force $dir | Out-Null
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'gh (GitHub CLI) is needed to download the private release: winget install GitHub.cli, then gh auth login' }

$exe = Join-Path $dir 'proj7k.exe'
if (Get-Process -Name proj7k -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
  throw 'proj7k is open: close its window first, then run this again.'
}
gh release download desktop-latest --repo ka1z07/proj7k --pattern 'proj7k-windows.exe' --dir $dir --clobber
Move-Item -Force (Join-Path $dir 'proj7k-windows.exe') $exe
Write-Host "installed: $exe"

# The shortcut's icon is drawn by the same code as the window's: refresh it too.
& (Join-Path $PSScriptRoot 'install-shortcut.ps1')
