[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifest = Get-Content -Raw -LiteralPath (Join-Path $repoRoot 'config\model-manifest.json') | ConvertFrom-Json
$verified = 0

foreach ($model in $manifest.models) {
    $targetDir = Join-Path $repoRoot ([string]$model.target_dir)
    foreach ($file in $model.files) {
        $path = Join-Path $targetDir ([string]$file.name)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Missing model file: $path"
        }
        $info = Get-Item -LiteralPath $path
        if ($info.Length -ne [long]$file.size) {
            throw "Model size mismatch: $path"
        }
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        if ($hash -ne [string]$file.sha256) {
            throw "Model SHA256 mismatch: $path"
        }
        $verified += 1
    }
}

Write-Output "Model manifest verification passed: $verified files"
