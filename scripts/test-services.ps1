[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$embeddingPython = Join-Path $repoRoot '.venv-embedding\Scripts\python.exe'
$refinePython = Join-Path $repoRoot '.venv-refine\Scripts\python.exe'
$analysisPython = Join-Path $repoRoot '.venv-analysis\Scripts\python.exe'

Push-Location $repoRoot
try {
    & $embeddingPython -m pytest voice_embedding_service/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Voice embedding tests failed' }
    & $refinePython -m pytest window_refine_service/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Window refine tests failed' }
    & $analysisPython -m pytest voice_analysis_engine/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Analysis engine tests failed' }
}
finally {
    Pop-Location
}

Write-Output 'Voice Analysis component tests passed.'
