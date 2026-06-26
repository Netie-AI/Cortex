#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Stage, commit, tag, and push everything to GitHub.
    Safe to run from any machine — works on dms-v2 branch.

.USAGE
    # Normal push:
    .\scripts\git_push_all.ps1 -Message "Phase 2+3+F4: Postgres ledger, security, brain, ponytail"

    # With a version tag:
    .\scripts\git_push_all.ps1 -Message "F4 task suggest + Ponytail + Brain" -Tag "v0.4.0"

    # Dry run (show what would be committed):
    .\scripts\git_push_all.ps1 -DryRun
#>

param(
    [string]$Message = "Update: $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
    [string]$Tag = "",
    [switch]$DryRun = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Verify we're in the repo root ────────────────────────────────────────────
if (-not (Test-Path ".git")) {
    Write-Error "Run this from the repo root (where .git lives)."
    exit 1
}

# ── Current branch ────────────────────────────────────────────────────────────
$branch = git branch --show-current
Write-Host "Branch: $branch" -ForegroundColor Cyan

# ── Check for changes ─────────────────────────────────────────────────────────
$status = git status --short
if (-not $status) {
    Write-Host "Nothing to commit. Already up to date." -ForegroundColor Green
    exit 0
}

# ── Show what will be committed ───────────────────────────────────────────────
Write-Host "`nFiles to stage:" -ForegroundColor Yellow
git status --short

if ($DryRun) {
    Write-Host "`n[DryRun] No changes made." -ForegroundColor Magenta
    exit 0
}

# ── Stage all (respects .gitignore) ──────────────────────────────────────────
git add -A

# ── Commit ────────────────────────────────────────────────────────────────────
Write-Host "`nCommitting: $Message" -ForegroundColor Cyan
git commit -m $Message

# ── Optional tag ─────────────────────────────────────────────────────────────
if ($Tag) {
    Write-Host "Tagging: $Tag" -ForegroundColor Cyan
    git tag -a $Tag -m "Release $Tag"
    git push origin $Tag
    Write-Host "Tag pushed: $Tag" -ForegroundColor Green
}

# ── Push ──────────────────────────────────────────────────────────────────────
Write-Host "Pushing to origin/$branch…" -ForegroundColor Cyan
git push origin $branch

Write-Host "`n✓ Pushed to https://github.com/Netie-AI/Cortex/tree/$branch" -ForegroundColor Green
Write-Host "  Actions: https://github.com/Netie-AI/Cortex/actions" -ForegroundColor Gray
