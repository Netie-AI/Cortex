#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Start the Cortex engine API (required before AirGPT Hosting/Cortex features).

.USAGE
  .\scripts\start_cortex_engine.ps1
  .\scripts\start_cortex_engine.ps1 -Port 8010
  .\scripts\start_cortex_engine.ps1 -Port 8010 -DryRun
#>
param(
    [int]$Port = 8010,
    [string]$Pack = "dms",
    [switch]$DryRun,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$DataDir = Join-Path $Root "data\engine"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$PidFile = Join-Path $DataDir "engine.pid"
$UrlHint = Join-Path $DataDir "cortex_api_url.txt"
$LogFile = Join-Path $DataDir "engine.log"
$ApiUrl = "http://127.0.0.1:$Port"

function Test-PortInUse {
    param([int]$P)
    try {
        $c = Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue
        return $null -ne $c
    } catch {
        return $false
    }
}

function Test-EngineHealthy {
    param([string]$Url, [int]$Attempts = 3)
    # Retries matter beyond tests: a falsely-failed probe under load would let
    # autostart try to double-bind the port.
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

if ((Test-EngineHealthy -Url $ApiUrl) -and -not $Force) {
    Set-Content -Path $UrlHint -Value $ApiUrl -Encoding utf8
    if ($DryRun) {
        Write-Host "[DryRun] Cortex engine already healthy at $ApiUrl" -ForegroundColor Yellow
    } else {
        Write-Host "Cortex engine already healthy at $ApiUrl" -ForegroundColor Green
    }
    exit 0
}

if ((Test-PortInUse -P $Port) -and -not $Force) {
    if (Test-EngineHealthy -Url $ApiUrl) {
        Set-Content -Path $UrlHint -Value $ApiUrl -Encoding utf8
        if ($DryRun) {
            Write-Host "[DryRun] Cortex engine already healthy at $ApiUrl" -ForegroundColor Yellow
        } else {
            Write-Host "Cortex engine already healthy at $ApiUrl" -ForegroundColor Green
        }
        exit 0
    }
    Write-Error "Port $Port is in use but /health is not Cortex. Pass -Force to override or choose another -Port."
    exit 1
}

if (Test-Path $PidFile) {
    $oldPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($oldPid -match '^\d+$') {
        $proc = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
        if ($proc -and -not $Force) {
            if (Test-EngineHealthy -Url $ApiUrl) {
                Set-Content -Path $UrlHint -Value $ApiUrl -Encoding utf8
                if ($DryRun) {
                    Write-Host "[DryRun] Cortex engine already running (pid $oldPid) at $ApiUrl" -ForegroundColor Yellow
                } else {
                    Write-Host "Cortex engine already running (pid $oldPid) at $ApiUrl" -ForegroundColor Green
                }
                exit 0
            }
        }
    }
}

$env:PACK = $Pack
if (-not $env:DMS_AUTH_DISABLED) {
    if (-not $env:DMS_API_KEYS) {
        $env:DMS_API_KEYS = "viewer:dms-demo-viewer-key;steward:dms-demo-steward-key;admin:dms-demo-admin-key"
    }
}

Set-Content -Path $UrlHint -Value $ApiUrl -Encoding utf8

Write-Host "Cortex engine → $ApiUrl  (PACK=$Pack)" -ForegroundColor Cyan
Write-Host "AirGPT should set CORTEX_API_URL=$ApiUrl" -ForegroundColor Gray
Write-Host "Hint file: $UrlHint" -ForegroundColor Gray
Write-Host "Health: GET /health · Engine: GET /api/engine/specs · Sidecar: POST /dms/secure" -ForegroundColor Gray

if ($DryRun) {
    Write-Host "[DryRun] Would start: python -m uvicorn CortexOS.api.main:app --host 127.0.0.1 --port $Port" -ForegroundColor Yellow
    Write-Host "[DryRun] PidFile=$PidFile LogFile=$LogFile" -ForegroundColor Yellow
    exit 0
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Error "python not found on PATH"
    exit 1
}

# Record this shell's PID; uvicorn inherits the session.
Set-Content -Path $PidFile -Value $PID -Encoding utf8
try {
    python -m uvicorn CortexOS.api.main:app --host 127.0.0.1 --port $Port 2>&1 |
        Tee-Object -FilePath $LogFile
} finally {
    if (Test-Path $PidFile) {
        $cur = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($cur -eq "$PID") {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        }
    }
}
