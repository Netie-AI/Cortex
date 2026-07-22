@echo off
title Cortex DMS Demo
cd /d "%~dp0"
echo.
echo  Cortex DMS — portable demo launcher
echo  Root: %CD%
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0\PORTABLE_DEMO.ps1"
if errorlevel 1 (
  echo.
  echo  Demo failed. See messages above or PORTABLE_README.md
  pause
  exit /b 1
)
