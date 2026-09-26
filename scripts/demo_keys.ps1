# Per-install API keys for the local demo (T2-FAILCLOSED, #263).
#
# Cortex has no built-in API key: with DMS_API_KEYS unset every gated request is
# refused. The local demo therefore generates its own random viewer / steward /
# admin keys once per install, keeps them in a gitignored file, and hands them
# to both the engine (DMS_API_KEYS) and the demo UI (NEXT_PUBLIC_DMS_*_KEY).
#
# Dot-source this file, then call Initialize-DemoApiKeys -Root <repo root>.
# The key file is data/local/demo_api_keys.env (gitignored). Delete it to rotate.

function New-DemoApiKey {
    param([string]$Role)
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $hex = -join ($bytes | ForEach-Object { $_.ToString("x2") })
    return "dms-local-$Role-$hex"
}

function Get-DemoApiKeyFile {
    param([Parameter(Mandatory = $true)][string]$Root)
    return (Join-Path $Root "data\local\demo_api_keys.env")
}

function Initialize-DemoApiKeys {
    <#
      Load the per-install demo keys, generating them on first use, and export
      DMS_API_KEYS plus NEXT_PUBLIC_DMS_{VIEWER,STEWARD,ADMIN}_KEY into this
      process so child processes (uvicorn, next dev) inherit them.
      Returns the key file path. Never prints the key values.
    #>
    param([Parameter(Mandatory = $true)][string]$Root)
    $file = Get-DemoApiKeyFile -Root $Root
    $keys = @{}
    if (Test-Path $file) {
        Get-Content $file -Encoding UTF8 | ForEach-Object {
            $line = $_.Trim()
            if (-not $line -or $line.StartsWith("#")) { return }
            $eq = $line.IndexOf("=")
            if ($eq -lt 1) { return }
            $keys[$line.Substring(0, $eq).Trim()] = $line.Substring($eq + 1).Trim()
        }
    }
    $names = @("NEXT_PUBLIC_DMS_VIEWER_KEY", "NEXT_PUBLIC_DMS_STEWARD_KEY", "NEXT_PUBLIC_DMS_ADMIN_KEY")
    $missing = @($names | Where-Object { -not $keys[$_] })
    if ($missing.Count -gt 0) {
        $keys["NEXT_PUBLIC_DMS_VIEWER_KEY"] = New-DemoApiKey -Role "viewer"
        $keys["NEXT_PUBLIC_DMS_STEWARD_KEY"] = New-DemoApiKey -Role "steward"
        $keys["NEXT_PUBLIC_DMS_ADMIN_KEY"] = New-DemoApiKey -Role "admin"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $file) | Out-Null
        $body = @(
            "# Local demo API keys, generated for this install. Gitignored; never commit.",
            "# Delete this file to rotate. Used by SETUP_ONCE.ps1, demo/run_demo.ps1",
            "# and scripts/start_cortex_engine.ps1."
        ) + ($names | ForEach-Object { "$_=$($keys[$_])" })
        Set-Content -Path $file -Value $body -Encoding UTF8
    }
    foreach ($n in $names) { Set-Item -Path "env:$n" -Value $keys[$n] }
    $env:DMS_API_KEYS = "viewer:$($keys['NEXT_PUBLIC_DMS_VIEWER_KEY']);" +
        "steward:$($keys['NEXT_PUBLIC_DMS_STEWARD_KEY']);" +
        "admin:$($keys['NEXT_PUBLIC_DMS_ADMIN_KEY'])"
    return $file
}
