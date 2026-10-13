#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$env:DB_PATH = Join-Path $PSScriptRoot 'data\oasis.db'
$env:OASIS_APP_DIR = Join-Path $PSScriptRoot 'app'
& (Join-Path $PSScriptRoot 'runtime\python\python.exe') -X utf8 -c "import os,sys;sys.path.insert(0,os.environ['OASIS_APP_DIR']);import auth;auth.enable();auth.print_code()"
if ($LASTEXITCODE -ne 0) { throw 'Access code rotation failed.' }
Write-Host 'Previously paired devices must enter the new code.'
