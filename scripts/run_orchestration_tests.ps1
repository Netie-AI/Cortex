#Requires -Version 5.1
<#
.SYNOPSIS
  Run OpenVault orchestration tests (bootstrap venv if missing).

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts\run_orchestration_tests.ps1
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host '.venv missing - running bootstrap_venv.ps1 ...'
    & (Join-Path $Root 'scripts\bootstrap_venv.ps1')
}

& $py -m pytest tests\test_openvault_client.py tests\test_openvault_gate.py tests\test_workflow_runner.py -q
exit $LASTEXITCODE
