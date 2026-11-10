<#
  winuse Windows installer. Run on the Windows PC you want to control:

      powershell -ExecutionPolicy Bypass -File scripts\install.ps1

  Or, without cloning, the one-liner (fetches this repo first):

      irm https://winuse.ozlab.xyz/install.ps1 | iex

  It finds a usable Python (or installs one), creates winuse's own virtual environment, generates an
  auth token, and registers a logon Scheduled Task that starts the winuse host in your desktop session.
  Re-running is safe. Uninstall:  powershell -File scripts\install.ps1 -Uninstall

  Requirements: an SSH server (OpenSSH, built in) with key login, so your other machine can reach this
  PC. Python is detected or installed automatically.
#>
param(
    [string]$InstallDir = 'C:\winuse',
    [string]$Source = '',          # repo root holding host\, scripts\ (default: this repo)
    [switch]$Uninstall,
    [switch]$NoTask,               # set up files/venv/token but don't register or start the task
    [switch]$NoAdmin,              # register the task without "highest privileges" (see README)
    [int]$MinMinor = 10            # minimum Python 3.x
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # avoids the slow IWR progress bar on PS 5.1
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$task = 'winuse'

function Invoke-Native {
    # Windows PowerShell 5.1 turns any stderr output of a native program into a terminating error when
    # ErrorActionPreference is Stop (even with 2>$null). Run it with Continue and check the exit code.
    param([scriptblock]$Run, [string]$What, [switch]$AllowFail)
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & $Run 2>&1; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $eap }
    if ($code -and -not $AllowFail) {
        $text = ($out | ForEach-Object { "$_" }) -join "`n"
        throw "$What failed (exit $code): $text"
    }
    return $out
}

function Remove-Winuse {
    Invoke-Native { schtasks /end /tn $task } 'stop task' -AllowFail | Out-Null
    Invoke-Native { schtasks /delete /tn $task /f } 'delete task' -AllowFail | Out-Null
    Get-CimInstance Win32_Process -Filter "name='pythonw.exe' OR name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*winuse_host.py*' -or $_.CommandLine -like '*winuse_agent.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
    # leftovers from older installers
    foreach ($d in 'winuse-src', 'winuse-dl', 'winuse-main.zip') {
        $p = Join-Path $env:TEMP $d
        if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
    }
}

if ($Uninstall) { Remove-Winuse; Write-Host "winuse removed from $InstallDir and the '$task' task deleted."; return }

# ---- find a usable Python, or install one -------------------------------------------------------
function Test-Python($exe) {
    try {
        # The Microsoft Store alias under WindowsApps is a stub that prints nothing and opens the Store.
        if ($exe -match '\\Microsoft\\WindowsApps\\') { return $null }
        $v = Invoke-Native { & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" } 'python' -AllowFail |
             Select-Object -Last 1
        if (-not $v) { return $null }
        $maj, $min = "$v".Trim().Split('.')
        if ([int]$maj -eq 3 -and [int]$min -ge $MinMinor) { return @{ exe = $exe; ver = "$v".Trim() } }
    } catch {}
    return $null
}
function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($ver in '-3.13', '-3.12', '-3.11', '-3') {
            try {
                $exe = Invoke-Native { & py $ver -c "import sys; print(sys.executable)" } 'py' -AllowFail |
                       Select-Object -Last 1
                if ($exe -and (Test-Path "$exe".Trim())) { $r = Test-Python "$exe".Trim(); if ($r) { return $r } }
            } catch {}
        }
    }
    foreach ($name in @('python', 'python3')) {
        $g = Get-Command $name -ErrorAction SilentlyContinue
        if ($g) { $r = Test-Python $g.Source; if ($r) { return $r } }
    }
    return $null
}

$py = Find-Python
if ($py) {
    Write-Host "Using existing Python $($py.ver): $($py.exe)"
} else {
    Write-Host "No Python 3.$MinMinor+ found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing Python 3.13 with winget (winget install Python.Python.3.13 --silent)..."
        Invoke-Native { winget install --id Python.Python.3.13 -e --silent --accept-source-agreements --accept-package-agreements } 'winget' | Out-Null
    } else {
        Write-Host "winget not found. Downloading the official python.org installer (per-user, silent)..."
        $url = 'https://www.python.org/ftp/python/3.13.1/python-3.13.1-amd64.exe'
        $tmp = Join-Path $env:TEMP 'winuse-python-3.13.1.exe'
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            Invoke-Native { curl.exe -fsSL --connect-timeout 15 --max-time 600 -o $tmp $url } 'download Python' | Out-Null
        } else {
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 600
        }
        Invoke-Native { & $tmp /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 } 'Python installer' | Out-Null
    }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $py = Find-Python
    if (-not $py) { throw "Python install finished but no usable Python was found on PATH. Open a new shell and re-run." }
    Write-Host "Installed Python $($py.ver)."
}

# ---- locate the repo source --------------------------------------------------------------------
if (-not $Source) { $Source = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path (Join-Path $Source 'host'))) { throw "cannot find 'host' under $Source; pass -Source <repo path>" }

# ---- install files + venv ----------------------------------------------------------------------
Write-Host "Copying the winuse host to $InstallDir ..."
New-Item -ItemType Directory -Force (Join-Path $InstallDir 'host') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $InstallDir 'logs') | Out-Null
Copy-Item (Join-Path $Source 'host\*') (Join-Path $InstallDir 'host') -Recurse -Force
Copy-Item (Join-Path $Source 'host\requirements.txt') $InstallDir -Force
# Upgrading from 0.1 (the component was called 'agent'): a logon task registered then still starts
# agent\winuse_agent.py. Leave a shim there that runs the host, so that task keeps working until the
# task is re-registered below (the folder is removed then).
$legacy = Join-Path $InstallDir 'agent'
if (Test-Path $legacy) {
    Get-ChildItem $legacy -Recurse -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $Source 'host\legacy_agent_shim.py') (Join-Path $legacy 'winuse_agent.py') -Force
    Write-Host "Upgraded a 0.1 install: $legacy now holds a shim that starts the host."
}
Copy-Item (Join-Path $Source 'scripts\install-task.ps1') $InstallDir -Force

$venvPy = Join-Path $InstallDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating the virtual environment ..."
    Invoke-Native { & $py.exe -m venv (Join-Path $InstallDir 'venv') } 'python -m venv' | Out-Null
}
Write-Host "Installing Python packages (pillow, comtypes) ..."
Invoke-Native { & $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $InstallDir 'requirements.txt') } 'pip install' | Out-Null

# ---- token -------------------------------------------------------------------------------------
$tokenFile = Join-Path $InstallDir 'token.txt'
if (-not (Test-Path $tokenFile)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [IO.File]::WriteAllText($tokenFile, $token, (New-Object Text.UTF8Encoding($false)))
    # readable by Administrators and SYSTEM, plus this user when the host runs without admin rights
    $grant = @('*S-1-5-32-544:F', '*S-1-5-18:F')
    if ($NoAdmin) { $grant += "$env:USERDOMAIN\$($env:USERNAME):R" }
    Invoke-Native { icacls $tokenFile /inheritance:r /grant:r @grant } 'icacls' | Out-Null
}
$token = (Get-Content $tokenFile -Raw).Trim()

# ---- logon task + start ------------------------------------------------------------------------
if ($NoTask) {
    Write-Host "winuse files, venv and token are set up in $InstallDir (-NoTask: task not registered)."
    return
}
Write-Host "Registering the logon task ..."
$taskArgs = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $InstallDir 'install-task.ps1'),
              '-InstallDir', $InstallDir)
if ($NoAdmin) { $taskArgs += '-NoAdmin' }
Invoke-Native { & powershell @taskArgs } 'register task' | ForEach-Object { Write-Host $_ }
Invoke-Native { schtasks /end /tn $task } 'stop task' -AllowFail | Out-Null
Start-Sleep 1
Invoke-Native { schtasks /run /tn $task } 'start task' | Out-Null
if (Test-Path $legacy) { Remove-Item -Recurse -Force $legacy -ErrorAction SilentlyContinue }  # task now points at host\

Write-Host ""
$mac = (Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -First 1).MacAddress
Write-Host "=== winuse installed and started ==="
Write-Host "Install dir : $InstallDir"
Write-Host "Host token  : $token"
Write-Host "PC MAC      : $mac   (for 'winuse wake'; winuse also learns this automatically)"
if ($NoAdmin) { Write-Host "Mode        : non-admin (cannot operate elevated windows; see README)" }
Write-Host ""
Write-Host "Now, on the machine you'll control from:"
Write-Host "  curl -fsSL https://winuse.ozlab.xyz/install.sh | sh"
Write-Host "  (it will ask for this PC's SSH host name and the token above)"
Write-Host ""
Write-Host "Make sure OpenSSH Server is enabled here and your other machine can 'ssh' in with a key."
