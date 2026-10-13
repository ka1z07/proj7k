@echo off
rem proj7k: restart the dashboard (7770) and the chart catalog site (7780), detached, from this checkout.
rem Double-click to run; arguments pass through (-DashboardOnly, -CatalogOnly). See restart-services.ps1.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-services.ps1" %*
if errorlevel 1 pause
