@echo off
rem proj7k desktop app: the dashboard in its own window (ADR-0034). Double-click to open; it reuses the running
rem dashboard or starts one in the background. Extra arguments pass through (e.g. --window browser, --page library).
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYTHONUTF8=1"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m proj7k.desktop %*
) else (
  start "" pythonw -m proj7k.desktop %*
)
