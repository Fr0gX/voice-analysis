[CmdletBinding()]
param(
    [string]$Source,
    [string]$Output,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv-analysis\Scripts\python.exe'
$arguments = @('-m', 'voice_analysis_engine.recording2_dataset')
if ($Source) { $arguments += @('--source', $Source) }
if ($Output) { $arguments += @('--output', $Output) }
if ($Force) { $arguments += '--force' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "Recording2 evaluation build failed with exit code $LASTEXITCODE" }
