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
    & $analysisPython -m pytest voice_analysis_api/tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Task API tests failed' }
    Push-Location (Join-Path $repoRoot 'web')
    try {
        & npm test
        if ($LASTEXITCODE -ne 0) { throw 'Web component tests failed' }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Web production build failed' }
    }
    finally { Pop-Location }
}
finally {
    Pop-Location
}

Write-Output 'Voice Analysis component tests passed.'
