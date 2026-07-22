@echo off
REM Start Cortex engine API for AirGPT (http://127.0.0.1:8000)
cd /d "%~dp0"
pwsh -NoProfile -File "%~dp0scripts\start_cortex_engine.ps1" %*
