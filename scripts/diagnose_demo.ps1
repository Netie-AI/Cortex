# Full local demo diagnostics — run from repo root
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = if (Test-Path "$Root\myenv\Scripts\python.exe") {
    "$Root\myenv\Scripts\python.exe"
} else {
    "python"
}

Write-Host "=== Cortex DMS Demo Diagnostics ===" -ForegroundColor Cyan
Write-Host "Repo: $Root"
Write-Host ""

function Test-Port([int]$Port) {
    $hit = netstat -ano | Select-String ":$Port\s.*LISTENING"
    if ($hit) { return "LISTENING`n$hit" }
    return "FREE"
}

Write-Host "[1] Ports"
Write-Host "  :8000 -> $(Test-Port 8000)"
Write-Host "  :3000 -> $(Test-Port 3000)"
Write-Host ""

Write-Host "[2] Python"
Write-Host "  exe: $Python"
& $Python --version 2>&1
Write-Host "  PYTHONPATH: $($env:PYTHONPATH)"
Write-Host "  PACK: $($env:PACK)"
Write-Host ""

Write-Host "[3] Package imports"
$env:PYTHONPATH = $Root
$env:PACK = "dms"
& $Python -c @"
import sys
print('  sys.path[0]:', sys.path[0])
try:
    import packs
    print('  packs:', packs.__file__)
except Exception as e:
    print('  packs FAIL:', e)
try:
    import CortexOS
    print('  CortexOS:', CortexOS.__file__)
except Exception as e:
    print('  CortexOS FAIL:', e)
try:
    from CortexOS.api.app import create_app
    app = create_app()
    print('  FastAPI routes:', len(app.routes))
except Exception as e:
    print('  create_app FAIL:', e)
try:
    from CortexOS.dms.seed_demo import seed_demo_warehouse
    seed_demo_warehouse()
    print('  seed_demo: ok')
except Exception as e:
    print('  seed_demo FAIL:', e)
"@

Write-Host ""
Write-Host "[4] HTTP probes"
foreach ($url in @(
    "http://127.0.0.1:8000/health",
    "http://127.0.0.1:8000/dms/warehouse/locations/tree",
    "http://127.0.0.1:3000/",
    "http://127.0.0.1:3000/warehouse"
)) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        Write-Host "  $url -> $($r.StatusCode)" -ForegroundColor Green
    } catch {
        Write-Host "  $url -> FAIL ($($_.Exception.Message))" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "[5] Node/npm"
if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "  node: $(node --version)"
} else {
    Write-Host "  node: NOT FOUND" -ForegroundColor Red
}
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host "  npm: $(npm --version)"
} else {
    Write-Host "  npm: NOT FOUND" -ForegroundColor Red
}

Write-Host ""
Write-Host "[6] Data files"
$duck = Join-Path $Root "data\dms_demo.duckdb"
$ops = Join-Path $Root "data\dms_ops.db"
Write-Host "  DuckDB: $(if (Test-Path $duck) { 'exists' } else { 'MISSING' })"
Write-Host "  Ops DB: $(if (Test-Path $ops) { 'exists' } else { 'MISSING (created on first warehouse use)' })"

Write-Host ""
Write-Host "[7] Recent logs"
foreach ($log in @("demo\logs\api.log", "demo\logs\ui.log")) {
    $path = Join-Path $Root $log
    if (Test-Path $path) {
        Write-Host "  --- $log (last 15 lines) ---"
        Get-Content $path -Tail 15
    }
}

Write-Host ""
Write-Host "Fix: .\demo\run_demo.ps1" -ForegroundColor Yellow
