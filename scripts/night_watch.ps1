# Cortex Crew night companion -- launch only.
# Restarts engine Crew :8023 and OpenVault API if they died. Does not implement tickets.
# Hung converse :8020 is founder YOU. Does not kill processes (R-0015).
# Does not grow D:\Cortex-crew.
param([switch]$SkipEstate)
$ErrorActionPreference = "Continue"
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
$CrewRoot = if (Test-Path "D:\Cortex\CortexOS\crew") { "D:\Cortex" } elseif (Test-Path "E:\Cortex\CortexOS\crew") { "E:\Cortex" } else { "D:\Cortex" }
$CrewPy = Join-Path $CrewRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $CrewPy)) { $CrewPy = "python" }
$Wake = Join-Path $CrewRoot "data\crew\NIGHT_WAKE.md"
$OvHome = if (Test-Path "D:\OpenVault\.openvault") { "D:\OpenVault\.openvault" } elseif (Test-Path "E:\OpenVault\.openvault") { "E:\OpenVault\.openvault" } else { "D:\OpenVault\.openvault" }
$notes = @()

function HttpOk([string]$Url, [int]$Sec = 12) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $Sec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
    } catch { return $false }
}

function PortHeld([int]$Port) {
    $c = $null
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(400, $false)
        if (-not $ok) { return $false }
        $c.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        if ($c) { $c.Close() }
    }
}

$CrewRepos = "Netie-AI/Cortex,Netie-AI/OpenVault,Netie-AI/Pointer,Netie-AI/Space,Netie-AI/dms,jian-hong/AirGPT"
if (-not (HttpOk "http://127.0.0.1:8020/crew/health" 2)) {
    if (PortHeld 8020) {
        $notes += "Crew :8020 held but /crew/health unread. Will not start a second process. Founder rebind: YOU step 8."
    } else {
        $legacyData = if (Test-Path "E:\Cortex-crew\data\crew") { "E:\Cortex-crew\data\crew" } elseif (Test-Path "D:\Cortex-crew\data\crew") { "D:\Cortex-crew\data\crew" } else { "" }
        $env:CREW_DATA_DIR = if ($legacyData) { $legacyData } else { Join-Path $CrewRoot "data\crew" }
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
    }
} else {
    $notes += "Crew converse :8020 health ok"
}

if (-not (HttpOk "http://127.0.0.1:8023/crew/health" 3)) {
    if (PortHeld 8023) {
        $notes += "Crew sidecar :8023 held but /crew/health unread. Will not start a second process."
    } else {
        $env:PYTHONPATH = $CrewRoot
        Remove-Item Env:CORTEX_COMPUTER_CONTROL -ErrorAction SilentlyContinue
        $env:CREW_ALLOW_OLLAMA = "0"
        Start-Process -FilePath $CrewPy -ArgumentList @(
            "-m", "CortexOS.crew",
            "--host", "127.0.0.1",
            "--port", "8023"
        ) -WorkingDirectory $CrewRoot -WindowStyle Minimized
        $notes += "started Crew sidecar :8023"
    }
} else {
    $notes += "Crew sidecar :8023 up"
}

if (-not ((HttpOk "http://127.0.0.1:8011/health" 3) -or (HttpOk "http://127.0.0.1:8010/health" 3))) {
    $notes += "ENGINE DOWN (will not start from this script; ANS checkout owns the engine)"
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
