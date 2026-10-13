# Read only on the owner's machine; do not copy the JSON file into GitHub.
$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'data\oasis.auth.json') -Raw | ConvertFrom-Json
Write-Host "Oasis access code: $($settings.code)"
