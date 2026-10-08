@echo off
rem proj7k dashboard: live radar, osu!lazer sync, downscaler and profiler in one browser page (ADR-0024).
rem Double-click to start; extra arguments are passed through (e.g. --port 7771 --no-watch).
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m proj7k.dashboard %*
) else (
  python -m proj7k.dashboard %*
)
if errorlevel 1 pause
