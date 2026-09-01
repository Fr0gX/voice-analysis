[CmdletBinding()]
param([switch]$InstallDependencies)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required but was not found on PATH.'
}

Push-Location $repoRoot
try {
    $embeddingMissing = -not (Test-Path -LiteralPath '.venv-embedding\Scripts\python.exe')
    $refineMissing = -not (Test-Path -LiteralPath '.venv-refine\Scripts\python.exe')
    $analysisMissing = -not (Test-Path -LiteralPath '.venv-analysis\Scripts\python.exe')
    if ($embeddingMissing -or $refineMissing -or $analysisMissing) {
        $python311 = (& uv python find 3.11 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0 -or -not $python311) {
            uv python install 3.11
            if ($LASTEXITCODE -ne 0) { throw 'uv python install failed' }
            $python311 = (& uv python find 3.11 | Select-Object -First 1)
            if ($LASTEXITCODE -ne 0 -or -not $python311) { throw 'Python 3.11 is unavailable after installation' }
        }
    }
    if ($embeddingMissing) {
        uv venv --python $python311 .venv-embedding
        if ($LASTEXITCODE -ne 0) { throw 'embedding venv creation failed' }
    }
    if ($refineMissing) {
        uv venv --python $python311 .venv-refine
        if ($LASTEXITCODE -ne 0) { throw 'window-refine venv creation failed' }
    }
    if ($analysisMissing) {
        uv venv --python $python311 .venv-analysis
        if ($LASTEXITCODE -ne 0) { throw 'analysis-engine venv creation failed' }
    }

    if ($InstallDependencies) {
        uv pip install --python .venv-embedding\Scripts\python.exe -r environments\voice-embedding\requirements.lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'embedding dependency installation failed' }
        uv pip install --python .venv-refine\Scripts\python.exe -r environments\window-refine\requirements.lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'window-refine dependency installation failed' }
        uv pip install --python .venv-analysis\Scripts\python.exe -r environments\analysis-engine\requirements.lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'analysis-engine dependency installation failed' }
    }
}
finally {
    Pop-Location
}

Write-Output ('Python environments ready. Dependencies installed: ' + [bool]$InstallDependencies)
