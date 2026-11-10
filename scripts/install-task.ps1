# Registers the 'winuse' scheduled task on the Windows PC. Run as the desktop user.
# It starts the agent in that user's desktop session at logon, elevated, and restarts it on failure.
# Undo: schtasks /delete /tn winuse /f
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$user = "$env:COMPUTERNAME\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute 'C:\winuse\venv\Scripts\pythonw.exe' `
    -Argument 'C:\winuse\agent\winuse_agent.py' -WorkingDirectory 'C:\winuse'
# Starts when this user logs on, inside their desktop session (no stored password).
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$desc = 'winuse agent: lets AI agents on the Mac see and operate this desktop through the SSH tunnel. ' +
        'Files: C:\winuse. Remove: schtasks /delete /tn winuse /f'

Register-ScheduledTask -TaskName 'winuse' -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description $desc -Force | Out-Null
Get-ScheduledTask -TaskName 'winuse' | ForEach-Object { "task {0}: {1}, runs as {2} ({3})" -f $_.TaskName, $_.State, $_.Principal.UserId, $_.Principal.RunLevel }
