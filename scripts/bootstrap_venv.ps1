#Requires -Version 5.1
<#
.SYNOPSIS
  Bootstrap a working Cortex Python venv on Windows (no broken myenv/.venv_gpu).

  Uses uv + Python 3.12. Installs core runtime deps with binary wheels only so
  litellm does not require MSVC link.exe.

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\bootstrap_venv.ps1
  .\.venv\Scripts\python.exe -m pytest tests\test_openvault_client.py tests\test_workflow_runner.py -q
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required. Install from https://docs.astral.sh/uv/"
}

$py = $null
foreach ($candidate in @("3.12", "3.11", "3.10")) {
    try {
        $null = & py -$candidate -c "import sys; print(sys.version)" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
    } catch { }
}
if (-not $py) {
    Write-Error "Python 3.10+ required (py -3.12 not found)."
}

Write-Host "Creating .venv with Python $py ..."
if (Test-Path (Join-Path $Root ".venv")) {
    uv venv --python $py --clear .venv
} else {
    uv venv --python $py .venv
}

$pip = Join-Path $Root ".venv\Scripts\python.exe"
& uv pip install --python $pip pytest pytest-asyncio pydantic rich typer pyyaml toml cryptography diskcache sqlite-utils rank-bm25 numpy wasmtime

# Binary-only litellm avoids Rust/MSVC build on Windows.
& uv pip install --python $pip "litellm==1.57.1" --only-binary=:all:

& uv pip install --python $pip --no-deps -e .

Write-Host ''
Write-Host 'OK - Cortex venv ready at .venv'
Write-Host 'Run: .\.venv\Scripts\python.exe -m pytest tests\test_openvault_client.py -q'
