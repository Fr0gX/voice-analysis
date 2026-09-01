[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$repoRoot = Get-VoiceAnalysisRepoRoot
Import-VoiceAnalysisEnv -RepoRoot $repoRoot
$python = Join-Path $repoRoot '.venv-analysis\Scripts\python.exe'
& $python -m uvicorn voice_analysis_api.app:create_app --factory --host 127.0.0.1 --port 8076
