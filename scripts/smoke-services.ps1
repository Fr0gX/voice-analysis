[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Get-VoiceAnalysisRepoRoot
Import-VoiceAnalysisEnv -RepoRoot $repoRoot
$python = Join-Path $repoRoot '.venv-embedding\Scripts\python.exe'

Push-Location $repoRoot
try {
    & $python scripts/smoke_services.py
    if ($LASTEXITCODE -ne 0) { throw 'Real-service smoke failed' }
}
finally {
    Pop-Location
}
