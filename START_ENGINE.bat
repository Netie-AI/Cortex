@echo off
REM Start Cortex engine API for AirGPT (canonical http://127.0.0.1:8010)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_cortex_engine.ps1" -Port 8010 %*
