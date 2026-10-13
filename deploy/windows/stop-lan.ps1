#Requires -RunAsAdministrator
Stop-ScheduledTask -TaskName 'OasisLAN'
Disable-NetFirewallRule -Name 'OasisLAN-HTTPS'
Write-Host 'Oasis stopped and its LAN firewall rule disabled. Data was preserved.'
