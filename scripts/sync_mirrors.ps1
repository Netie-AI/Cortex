#!/usr/bin/env pwsh
<#
.SYNOPSIS
  After a push from RUMA/Cortex, refresh local mirrors:
    - F:\Cortex          → git fetch + fast-forward current branch
    - F:\AirGPT\CortexOS → robocopy package surface (API client still talks HTTP)

.USAGE
  .\scripts\sync_mirrors.ps1
  .\scripts\sync_mirrors.ps1 -Branch netie-engine-up
#>
param(
    [string]$Branch = "",
    [string]$CortexMirror = "F:\Cortex",
    [string]$AirGptCortexOs = "F:\AirGPT\CortexOS"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Branch) {
    $Branch = (git -C $Root branch --show-current).Trim()
}

Write-Host "Sync mirrors from $Root ($Branch)" -ForegroundColor Cyan

# ── F:\Cortex git mirror ─────────────────────────────────────────────────────
if (Test-Path (Join-Path $CortexMirror ".git")) {
    Write-Host "`n→ $CortexMirror" -ForegroundColor Yellow
    git -C $CortexMirror fetch origin --prune 2>&1 | Out-Host
    $cur = (git -C $CortexMirror branch --show-current).Trim()
    if ($cur -ne $Branch) {
        git -C $CortexMirror checkout $Branch 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) {
            git -C $CortexMirror checkout -B $Branch "origin/$Branch" 2>&1 | Out-Host
        }
    }
    git -C $CortexMirror pull --ff-only origin $Branch 2>&1 | Out-Host
    Write-Host "  HEAD: $(git -C $CortexMirror rev-parse --short HEAD)" -ForegroundColor Green
} else {
    Write-Host "SKIP: $CortexMirror (no .git)" -ForegroundColor DarkYellow
}

# ── F:\AirGPT\CortexOS package mirror (thin; runtime = API to F:\Cortex) ─────
$Src = Join-Path $Root "CortexOS"
if ((Test-Path $Src) -and (Test-Path (Split-Path $AirGptCortexOs -Parent))) {
    Write-Host "`n→ $AirGptCortexOs (package robocopy)" -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $AirGptCortexOs | Out-Null
    $dirs = @("api", "engine", "memory", "routing", "execution", "ponytail", "dms", "compliance", "db", "nlp", "rag", "security", "packs", "personality", "skills", "cas", "crypto", "fabrication", "a2a")
    foreach ($d in $dirs) {
        $from = Join-Path $Src $d
        if (Test-Path $from) {
            $to = Join-Path $AirGptCortexOs $d
            robocopy $from $to /MIR /NFL /NDL /NJH /NJS /NP /XD __pycache__ .pytest_cache /XF *.pyc 2>&1 | Out-Null
        }
    }
    foreach ($f in @("__init__.py", "cli.py", "config.py", "result.py")) {
        $from = Join-Path $Src $f
        if (Test-Path $from) {
            Copy-Item -Force $from (Join-Path $AirGptCortexOs $f)
        }
    }
    # Marker: this tree is a mirror — prefer HTTP API to the running F:\Cortex engine
    @"
# CortexOS mirror (auto-synced from RUMA/Cortex)
# Do not run this copy as the engine. Start F:\Cortex (or RUMA/Cortex) via:
#   .\scripts\start_cortex_engine.ps1
# AirGPT connects with CORTEX_API_URL=http://127.0.0.1:8000
"@ | Set-Content -Encoding utf8 (Join-Path $AirGptCortexOs "MIRROR_README.txt")
    Write-Host "  Package mirror updated" -ForegroundColor Green
} else {
    Write-Host "SKIP: AirGPT CortexOS mirror path missing" -ForegroundColor DarkYellow
}

Write-Host "`n✓ Sync done" -ForegroundColor Green
