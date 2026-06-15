# DMS Brain investor demo — one-command startup (Windows)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

Write-Host "==> Generating / cleaning warehouse sample..."
& "$Root\myenv\Scripts\python.exe" -m netie.dms.generate_sample
& "$Root\myenv\Scripts\python.exe" -m netie.dms.cleaner
& "$Root\myenv\Scripts\python.exe" -m netie.dms.warehouse_db

Write-Host "==> Starting API (PACK=dms) on :8000..."
$env:PACK = "dms"
Start-Process -FilePath "$Root\myenv\Scripts\python.exe" `
  -ArgumentList "-m", "uvicorn", "netie.api.main:app", "--host", "0.0.0.0", "--port", "8000" `
  -WorkingDirectory $Root

Write-Host "==> Starting Next.js UI on :3000..."
Set-Location "$Root\demo\dms-ui"
if (-not (Test-Path "node_modules")) {
  npm install
}
Start-Process -FilePath "npm" -ArgumentList "run", "dev" -WorkingDirectory "$Root\demo\dms-ui"

Write-Host ""
Write-Host "Demo running:"
Write-Host "  UI:  http://localhost:3000"
Write-Host "  API: http://localhost:8000/health"
