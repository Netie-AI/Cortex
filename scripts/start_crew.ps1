#!/usr/bin/env pwsh
param(
    [int]$Port = 8020,
    [string]$EngineUrl = "http://127.0.0.1:8011"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$env:PYTHONPATH = $Root
$env:CREW_PORT = "$Port"
$env:CREW_ENGINE_URL = $EngineUrl
$env:CORTEX_COMPUTER_CONTROL = "1"

function HttpOk([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
    } catch { return $false }
}

function PortHeld([int]$P) {
    $c = $null
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", $P, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(400, $false)
        if (-not $ok) { return $false }
        $c.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        if ($c) { $c.Close() }
    }
}

if (PortHeld $Port) {
    if (HttpOk "http://127.0.0.1:$Port/crew/health") {
        Write-Host "Crew already healthy at http://127.0.0.1:$Port - not starting a second process."
        exit 0
    }
    Write-Host "Port $Port is held but /crew/health unread."
    Write-Host "Stop that hung process yourself (R-0015 / Control YOU step 8)."
    Write-Host "Then re-run this script from E:\Cortex. Do not start a second Crew."
    exit 1
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Error "python not found on PATH"
    exit 1
}

Write-Host "Cortex Crew -> http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Engine bridge: $EngineUrl  (set CREW_ENGINE_URL to change)" -ForegroundColor Gray
Write-Host "Paste keys in the UI (Providers / API keys) or set ANTHROPIC_API_KEY / OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / CREW_MODEL" -ForegroundColor Gray
Write-Host "Computer control ON (CORTEX_COMPUTER_CONTROL=1). Arm UACC/windows-mcp in the panel. Mutating tools still Confirm." -ForegroundColor Gray

python -m CortexOS.crew --host 127.0.0.1 --port $Port
