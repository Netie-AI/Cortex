#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Start the Cortex engine API (required before AirGPT Hosting/Cortex features).

.USAGE
  .\scripts\start_cortex_engine.ps1
  .\scripts\start_cortex_engine.ps1 -Port 8000
#>
param(
    [int]$Port = 8000,
    [string]$Pack = "dms"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$env:PACK = $Pack
if (-not $env:DMS_AUTH_DISABLED) {
    # Demo-friendly defaults if not already set
    if (-not $env:DMS_API_KEYS) {
        $env:DMS_API_KEYS = "viewer:dms-demo-viewer-key;steward:dms-demo-steward-key;admin:dms-demo-admin-key"
    }
}

Write-Host "Cortex engine → http://127.0.0.1:$Port  (PACK=$Pack)" -ForegroundColor Cyan
Write-Host "AirGPT should set CORTEX_API_URL=http://127.0.0.1:$Port" -ForegroundColor Gray
Write-Host "Health: GET /health · Engine: GET /api/engine/specs · Sidecar: POST /dms/secure" -ForegroundColor Gray

python -m uvicorn CortexOS.api.main:app --host 127.0.0.1 --port $Port
