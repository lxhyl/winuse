<#
  winuse Windows installer. Run in PowerShell on the Windows PC you want to control:

      powershell -ExecutionPolicy Bypass -File scripts\install.ps1

  It installs the agent into C:\winuse, creates a Python virtual environment, generates an auth
  token, and registers a logon Scheduled Task that starts the agent in your desktop session.

  Re-running is safe (it updates the files and restarts the agent). Uninstall:

      powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Uninstall

  Requirements: Python 3.10+ on PATH, and an SSH server (OpenSSH) with key login so your other
  machine can reach this PC.
#>
param(
    [string]$InstallDir = 'C:\winuse',
    [string]$Source = '',          # folder holding agent\, scripts\ (default: this repo)
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$task = 'winuse'

if ($Uninstall) {
    schtasks /end /tn $task 2>$null | Out-Null
    schtasks /delete /tn $task /f 2>$null | Out-Null
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
    Write-Host "winuse removed."
    return
}

if (-not $Source) { $Source = Split-Path -Parent $PSScriptRoot }   # repo root = parent of scripts\
foreach ($d in 'agent') {
    if (-not (Test-Path (Join-Path $Source $d))) { throw "cannot find '$d' under $Source; pass -Source <repo path>" }
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "Python 3.10+ not found on PATH. Install it from python.org, then re-run." }

New-Item -ItemType Directory -Force (Join-Path $InstallDir 'agent') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $InstallDir 'logs') | Out-Null
Copy-Item (Join-Path $Source 'agent\*') (Join-Path $InstallDir 'agent') -Recurse -Force
Copy-Item (Join-Path $Source 'agent\requirements.txt') $InstallDir -Force
Copy-Item (Join-Path $Source 'scripts\install-task.ps1') $InstallDir -Force

if (-not (Test-Path (Join-Path $InstallDir 'venv\Scripts\python.exe'))) {
    & $python -m venv (Join-Path $InstallDir 'venv')
}
& (Join-Path $InstallDir 'venv\Scripts\python.exe') -m pip install --disable-pip-version-check -q -r (Join-Path $InstallDir 'requirements.txt')

$tokenFile = Join-Path $InstallDir 'token.txt'
if (-not (Test-Path $tokenFile)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [IO.File]::WriteAllText($tokenFile, $token, (New-Object Text.UTF8Encoding($false)))
    icacls $tokenFile /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallDir 'install-task.ps1')
schtasks /end /tn $task 2>$null | Out-Null
Start-Sleep 1
schtasks /run /tn $task | Out-Null

Write-Host ""
Write-Host "winuse installed to $InstallDir and started."
Write-Host "Token file: $tokenFile"
Write-Host ""
Write-Host "On your other machine:"
Write-Host "  1. copy the token above into the repo as .token"
Write-Host "  2. winuse config set host <this PC's ssh host name>"
Write-Host "  3. winuse status"
