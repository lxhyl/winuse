<#
  winuse Windows installer. Run on the Windows PC you want to control:

      powershell -ExecutionPolicy Bypass -File scripts\install.ps1

  Or, without cloning, the one-liner (fetches this repo first):

      irm https://winuse.ozlab.xyz/install.ps1 | iex

  It finds a usable Python (or installs one), creates winuse's own virtual environment, generates an
  auth token, and registers a logon Scheduled Task that starts the agent in your desktop session.
  Re-running is safe. Uninstall:  powershell -File scripts\install.ps1 -Uninstall

  Requirements: an SSH server (OpenSSH, built in) with key login, so your other machine can reach this
  PC. Python is detected or installed automatically.
#>
param(
    [string]$InstallDir = 'C:\winuse',
    [string]$Source = '',          # repo root holding agent\, scripts\ (default: this repo)
    [switch]$Uninstall,
    [switch]$NoTask,               # set up files/venv/token but don't register or start the task
    [int]$MinMinor = 10            # minimum Python 3.x
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$task = 'winuse'

function Remove-Winuse {
    schtasks /end /tn $task 2>$null | Out-Null
    schtasks /delete /tn $task /f 2>$null | Out-Null
    Get-CimInstance Win32_Process -Filter "name='pythonw.exe' OR name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*winuse_agent.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
}

if ($Uninstall) { Remove-Winuse; Write-Host "winuse removed from $InstallDir and the '$task' task deleted."; return }

# ---- find a usable Python, or install one -------------------------------------------------------
function Test-Python($exe) {
    try {
        # The Microsoft Store alias under WindowsApps is a stub that prints nothing and opens the Store.
        if ($exe -match '\\Microsoft\\WindowsApps\\') { return $null }
        $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $v) { return $null }
        $maj, $min = $v.Trim().Split('.')
        if ([int]$maj -eq 3 -and [int]$min -ge $MinMinor) { return @{ exe = $exe; ver = $v.Trim() } }
    } catch {}
    return $null
}
function Find-Python {
    foreach ($c in @('py -3.13', 'py -3.12', 'py -3.11', 'py -3')) {
        $parts = $c.Split(' ')
        $launcher = (Get-Command $parts[0] -ErrorAction SilentlyContinue).Source
        if ($launcher) {
            try {
                $exe = (& $parts[0] $parts[1] -c "import sys; print(sys.executable)" 2>$null)
                if ($exe) { $r = Test-Python $exe.Trim(); if ($r) { return $r } }
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
    Write-Host "No Python $([string]::Format('3.{0}+', $MinMinor)) found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing Python 3.13 with winget (winget install Python.Python.3.13 --silent)..."
        winget install --id Python.Python.3.13 -e --silent --accept-source-agreements --accept-package-agreements
    } else {
        Write-Host "winget not found. Downloading the official python.org installer (per-user, silent)..."
        $url = 'https://www.python.org/ftp/python/3.13.1/python-3.13.1-amd64.exe'
        $tmp = Join-Path $env:TEMP 'winuse-python-3.13.1.exe'
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        & $tmp /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 | Out-Null
    }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $py = Find-Python
    if (-not $py) { throw "Python install finished but no usable Python was found on PATH. Open a new shell and re-run." }
    Write-Host "Installed Python $($py.ver)."
}

# ---- locate the repo source --------------------------------------------------------------------
if (-not $Source) { $Source = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path (Join-Path $Source 'agent'))) { throw "cannot find 'agent' under $Source; pass -Source <repo path>" }

# ---- install files + venv ----------------------------------------------------------------------
New-Item -ItemType Directory -Force (Join-Path $InstallDir 'agent') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $InstallDir 'logs') | Out-Null
Copy-Item (Join-Path $Source 'agent\*') (Join-Path $InstallDir 'agent') -Recurse -Force
Copy-Item (Join-Path $Source 'agent\requirements.txt') $InstallDir -Force
Copy-Item (Join-Path $Source 'scripts\install-task.ps1') $InstallDir -Force

$venvPy = Join-Path $InstallDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { & $py.exe -m venv (Join-Path $InstallDir 'venv') }
& $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $InstallDir 'requirements.txt')

# ---- token -------------------------------------------------------------------------------------
$tokenFile = Join-Path $InstallDir 'token.txt'
if (-not (Test-Path $tokenFile)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [IO.File]::WriteAllText($tokenFile, $token, (New-Object Text.UTF8Encoding($false)))
    icacls $tokenFile /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
}
$token = (Get-Content $tokenFile -Raw).Trim()

# ---- logon task + start ------------------------------------------------------------------------
if ($NoTask) {
    Write-Host "winuse files, venv and token are set up in $InstallDir (-NoTask: task not registered)."
    return
}
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallDir 'install-task.ps1')
schtasks /end /tn $task 2>$null | Out-Null
Start-Sleep 1
schtasks /run /tn $task | Out-Null

Write-Host ""
$mac = (Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -First 1).MacAddress
Write-Host "=== winuse installed and started ==="
Write-Host "Install dir : $InstallDir"
Write-Host "Agent token : $token"
Write-Host "PC MAC      : $mac   (for 'winuse wake'; winuse also learns this automatically)"
Write-Host ""
Write-Host "Now, on the machine you'll control from:"
Write-Host "  curl -fsSL https://winuse.ozlab.xyz/install.sh | sh"
Write-Host "  (it will ask for this PC's SSH host name and the token above)"
Write-Host ""
Write-Host "Make sure OpenSSH Server is enabled here and your other machine can 'ssh' in with a key."
