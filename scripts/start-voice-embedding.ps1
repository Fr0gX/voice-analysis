[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Get-VoiceAnalysisRepoRoot
Import-VoiceAnalysisEnv -RepoRoot $repoRoot
$python = Join-Path $repoRoot '.venv-embedding\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Missing embedding Python: $python" }

Push-Location $repoRoot
try {
    & $python -m voice_embedding_service.app
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
