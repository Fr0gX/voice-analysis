[CmdletBinding()]
param(
    [ValidateRange(10, 1800)]
    [int]$ReadyTimeoutSec = 180,
    [switch]$SkipWorkspaceVerification
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not $SkipWorkspaceVerification) {
    & (Join-Path $PSScriptRoot 'verify-workspace.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Workspace verification failed' }
}

& (Join-Path $PSScriptRoot 'start-local-services.ps1') -ReadyTimeoutSec $ReadyTimeoutSec
if ($LASTEXITCODE -ne 0) { throw 'Voice Analysis services failed to start' }

Write-Output ''
Write-Output 'Voice Analysis is ready:'
Write-Output '  Web:              http://127.0.0.1:8076/'
Write-Output '  Task API health:  http://127.0.0.1:8076/health'
Write-Output '  Embedding ready:  http://127.0.0.1:8077/health/ready'
Write-Output '  Refine ready:     http://127.0.0.1:8078/health/ready'
Write-Output ''
Write-Output 'Stop all services with: ./scripts/stop-local-services.ps1'
Write-Output 'Logs: runtime/logs/'
