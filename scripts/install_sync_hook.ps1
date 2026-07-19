#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Install a post-push git hook that runs sync_mirrors.ps1 after every push.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Hook = Join-Path $Root ".git\hooks\post-push"

@'
#!/bin/sh
# Auto-sync F:\Cortex + F:\AirGPT\CortexOS after push (Netie Engine Up)
ROOT="$(git rev-parse --show-toplevel)"
if [ -f "$ROOT/scripts/sync_mirrors.ps1" ]; then
  pwsh -NoProfile -File "$ROOT/scripts/sync_mirrors.ps1" || true
fi
'@ | Set-Content -Encoding ascii $Hook

Write-Host "Installed $Hook" -ForegroundColor Green
Write-Host "Manual sync: .\scripts\sync_mirrors.ps1" -ForegroundColor Gray
