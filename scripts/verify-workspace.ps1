[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

foreach ($relative in @(
    '.env', '.env.example', 'config/services.yaml',
    'config/model-manifest.json',
    '.venv-embedding/Scripts/python.exe', '.venv-refine/Scripts/python.exe',
    '.venv-analysis/Scripts/python.exe', 'config/analysis.yaml',
    'voice_embedding_service/app.py', 'window_refine_service/app.py',
    'voice_analysis_api/app.py', 'web/package.json', 'web/package-lock.json',
    'scripts/start-voice-embedding.ps1', 'scripts/start-window-refine.ps1',
    'scripts/start-task-api.ps1', 'scripts/start-all-services.ps1',
    'scripts/start-local-services.ps1', 'scripts/stop-local-services.ps1'
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $relative))) {
        $failures.Add("missing: $relative")
    }
}

$analysisPython = Join-Path $repoRoot '.venv-analysis\Scripts\python.exe'
if (Test-Path -LiteralPath $analysisPython) {
    & $analysisPython -c "import sys, fastapi, httpx, multipart, nls, numpy, pydantic, scipy, sklearn, uvicorn, yaml; from aliyunsdkcore.client import AcsClient; assert sys.version_info[:2] == (3, 11)"
    if ($LASTEXITCODE -ne 0) { $failures.Add('analysis engine environment import/version validation failed') }
}

if (Test-Path -LiteralPath (Join-Path $repoRoot '.env')) {
    $raw = [IO.File]::ReadAllText((Join-Path $repoRoot '.env'))
    if ($raw -match 'VOICEANALYSIS_API_KEY=__GENERATE_LOCAL_SECRET__' -or $raw -notmatch '(?m)^VOICEANALYSIS_API_KEY=.{32,}$') {
        $failures.Add('VOICEANALYSIS_API_KEY is missing or was not generated')
    }
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    $failures.Add('ffmpeg is not available on PATH')
}
if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    $failures.Add('Node.js and npm are required for the M3 Web application')
}

foreach ($relative in @(
    'runtime/models/ecapa/spkrec-ecapa-voxceleb',
    'runtime/models/pyannote/segmentation-3.0'
)) {
    $path = Join-Path $repoRoot $relative
    $files = @(Get-ChildItem -LiteralPath $path -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne '.gitkeep' })
    if ($files.Count -eq 0) {
        $warnings.Add("model not-ready: $relative")
    }
}

if ($warnings.Count -eq 0) {
    try {
        & (Join-Path $PSScriptRoot 'verify-models.ps1') | Out-Null
    }
    catch {
        $failures.Add("model manifest verification failed: $($_.Exception.Message)")
    }
}

$embeddingPython = Join-Path $repoRoot '.venv-embedding\Scripts\python.exe'
if (Test-Path -LiteralPath $embeddingPython) {
    & $embeddingPython -c "import sys, torch, torchaudio, speechbrain; assert sys.version_info[:2] == (3, 11); assert torch.__version__.startswith('2.11.'); assert torchaudio.__version__.startswith('2.11.'); assert speechbrain.__version__ == '1.1.0'"
    if ($LASTEXITCODE -ne 0) { $failures.Add('embedding environment import/version validation failed') }
}

$refinePython = Join-Path $repoRoot '.venv-refine\Scripts\python.exe'
if (Test-Path -LiteralPath $refinePython) {
    & $refinePython -c "import sys, torch, torchaudio, miniaudio, pyannote.audio; assert sys.version_info[:2] == (3, 11); assert torch.__version__.startswith('2.11.'); assert torchaudio.__version__.startswith('2.11.'); assert pyannote.audio.__version__.startswith('4.')"
    if ($LASTEXITCODE -ne 0) { $failures.Add('window-refine environment import/version validation failed') }
}

foreach ($warning in $warnings) { Write-Warning $warning }
if ($failures.Count -gt 0) {
    foreach ($failure in $failures) { Write-Error $failure }
    exit 1
}

Write-Output 'Workspace configuration and Python environments are ready.'
