# Registers the 'winuse' scheduled task on the Windows PC. Run as the desktop user.
# It starts the agent in that user's desktop session at logon and restarts it on failure.
# By default it runs with highest privileges (elevated); -NoAdmin registers it as a normal user.
# Undo: schtasks /delete /tn winuse /f
param(
    [string]$InstallDir = 'C:\winuse',
    [switch]$NoAdmin
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$user = "$env:COMPUTERNAME\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute (Join-Path $InstallDir 'venv\Scripts\pythonw.exe') `
    -Argument ('"' + (Join-Path $InstallDir 'agent\winuse_agent.py') + '"') -WorkingDirectory $InstallDir
# Starts when this user logs on, inside their desktop session (no stored password).
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$level = if ($NoAdmin) { 'Limited' } else { 'Highest' }
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel $level
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$desc = 'winuse agent: lets AI agents see and operate this desktop through an SSH tunnel. ' +
        "Files: $InstallDir. Remove: schtasks /delete /tn winuse /f"

Register-ScheduledTask -TaskName 'winuse' -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description $desc -Force | Out-Null
Get-ScheduledTask -TaskName 'winuse' | ForEach-Object { "task {0}: {1}, runs as {2} ({3})" -f $_.TaskName, $_.State, $_.Principal.UserId, $_.Principal.RunLevel }
