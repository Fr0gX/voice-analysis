[CmdletBinding()]
param([int]$ReadyTimeoutSec = 180)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Get-VoiceAnalysisRepoRoot
Import-VoiceAnalysisEnv -RepoRoot $repoRoot
& (Join-Path $PSScriptRoot 'verify-models.ps1')

$embeddingPython = Join-Path $repoRoot '.venv-embedding\Scripts\python.exe'
$refinePython = Join-Path $repoRoot '.venv-refine\Scripts\python.exe'
$logRoot = Join-Path $repoRoot 'runtime\logs'
$pidPath = Join-Path $repoRoot 'runtime\tmp\local-services.json'
New-Item -ItemType Directory -Force -Path $logRoot,(Split-Path -Parent $pidPath) | Out-Null
if (Test-Path -LiteralPath $pidPath) {
    throw "Local service PID file already exists; stop or inspect the existing services first: $pidPath"
}
$started = @()
try {
    $embedding = Start-Process -FilePath $embeddingPython -ArgumentList @('-m','voice_embedding_service.app') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'voice-embedding.stdout.log') -RedirectStandardError (Join-Path $logRoot 'voice-embedding.stderr.log')
    $started += $embedding
    $refine = Start-Process -FilePath $refinePython -ArgumentList @('-m','window_refine_service.app') -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'window-refine.stdout.log') -RedirectStandardError (Join-Path $logRoot 'window-refine.stderr.log')
    $started += $refine
    [pscustomobject]@{
        voice_embedding_pid = $embedding.Id
        window_refine_pid = $refine.Id
        started_at = [DateTimeOffset]::Now.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8

    Wait-VoiceAnalysisReady -Uri 'http://127.0.0.1:8077/health/ready' -TimeoutSec $ReadyTimeoutSec | Out-Null
    Wait-VoiceAnalysisReady -Uri 'http://127.0.0.1:8078/health/ready' -TimeoutSec $ReadyTimeoutSec | Out-Null
    Write-Output "Voice Analysis model services are ready. PID file: $pidPath"
}
catch {
    foreach ($process in $started) {
        if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    throw
}
