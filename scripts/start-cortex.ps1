# Start Cortex with repo-anchored cwd so data/* never resolves off a dead drive.
#
# Two failure modes this guards against, both seen in the wild:
#   1. Wrong cwd  -> Path("data/workflows") hits WinError 433 on Windows.
#   2. Stale PID  -> an older uvicorn still owns :8000 and keeps serving the
#      pre-fix bytecode, so editing the file changes nothing until it dies.
# The port is reclaimed before bind, so "restart" always means new code.
$ErrorActionPreference = "Stop"
$Root = "D:\Cortex"
$env:PACK = if ($env:PACK) { $env:PACK } else { "dms" }
$env:PYTHONPATH = $Root
Set-Location $Root
$port = if ($args[0]) { $args[0] } else { "8000" }

# Reclaim the port. Whoever owns it is by definition running older code than
# what is on disk right now, which is the only thing we are about to start.
$owners = @()
try {
  $owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
              Select-Object -ExpandProperty OwningProcess -Unique)
} catch {
  # Get-NetTCPConnection is absent on some SKUs - fall back to netstat parsing.
  $owners = @(netstat -ano | Select-String ":$port\s+.*LISTENING\s+(\d+)" |
              ForEach-Object { $_.Matches[0].Groups[1].Value } | Select-Object -Unique)
}
foreach ($procId in $owners) {
  if (-not $procId -or $procId -eq "0") { continue }
  try {
    $p = Get-Process -Id $procId -ErrorAction Stop
    Write-Host "Stopping stale listener on :$port -> PID $procId ($($p.ProcessName))"
    Stop-Process -Id $procId -Force -ErrorAction Stop
  } catch {
    Write-Host "Could not stop PID ${procId}: $($_.Exception.Message)"
  }
}

# Drop compiled bytecode for the modules that were hit by the cwd bug, so a
# half-written .pyc can never outlive the source fix.
Get-ChildItem -Path $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notmatch "node_modules|\.venv|site-packages" } |
  ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host "Cortex cwd=$Root port=$port"
& "C:\Program Files\Python314\python.exe" -m uvicorn CortexOS.api.main:app --host 127.0.0.1 --port $port
