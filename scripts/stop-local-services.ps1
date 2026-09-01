[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $repoRoot 'runtime\tmp\local-services.json'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'No local Voice Analysis PID file exists.'
    exit 0
}

$pids = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
foreach ($property in @('task_api_pid','voice_embedding_pid','window_refine_pid')) {
    $processId = [int]$pids.$property
    if ($processId -gt 0 -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $processId -Force
    }
}
Remove-Item -LiteralPath $pidPath -Force
Write-Output 'Local Voice Analysis services stopped.'
