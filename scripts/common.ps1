$ErrorActionPreference = 'Stop'

function Get-VoiceAnalysisRepoRoot {
    return (Split-Path -Parent $PSScriptRoot)
}
function Import-VoiceAnalysisEnv {
    param([string]$RepoRoot = (Get-VoiceAnalysisRepoRoot))

    $envPath = Join-Path $RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw "Missing local environment file: $envPath"
    }
    foreach ($line in [IO.File]::ReadAllLines($envPath)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $parts = $trimmed.Split('=', 2)
        if ($parts.Count -ne 2) { continue }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

function Wait-VoiceAnalysisReady {
    param(
        [Parameter(Mandatory)][string]$Uri,
        [int]$TimeoutSec = 180
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSec)
    do {
        try {
            $response = Invoke-RestMethod -Method Get -Uri $Uri -TimeoutSec 5
            if ($response.status -eq 'ready') { return $response }
        }
        catch {
            # Model preload returns 503 until it is ready. Keep polling until the deadline.
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTimeOffset]::UtcNow -lt $deadline)

    throw "Service did not become ready within ${TimeoutSec}s: $Uri"
}
