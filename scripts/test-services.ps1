[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$embeddingPython = Join-Path $repoRoot '.venv-embedding\Scripts\python.exe'
$refinePython = Join-Path $repoRoot '.venv-refine\Scripts\python.exe'

Push-Location $repoRoot
try {
    & $embeddingPython -m pytest voice_embedding_service/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Voice embedding tests failed' }
    & $refinePython -m pytest window_refine_service/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Window refine tests failed' }
}
finally {
    Pop-Location
}

Write-Output 'Voice Analysis component tests passed.'
