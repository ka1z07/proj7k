@echo off
rem Weekly full proj7k catalog crawl and re-evaluation (catalog-update.cmd does the frequent runs), for a Windows scheduled task (deploy/catalog/README.md).
rem Needs OSU_CLIENT_ID and OSU_CLIENT_SECRET in the environment of the account the task runs as.
cd /d "%~dp0..\..\.."
set "PYTHONPATH=%CD%\src"
set "PYTHONUTF8=1"
set "DB=%~1"
if "%DB%"=="" set "DB=%USERPROFILE%\.cache\proj7k\catalog.sqlite3"
".venv\Scripts\python.exe" -m proj7k.catalog --db "%DB%" crawl --status all
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m proj7k.catalog --db "%DB%" refresh
