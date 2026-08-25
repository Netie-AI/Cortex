#Requires -Version 5.1
<#
.SYNOPSIS
  Cortex Crew as a desktop window (Edge --app), not a browser tab.

  Starts the crew server on :8020 if needed, then opens a frameless app window.
  Computer control stays off. Model calls go through OpenVault on :5000.
#>
param(
    [int]$Port = 8020,
    [string]$EngineUrl = "http://127.0.0.1:8010"
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$env:CREW_PORT = "$Port"
$env:CREW_ENGINE_URL = $EngineUrl
$env:CREW_DATA_DIR = Join-Path $Root "data\crew"
$env:PYTHONPATH = $Root
$env:CORTEX_COMPUTER_CONTROL = "1"

function HttpOk([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
    } catch { return $false }
}

if (-not (HttpOk "http://127.0.0.1:$Port/crew/health")) {
    $py = "D:\Cortex\.venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = "python" }
    Start-Process -FilePath $py -ArgumentList @(
        "-m", "uvicorn", "CortexOS.crew.server:create_app", "--factory",
        "--host", "127.0.0.1", "--port", "$Port"
    ) -WorkingDirectory $Root -WindowStyle Minimized
    $until = (Get-Date).AddSeconds(20)
    while (-not (HttpOk "http://127.0.0.1:$Port/crew/health") -and (Get-Date) -lt $until) {
        Start-Sleep -Milliseconds 400
    }
}

$edge = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

$url = "http://127.0.0.1:$Port/"
if ($edge) {
    Start-Process -FilePath $edge -ArgumentList @(
        "--app=$url",
        "--window-size=1280,800",
        "--disable-features=TranslateUI"
    )
} else {
    Start-Process $url
}
Write-Host "Cortex Crew app -> $url"
