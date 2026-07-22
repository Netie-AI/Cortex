# Install repo git hooks (Windows-friendly).
# Usage:  .\scripts\install_git_hooks.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$hooks = Join-Path $root ".githooks"
if (-not (Test-Path $hooks)) { throw ".githooks missing" }
git config core.hooksPath .githooks
Write-Host "core.hooksPath -> .githooks (secrets_scan --staged on commit)"
