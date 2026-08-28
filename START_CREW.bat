@echo off
REM Start Cortex Crew (Guaca-shaped agentic UI) on http://127.0.0.1:8020
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_crew.ps1" -Port 8020 %*
