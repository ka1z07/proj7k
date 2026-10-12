@echo off
rem proj7k chart catalog website, for a Windows scheduled task that starts it at boot (deploy/catalog/README.md).
rem Argument: the catalog database (default: %USERPROFILE%\.cache\proj7k\catalog.sqlite3). Pass it from a
rem task that runs as SYSTEM, whose home folder is not yours.
cd /d "%~dp0..\..\.."
set "PYTHONPATH=%CD%\src"
set "PYTHONUTF8=1"
set "DB=%~1"
if "%DB%"=="" set "DB=%USERPROFILE%\.cache\proj7k\catalog.sqlite3"
".venv\Scripts\python.exe" -m proj7k.catalog --db "%DB%" serve --host 127.0.0.1 --port 7780
