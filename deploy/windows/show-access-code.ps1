# Read only on the owner's machine; do not copy the JSON file into GitHub.
$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'data\oasis.auth.json') -Raw | ConvertFrom-Json
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'lan-config.json') -Raw | ConvertFrom-Json
Write-Host "Oasis URL: https://$($config.host):$($config.port)"
Write-Host "Oasis access code: $($settings.code)"
$cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2((Join-Path $PSScriptRoot 'data\certs\server.crt'))
$sha = [Security.Cryptography.SHA256]::Create()
try { Write-Host ('Certificate SHA-256: ' + [BitConverter]::ToString($sha.ComputeHash($cert.RawData)).Replace('-','').ToLowerInvariant()) } finally { $sha.Dispose(); $cert.Dispose() }
