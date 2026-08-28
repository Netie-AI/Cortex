# Build standalone cortex-contract wheel (P-DMS-25).
# ASCII-only for Windows PowerShell 5.1.
param(
  [string]$CortexRoot = "D:\Cortex",
  [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$pkg = Join-Path $CortexRoot "packages\cortex_contract"
if (-not (Test-Path $pkg)) { throw "missing $pkg" }

Push-Location $CortexRoot
try {
  python scripts\check_versions.py
} catch {
  Write-Host "check_versions skipped/failed: $_" -ForegroundColor Yellow
}
Pop-Location

Push-Location $pkg
try {
  python -m pip install --upgrade build wheel -q
  python -m build --wheel
  $wheel = Get-ChildItem dist\cortex_contract-*.whl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $wheel) { throw "wheel not produced" }
  Write-Host "Built $($wheel.FullName)"
  if ($OutDir) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    Copy-Item $wheel.FullName $OutDir -Force
    Write-Host "Copied to $OutDir"
  }
} finally {
  Pop-Location
}
