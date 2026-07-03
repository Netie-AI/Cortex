# Portable Cortex DMS demo — works from any drive letter (SSD)
param([switch]$Fast)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$marker = Join-Path $Root ".portable-setup-done"
if (-not (Test-Path $marker)) {
    Write-Host "First run on this machine — running one-time setup..." -ForegroundColor Yellow
    & (Join-Path $Root "SETUP_ONCE.ps1")
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

$duck = Join-Path $Root "data\dms_demo.duckdb"
$demoScript = Join-Path $Root "demo\run_demo.ps1"
if (-not (Test-Path $demoScript)) {
    Write-Host "ERROR: demo\run_demo.ps1 not found. Wait for copy to finish or re-copy the repo to this folder." -ForegroundColor Red
    exit 1
}

$useFast = $Fast -or (Test-Path $duck)
if ($useFast) {
    Write-Host "Starting demo (fast restart)..." -ForegroundColor Cyan
    & $demoScript -Fast
} else {
    Write-Host "Starting demo (first build — generates sample data, ~2-5 min)..." -ForegroundColor Cyan
    & $demoScript
}
exit $LASTEXITCODE
