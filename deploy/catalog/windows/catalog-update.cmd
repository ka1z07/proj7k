@echo off
rem proj7k catalog: incremental update from osu! (only what changed since the last run), then re-evaluation,
rem for a Windows scheduled task every few hours (deploy/catalog/README.md). A run takes a minute or two.
rem Needs OSU_CLIENT_ID and OSU_CLIENT_SECRET in the environment of the account the task runs as.
rem Argument: the catalog database (default: %USERPROFILE%\.cache\proj7k\catalog.sqlite3).
cd /d "%~dp0..\..\.."
set "PYTHONPATH=%CD%\src"
set "PYTHONUTF8=1"
set "DB=%~1"
if "%DB%"=="" set "DB=%USERPROFILE%\.cache\proj7k\catalog.sqlite3"
".venv\Scripts\python.exe" -m proj7k.catalog --db "%DB%" update
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m proj7k.catalog --db "%DB%" refresh
