[CmdletBinding()]
param(
    [string]$SourceRoot = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $SourceRoot) {
    $SourceRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $repoRoot)) 'smart_badge-main\refer\badge\smart_badge'
}
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$manifestPath = Join-Path $repoRoot 'config\model-manifest.json'
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json

foreach ($model in $manifest.models) {
    $sourceDir = Join-Path $SourceRoot ([string]$model.source_dir)
    $targetDir = Join-Path $repoRoot ([string]$model.target_dir)
    if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) {
        throw "Model source directory does not exist: $sourceDir"
    }
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    foreach ($file in $model.files) {
        $source = Join-Path $sourceDir ([string]$file.name)
        $target = Join-Path $targetDir ([string]$file.name)
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Missing model source file: $source"
        }
        $sourceInfo = Get-Item -LiteralPath $source
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant()
        if ($sourceInfo.Length -ne [long]$file.size -or $sourceHash -ne [string]$file.sha256) {
            throw "Model source verification failed: $source"
        }
        if ((Test-Path -LiteralPath $target) -and -not $Force) {
            $targetInfo = Get-Item -LiteralPath $target
            $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
            if ($targetInfo.Length -eq [long]$file.size -and $targetHash -eq [string]$file.sha256) {
                continue
            }
            throw "Target exists with unexpected content; rerun with -Force after review: $target"
        }
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
}

& (Join-Path $PSScriptRoot 'verify-models.ps1')
Write-Output "Model assets installed from $SourceRoot"
