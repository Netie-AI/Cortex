# First-time setup for portable Cortex DMS (run once per machine)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m) { Write-Host "OK  $m" -ForegroundColor Green }
function Write-Err($m) { Write-Host "ERR $m" -ForegroundColor Red }

Write-Step "Checking prerequisites..."

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Err "Python 3.10+ not found. Install from https://www.python.org/downloads/ (check Add to PATH)"
    exit 1
}
$pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Ok "Python $pyVer"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Err "Node.js/npm not found. Install LTS from https://nodejs.org/"
    exit 1
}
Write-Ok "npm $(npm --version)"

$env:PYTHONPATH = $Root
$env:PACK = "dms"
$env:DMS_API_KEYS = "viewer:dms-demo-viewer-key;steward:dms-demo-steward-key;admin:dms-demo-admin-key"

Write-Step "Installing Python package (editable, dms+api+dev)..."
python -m pip install --upgrade pip -q
python -m pip install -e ".[dev,api,dms]" -q
if ($LASTEXITCODE -ne 0) { Write-Err "pip install failed"; exit 1 }
Write-Ok "Python deps installed"

Write-Step "Installing UI dependencies..."
$ui = Join-Path $Root "demo\dms-ui"
if (-not (Test-Path $ui)) {
    Write-Err "demo\dms-ui missing — copy may be incomplete. Re-run copy from source."
    exit 1
}
Set-Location $ui
$pagesDir = Join-Path $ui "pages"
if (-not (Test-Path $pagesDir)) {
    New-Item -ItemType Directory -Force -Path $pagesDir | Out-Null
    New-Item -ItemType File -Force -Path (Join-Path $pagesDir ".gitkeep") | Out-Null
}
npm install
if ($LASTEXITCODE -ne 0) { Write-Err "npm install failed"; exit 1 }
Write-Ok "UI deps installed"

Set-Location $Root
New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "demo\logs") | Out-Null

Set-Content -Path (Join-Path $Root ".portable-setup-done") -Value (Get-Date -Format o)
Write-Ok "Setup complete. Double-click RUN_DEMO.bat anytime."
