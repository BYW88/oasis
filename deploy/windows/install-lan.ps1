#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$BundlePath,
    [Parameter(Mandatory=$true)][string]$ListenAddress,
    [Parameter(Mandatory=$true)][string]$AllowedSubnet,
    [Parameter(Mandatory=$true)][string]$InterfaceAlias,
    [Parameter(Mandatory=$true)][string]$OwnerSid,
    [string]$InstallPath = "$env:ProgramData\OasisLAN",
    [ValidateRange(1024,65535)][int]$Port = 8443
)
$ErrorActionPreference = 'Stop'
$taskName = 'OasisLAN'
$accountName = 'OasisSvc'
$ruleName = 'OasisLAN-HTTPS'
$bundle = (Resolve-Path -LiteralPath $BundlePath).Path
$target = [IO.Path]::GetFullPath($InstallPath)
if ($target -eq [IO.Path]::GetPathRoot($target)) { throw 'Installation cannot target a drive root.' }
if (Test-Path -LiteralPath $target) { throw "Installation directory already exists: $target. No changes made." }
if (Get-LocalUser -Name $accountName -ErrorAction SilentlyContinue) { throw "Account $accountName already exists. No changes made." }
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Scheduled task already exists. No changes made.' }
if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) { throw 'Firewall rule already exists. No changes made.' }
$address = Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 | Where-Object IPAddress -eq $ListenAddress
if (-not $address) { throw 'ListenAddress is not assigned to the specified interface.' }
$profile = Get-NetConnectionProfile -InterfaceAlias $InterfaceAlias
if ($profile.NetworkCategory -eq 'DomainAuthenticated') { throw 'This installer is for a private home LAN.' }
foreach ($fwProfile in Get-NetFirewallProfile) {
    if (-not $fwProfile.Enabled) { throw 'Enable Windows Firewall before installation.' }
}
$manifestPath = Join-Path $bundle 'bundle-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) { throw 'Bundle manifest missing.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
foreach ($entry in $manifest.files) {
    $file = [IO.Path]::GetFullPath((Join-Path $bundle $entry.path))
    if (-not $file.StartsWith($bundle.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid bundle path.' }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.sha256) { throw "Bundle hash mismatch: $($entry.path)" }
}
$accountCreated = $false
$taskCreated = $false
$ruleCreated = $false
try {
    New-Item -ItemType Directory -Path $target | Out-Null
    foreach ($name in @('app','frontend','runtime')) {
        Copy-Item -LiteralPath (Join-Path $bundle $name) -Destination (Join-Path $target $name) -Recurse
    }
    foreach ($name in @('lan_server.py','windows_limits.py','initialize_lan.py','show-access-code.ps1','rotate-access-code.ps1','stop-lan.ps1')) {
        Copy-Item -LiteralPath (Join-Path $bundle $name) -Destination (Join-Path $target $name)
    }
    foreach ($name in @('data','logs','data\temp')) { New-Item -ItemType Directory -Path (Join-Path $target $name) -Force | Out-Null }
    $random = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($random) } finally { $rng.Dispose() }
    $password = 'Aa!9' + [Convert]::ToBase64String($random)
    $securePassword = ConvertTo-SecureString $password -AsPlainText -Force
    $user = New-LocalUser -Name $accountName -Password $securePassword -PasswordNeverExpires -UserMayNotChangePassword -Description 'Oasis LAN restricted background service'
    $accountCreated = $true
    $usersGroup = Get-LocalGroup -SID 'S-1-5-32-545'
    Add-LocalGroupMember -Group $usersGroup -Member $user -ErrorAction SilentlyContinue
    $serviceSid = $user.SID.Value
    # Use SIDs to work on both localized and English Windows installations.
    & icacls.exe $target /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "*${OwnerSid}:(OI)(CI)RX" "*${serviceSid}:(OI)(CI)RX" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict installation permissions.' }
    foreach ($name in @('data','logs')) {
        & icacls.exe (Join-Path $target $name) /grant "*${serviceSid}:(OI)(CI)M" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to grant service data permissions.' }
    }
    $python = Join-Path $target 'runtime\python\python.exe'
    & $python -X utf8 (Join-Path $target 'initialize_lan.py') --root $target --host $ListenAddress --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Credential and certificate initialization failed.' }
    # A scheduled task stores its generated credential in Windows, never a text file.
    $arguments = '-X utf8 -u "' + (Join-Path $target 'lan_server.py') + '"'
    $action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $target
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings
    Register-ScheduledTask -TaskName $taskName -InputObject $task -User "$env:COMPUTERNAME\$accountName" -Password $password | Out-Null
    $taskCreated = $true
    $password = $null
    # The user has confirmed this interface is their trusted private/home LAN.
    Set-NetConnectionProfile -InterfaceAlias $InterfaceAlias -NetworkCategory Private
    New-NetFirewallRule -Name $ruleName -DisplayName 'Oasis LAN HTTPS (private subnet only)' -Direction Inbound -Action Allow -Profile Private -InterfaceAlias $InterfaceAlias -Protocol TCP -LocalAddress $ListenAddress -LocalPort $Port -RemoteAddress $AllowedSubnet -Program $python -EdgeTraversalPolicy Block | Out-Null
    $ruleCreated = $true
    Start-ScheduledTask -TaskName $taskName
    [pscustomobject]@{Url="https://${ListenAddress}:$Port";Account=$accountName;Task=$taskName;InstallPath=$target;FirewallSubnet=$AllowedSubnet} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $target 'installation-info.json') -Encoding UTF8
    Write-Host "Installed. Open https://${ListenAddress}:$Port on a trusted LAN device."
    Write-Host "Access code: run $target\show-access-code.ps1 locally."
} catch {
    if ($ruleCreated) { Remove-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue }
    if ($taskCreated) { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue }
    if ($accountCreated) { Remove-LocalUser -Name $accountName -ErrorAction SilentlyContinue }
    Write-Warning "Installation failed. Copied files were retained for inspection at $target; no automatic recursive deletion was performed."
    throw
} finally {
    $password = $null
    if ($securePassword) { $securePassword.Dispose() }
}
