# Cortex Crew night companion -- launch only.
# Restarts Crew and OpenVault API if they died. Does not implement tickets.
# Does not touch D:\Cortex (ANS) or kind-euclid. Does not kill processes.
param([switch]$SkipEstate)
$ErrorActionPreference = "Continue"
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
$Wake = "D:\Cortex-crew\docs\NIGHT_WAKE.md"
$CrewPy = "D:\Cortex\.venv\Scripts\python.exe"
$CrewRoot = "D:\Cortex-crew"
$OvHome = "D:\OpenVault\.openvault"
$notes = @()

function HttpOk([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 12
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
    } catch { return $false }
}

$CrewRepos = "Netie-AI/Cortex,Netie-AI/OpenVault,Netie-AI/Pointer,Netie-AI/Space,Netie-AI/dms,jian-hong/AirGPT"
if (-not (HttpOk "http://127.0.0.1:8020/crew/health")) {
    $env:CREW_DATA_DIR = "$CrewRoot\data\crew"
    Remove-Item Env:CORTEX_COMPUTER_CONTROL -ErrorAction SilentlyContinue
    $env:CREW_ALLOW_OLLAMA = "0"
    $env:CREW_CURSOR_MODEL = "grok-4.6"
    $env:CREW_GH_REPOS = $CrewRepos
    $env:PYTHONPATH = $CrewRoot
    Start-Process -FilePath $CrewPy -ArgumentList @(
        "-m", "uvicorn", "CortexOS.crew.server:create_app", "--factory",
        "--host", "127.0.0.1", "--port", "8020"
    ) -WorkingDirectory $CrewRoot -WindowStyle Minimized
    $notes += "started Crew :8020"
} else {
    $notes += "Crew up"
}

if (-not (HttpOk "http://127.0.0.1:8010/api/engine/activity")) {
    $notes += "ENGINE DOWN (will not start from this script; ANS checkout owns :8010)"
} else {
    $notes += "engine up"
}

if (-not (HttpOk "http://127.0.0.1:5000/api/healthz")) {
    $env:OPENVAULT_HOME = $OvHome
    $env:CORTEX_URL = "http://127.0.0.1:8010"
    $uv = "$env:USERPROFILE\.local\bin\uv.exe"
    if (-not (Test-Path $uv)) { $uv = "uv" }
    New-Item -ItemType Directory -Force -Path "$OvHome\logs" | Out-Null
    Start-Process -FilePath $uv -ArgumentList @(
        "run", "--no-sync", "openmw", "console",
        "--host", "127.0.0.1", "--port", "5000",
        "--cortex-url", "http://127.0.0.1:8010",
        "--openide-url", "http://127.0.0.1:8765",
        "--no-open-browser"
    ) -WorkingDirectory "D:\OpenVault\OpenMW" -WindowStyle Minimized
    $notes += "started OpenVault API :5000"
} else {
    $notes += "OpenVault up"
}

if (-not $SkipEstate -and (Test-Path "D:\Netie\Internal\Agents\estate-watchdog.ps1")) {
    $wd = Start-Process -FilePath "powershell" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "D:\Netie\Internal\Agents\estate-watchdog.ps1"
    ) -WindowStyle Minimized -PassThru
    if ($wd.WaitForExit(45000)) {
        $notes += "estate-watchdog ran"
    } else {
        $notes += "estate-watchdog still running (docker/gh)"
    }
}

if ($SkipEstate) {
    $line = "- $Stamp " + ($notes -join "; ") + " (via estate)"
} elseif (Get-Command gh -ErrorAction SilentlyContinue) {
    try {
        $bits = @()
        $total = 0
        foreach ($repo in $CrewRepos.Split(",")) {
            $prJson = gh pr list --repo $repo --limit 12 --json number,title,isDraft,reviewDecision 2>$null
            if ($LASTEXITCODE -eq 0 -and $prJson) {
                $rows = @($prJson | ConvertFrom-Json)
                $total += $rows.Count
                $needVerify = @($rows | Where-Object { -not $_.isDraft -and $_.reviewDecision -ne "APPROVED" }).Count
                if ($needVerify -gt 0) {
                    $notes += ($repo.Split("/")[-1] + " need-verify=$needVerify")
                }
                $bits += ($rows | Select-Object -First 3 | ForEach-Object { $repo.Split("/")[-1] + "#" + $_.number })
            }
        }
        $notes += "PRs open=$total"
        $line = "- $Stamp " + ($notes -join "; ") + " :: " + (($bits | Select-Object -First 12) -join " ")
    } catch {
        $notes += "PRs gh-error"
        $line = "- $Stamp " + ($notes -join "; ")
    }
} else {
    $line = "- $Stamp " + ($notes -join "; ")
}
if (-not (Test-Path $Wake)) {
    Set-Content -Path $Wake -Value "# Night wake log`n`nCrew http://127.0.0.1:8020/  Plane http://localhost:8099/netie/`n`n" -Encoding utf8
}
Add-Content -Path $Wake -Value $line -Encoding utf8
Write-Host $line
