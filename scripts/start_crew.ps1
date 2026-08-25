#!/usr/bin/env pwsh
param(
    [int]$Port = 8020,
    [string]$EngineUrl = "http://127.0.0.1:8010"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$env:CREW_PORT = "$Port"
$env:CREW_ENGINE_URL = $EngineUrl

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Error "python not found on PATH"
    exit 1
}

Write-Host "Cortex Crew -> http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Engine bridge: $EngineUrl  (set CREW_ENGINE_URL to change)" -ForegroundColor Gray
Write-Host "Paste keys in the UI (Providers / API keys) or set ANTHROPIC_API_KEY / OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / CREW_MODEL" -ForegroundColor Gray
Write-Host "Computer control stays off until CORTEX_COMPUTER_CONTROL=1, then arm UACC in the panel" -ForegroundColor Gray

python -m CortexOS.crew --host 127.0.0.1 --port $Port
