[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$templatePath = Join-Path $repoRoot '.env.example'
$envPath = Join-Path $repoRoot '.env'

if ((Test-Path -LiteralPath $envPath) -and -not $Force) {
    Write-Output '.env already exists; no changes made.'
    exit 0
}

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$content = [IO.File]::ReadAllText($templatePath, [Text.Encoding]::UTF8)
$content = $content.Replace('__GENERATE_LOCAL_SECRET__', $secret)
[IO.File]::WriteAllText($envPath, $content, (New-Object Text.UTF8Encoding($false)))
Write-Output 'Created local .env with a generated 256-bit API key.'
