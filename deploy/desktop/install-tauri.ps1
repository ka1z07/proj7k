# proj7k desktop app: install proj7k's own window (desktop/tauri, ADR-0037) from the latest release (`desktop-latest`,
# the build of main, with -Channel main or before the first release).
# `python -m proj7k.desktop` (proj7k.cmd, the shortcut) then opens it instead of an Edge app window. Run it again to
# update; the window only needs updating when desktop/tauri changes, the pages inside it update with the dashboard.
#   powershell -ExecutionPolicy Bypass -File deploy\desktop\install-tauri.ps1
param([ValidateSet('release', 'main')][string]$Channel = 'release')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dir = Join-Path $env:LOCALAPPDATA 'proj7k\desktop'
New-Item -ItemType Directory -Force $dir | Out-Null

$exe = Join-Path $dir 'proj7k.exe'
if (Get-Process -Name proj7k -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
  throw 'proj7k is open: close its window first, then run this again.'
}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
$base = 'https://github.com/ka1z07/proj7k/releases'
$urls = @("$base/download/desktop-latest/proj7k-windows.exe")
if ($Channel -eq 'release') { $urls = @("$base/latest/download/proj7k-windows.exe") + $urls }
$download = Join-Path $dir 'proj7k-windows.exe'
$got = $false
foreach ($url in $urls) {
  try {
    Write-Host "downloading $url"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $download
    $got = $true
    break
  } catch {
    Write-Host "  not available: $($_.Exception.Message)"
  }
}
if (-not $got) { throw 'Could not download the proj7k window. Check the network, then run this again.' }
Move-Item -Force $download $exe
Write-Host "installed: $exe"

# The shortcut's icon is drawn by the same code as the window's: refresh it too.
& (Join-Path $PSScriptRoot 'install-shortcut.ps1')
