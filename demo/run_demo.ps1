# DMS Brain demo — one-command startup (Windows)
param(
    [switch]$Fast
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "demo\logs"
$ApiLogOut = Join-Path $LogDir "api.out.log"
$ApiLogErr = Join-Path $LogDir "api.err.log"
$UiLogOut = Join-Path $LogDir "ui.out.log"
$UiLogErr = Join-Path $LogDir "ui.err.log"
$PipExtras = '.[dev,api,dms]'

function Resolve-PythonExe {
    param([string]$RepoRoot)
    $myenvPy = Join-Path $RepoRoot "myenv\Scripts\python.exe"
    if (Test-Path $myenvPy) { return $myenvPy }
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    throw "No Python found. Install Python 3.10+ or create myenv."
}

function Test-PythonImports {
    param([string]$PythonExe, [string]$RepoRoot)
    $env:PYTHONPATH = $RepoRoot
    $env:PACK = "dms"
    $code = "import packs; from CortexOS.api.app import create_app; create_app(); import qrcode; from PIL import Image"
    & $PythonExe -c $code
    return ($LASTEXITCODE -eq 0)
}

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "WARN $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "ERROR $msg" -ForegroundColor Red }

function Stop-PortListener {
    param([int]$Port)
    $lines = netstat -ano | Select-String ":$Port\s"
    foreach ($line in $lines) {
        if ($line -match "\sLISTENING\s+(\d+)\s*$") {
            $procId = [int]$Matches[1]
            if ($procId -gt 0) {
                Write-Warn "Stopping PID $procId on port $Port"
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Wait-HttpOk {
    param([string]$Url, [int]$Seconds = 45)
    for ($i = 0; $i -lt $Seconds; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Seconds 1
    }
    return $false
}

Set-Location $Root
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:PYTHONPATH = $Root
$env:PACK = "dms"

$Python = Resolve-PythonExe -RepoRoot $Root

Write-Step "Installing package (editable, dms+api+dev)..."
& $Python -m pip install -e $PipExtras -q
if ($LASTEXITCODE -ne 0) {
    Write-Err "pip install failed"
    exit 1
}

if (-not (Test-PythonImports -PythonExe $Python -RepoRoot $Root)) {
    if ($Python -like "*myenv*") {
        Write-Warn "myenv missing DMS deps - falling back to system python"
        $Python = "python"
        & $Python -m pip install -e $PipExtras -q
        if (-not (Test-PythonImports -PythonExe $Python -RepoRoot $Root)) {
            Write-Err "Python still missing deps after pip install"
            exit 1
        }
    } else {
        Write-Err "Python missing DMS deps - run: pip install -e $PipExtras"
        exit 1
    }
}
Write-Ok "Python ready: $Python"

$DuckDb = Join-Path $Root "data\dms_demo.duckdb"
if ($Fast -and (Test-Path $DuckDb)) {
    Write-Step "Fast mode: skipping dataset regen (existing DuckDB)"
    & $Python -m CortexOS.dms.seed_demo
    if ($LASTEXITCODE -ne 0) { Write-Err "seed_demo failed"; exit 1 }
    Write-Ok "Warehouse locations verified"
} else {
    Write-Step "Generating / cleaning warehouse sample..."
    & $Python -m CortexOS.dms.generate_sample
    if ($LASTEXITCODE -ne 0) { Write-Err "generate_sample failed"; exit 1 }
    & $Python -m CortexOS.dms.cleaner
    if ($LASTEXITCODE -ne 0) { Write-Err "cleaner failed"; exit 1 }
    & $Python -m CortexOS.dms.warehouse_db
    if ($LASTEXITCODE -ne 0) { Write-Err "warehouse_db failed"; exit 1 }
    & $Python -m CortexOS.dms.seed_demo
    if ($LASTEXITCODE -ne 0) {
        Write-Err "seed_demo failed - warehouse bins missing"
        exit 1
    }
    Write-Ok "DuckDB + warehouse locations seeded"
}

Stop-PortListener -Port 8000
Stop-PortListener -Port 3000

Write-Step "Starting API (PACK=dms) on :8000..."
foreach ($f in @($ApiLogOut, $ApiLogErr)) { if (Test-Path $f) { Remove-Item $f -Force } }
$apiProc = Start-Process -FilePath $Python -PassThru `
    -ArgumentList @("-m", "uvicorn", "CortexOS.api.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $ApiLogOut `
    -RedirectStandardError $ApiLogErr `
    -WindowStyle Hidden

if (-not (Wait-HttpOk -Url "http://127.0.0.1:8000/health" -Seconds 45)) {
    Write-Err "API did not respond on :8000 within 45s"
    Write-Host "---- api.err.log (last 40 lines) ----"
    if (Test-Path $ApiLogErr) { Get-Content $ApiLogErr -Tail 40 }
    exit 1
}
Write-Ok "API ready: http://localhost:8000/health (PID $($apiProc.Id))"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Err "npm not found - install Node.js 18+"
    exit 1
}

$npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npmCmd) { $npmCmd = "npm.cmd" }

Write-Step "Starting Next.js UI on :3000..."
Set-Location "$Root\demo\dms-ui"
# App Router project — Next dev still scans pages/; missing dir causes 500
$pagesDir = Join-Path (Get-Location) "pages"
if (-not (Test-Path $pagesDir)) {
    New-Item -ItemType Directory -Force -Path $pagesDir | Out-Null
    New-Item -ItemType File -Force -Path (Join-Path $pagesDir ".gitkeep") | Out-Null
}
if (-not (Test-Path "node_modules")) {
    npm install
    if ($LASTEXITCODE -ne 0) { Write-Err "npm install failed"; exit 1 }
}
foreach ($f in @($UiLogOut, $UiLogErr)) { if (Test-Path $f) { Remove-Item $f -Force } }
$uiProc = Start-Process -FilePath $npmCmd -PassThru `
    -ArgumentList @("run", "dev") `
    -WorkingDirectory "$Root\demo\dms-ui" `
    -RedirectStandardOutput $UiLogOut `
    -RedirectStandardError $UiLogErr `
    -WindowStyle Hidden

if (-not (Wait-HttpOk -Url "http://127.0.0.1:3000/" -Seconds 60)) {
    Write-Err "UI did not respond on :3000 within 60s"
    Write-Host "---- ui.err.log (last 40 lines) ----"
    if (Test-Path $UiLogErr) { Get-Content $UiLogErr -Tail 40 }
    exit 1
}
Write-Ok "UI ready: http://localhost:3000 (PID $($uiProc.Id))"

Write-Host ""
Write-Host "Demo running:" -ForegroundColor Green
Write-Host "  UI:        http://localhost:3000"
Write-Host "  Warehouse: http://localhost:3000/warehouse"
Write-Host "  Chat:      http://localhost:3000/chat"
Write-Host "  API:       http://localhost:8000/health"
Write-Host "  Show guide: docs/DEMO.md"
Write-Host "  Logs:      demo/logs/api.*.log, demo/logs/ui.*.log"
Write-Host ""
Write-Host "Diagnostics: scripts/diagnose_demo.ps1"
