@echo off
REM Start Cortex Crew on http://127.0.0.1:8020 (original /crew.css; not Guaca).
REM start_crew.ps1 refuses a second bind if the port is held (R-0015 / YOU step 8).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_crew.ps1" -Port 8020 %*
