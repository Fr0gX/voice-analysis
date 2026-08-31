[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Get-VoiceAnalysisRepoRoot
Import-VoiceAnalysisEnv -RepoRoot $repoRoot
$python = Join-Path $repoRoot '.venv-refine\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Missing window-refine Python: $python" }

Push-Location $repoRoot
try {
    & $python -m window_refine_service.app
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
