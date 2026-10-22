@echo off
rem proj7k: install everything on Windows (Python, Node.js, packages, the proj7k window, shortcuts), then open it.
rem Double-click it after unzipping a release; run it again after unzipping a newer one to update.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\install.ps1" %*
if errorlevel 1 (
  echo.
  echo Install did not finish: the error is above.
)
pause
