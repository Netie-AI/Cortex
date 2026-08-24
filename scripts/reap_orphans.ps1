<#
.SYNOPSIS
  Find local dev processes that are running but nothing is using.

.DESCRIPTION
  Two kinds of waste accumulate on this box and neither cleans itself up:

    1. MCP servers marked "disabled" in an editor's mcp.json whose process is
       still alive. Editors stop routing to a disabled server but do not reap
       the child they already spawned, so it lingers until reboot.

    2. Duplicate dev servers - the same command line started twice. Only one
       can hold the port; the loser sits there owning memory and answering
       nothing.

  REPORTS ONLY by default. Nothing is terminated unless you pass -Kill, because
  a process that looks idle may be one you are about to use. Read the table,
  then decide.

.PARAMETER Kill
  Actually terminate what the report lists.

.PARAMETER CursorConfig
  Path to the editor mcp.json to read "disabled" flags from.

.EXAMPLE
  . scripts/reap_orphans.ps1
  . scripts/reap_orphans.ps1 -Kill
#>
[CmdletBinding()]
param(
    [switch]$Kill,
    [string]$CursorConfig = "$env:USERPROFILE\.cursor\mcp.json"
)

$ErrorActionPreference = 'Stop'

function Get-MB($bytes) { [int]($bytes / 1MB) }

# Every PID that currently owns a LISTENING socket. A duplicate that holds no
# port is answering nothing, which is what makes it safe to name as a duplicate.
$listening = @{}
try {
    foreach ($c in (Get-NetTCPConnection -State Listen -ErrorAction Stop)) {
        $listening[[int]$c.OwningProcess] = $true
    }
} catch {
    foreach ($line in (netstat -ano | Select-String 'LISTENING')) {
        $parts = ($line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 5) { $listening[[int]$parts[-1]] = $true }
    }
}

$procs = Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine }

$findings = New-Object System.Collections.Generic.List[object]

# --- 1. MCP servers the editor has disabled but never stopped ---------------
#
# Identity must come from the package an entry spawns, never from a substring
# of the whole command line. The enabled Playwright server carries an
# --output-dir under a folder named uacc-playwright-harness, so a naive match
# on the name "uacc" claims it, and -Kill would then terminate a server that is
# in active use. Two rules keep that from happening: build needles from the
# executable and the package argument only, and let an enabled entry always win.

function Get-Needles($name, $entry) {
    $out = @()
    if ($entry.command) {
        $leaf = Split-Path $entry.command -Leaf
        $runners = @('npx', 'npx.cmd', 'node', 'node.exe', 'uv', 'uv.exe', 'uvx',
                     'python', 'python.exe', 'cmd', 'cmd.exe', 'pwsh', 'powershell.exe')
        if ($leaf -and ($runners -notcontains $leaf)) {
            $out += [IO.Path]::GetFileNameWithoutExtension($leaf)
        }
    }
    # For a runner (npx/uvx) the package is the first non-flag argument.
    # Later arguments are options and paths and must not become identities.
    foreach ($a in @($entry.args)) {
        if (-not $a) { continue }
        if ($a -like '-*') { continue }
        $out += ($a -replace '@[0-9][^@]*$', '')
        break
    }
    if ($out.Count -eq 0) { $out += $name }
    return ($out | Where-Object { $_ } | Select-Object -Unique)
}

function Test-NeedleMatch($cmdLine, $needles) {
    # npx resolves a scoped package to a real path, so the same package appears
    # as @drawio/mcp in the config but @drawio\\mcp on the command line, so
    # with both separators folded to one, or every scoped package is missed.
    $flat = ($cmdLine -replace '[\\/]+', '/')
    foreach ($needle in $needles) {
        if (-not $needle) { continue }
        $n = ($needle -replace '[\\/]+', '/')
        if ($flat -like "*$n*") { return $true }
    }
    return $false
}

$disabledEntries = @()
$enabledEntries = @()
if (Test-Path $CursorConfig) {
    $cfg = Get-Content $CursorConfig -Raw | ConvertFrom-Json
    foreach ($entry in $cfg.mcpServers.PSObject.Properties) {
        $row = [pscustomobject]@{
            Server  = $entry.Name
            Needles = (Get-Needles $entry.Name $entry.Value)
        }
        if ($entry.Value.disabled -eq $true) { $disabledEntries += $row }
        else { $enabledEntries += $row }
    }
}

foreach ($proc in $procs) {
    # An enabled server always wins: anything it could have spawned is in use.
    $claimedByEnabled = $false
    foreach ($e in $enabledEntries) {
        if (Test-NeedleMatch $proc.CommandLine $e.Needles) { $claimedByEnabled = $true; break }
    }
    if ($claimedByEnabled) { continue }

    foreach ($d in $disabledEntries) {
        if (-not (Test-NeedleMatch $proc.CommandLine $d.Needles)) { continue }
        $findings.Add([pscustomobject]@{
            Reason = "mcp disabled in config"
            Detail = $d.Server
            PID    = [int]$proc.ProcessId
            MB     = Get-MB $proc.WorkingSetSize
            Cmd    = $proc.CommandLine.Substring(0, [Math]::Min(90, $proc.CommandLine.Length))
        })
        break
    }
}

# --- 2. Duplicate dev servers, keeping whichever one holds the port ---------
$seenPids = $findings | ForEach-Object { $_.PID }
foreach ($group in ($procs | Group-Object CommandLine | Where-Object { $_.Count -gt 1 })) {
    $members = $group.Group | Sort-Object CreationDate
    $holder = $members | Where-Object { $listening[[int]$_.ProcessId] } | Select-Object -First 1
    if (-not $holder) { $holder = $members[0] }   # nothing listens: keep the oldest
    foreach ($proc in $members) {
        if ([int]$proc.ProcessId -eq [int]$holder.ProcessId) { continue }
        if ($seenPids -contains [int]$proc.ProcessId) { continue }
        $findings.Add([pscustomobject]@{
            Reason = "duplicate of pid $($holder.ProcessId)"
            Detail = "not listening"
            PID    = [int]$proc.ProcessId
            MB     = Get-MB $proc.WorkingSetSize
            Cmd    = $proc.CommandLine.Substring(0, [Math]::Min(90, $proc.CommandLine.Length))
        })
    }
}

# --- report -----------------------------------------------------------------
if ($findings.Count -eq 0) {
    Write-Output "Nothing to reap. No disabled-but-running MCP servers, no duplicate dev servers."
    return
}

$findings | Sort-Object MB -Descending | Format-Table -AutoSize PID, MB, Reason, Detail, Cmd
$totalMB = ($findings | Measure-Object MB -Sum).Sum
Write-Output ("{0} processes, {1} MB" -f $findings.Count, $totalMB)

if (-not $Kill) {
    Write-Output "Report only. Re-run with -Kill to terminate these."
    return
}

foreach ($f in $findings) {
    try {
        Stop-Process -Id $f.PID -Force -ErrorAction Stop
        Write-Output ("killed pid {0} ({1} MB, {2})" -f $f.PID, $f.MB, $f.Reason)
    } catch {
        Write-Output ("could not kill pid {0}: {1}" -f $f.PID, $_.Exception.Message)
    }
}
