<#
  winuse one-line installer for the Windows PC being controlled.

      irm https://winuse.ozlab.xyz/install.ps1 | iex

  For agents installing over SSH, or on networks where PowerShell's own web client stalls (it can hang
  forever even with -TimeoutSec), use curl.exe (built into Windows 10/11): every fetch is time-limited
  and retried.

      powershell -NoProfile -Command "iex ((curl.exe -fsSL --retry 5 --retry-all-errors --connect-timeout 10 --max-time 60 https://winuse.ozlab.xyz/install.ps1) -join [char]10)"

  Downloads the winuse source (a zip, no git needed), then finds or installs Python, creates winuse's
  own virtual environment, generates an auth token, registers a logon task and starts the winuse host.

  Options (set before running):
    $env:WINUSE_UNINSTALL=1   remove winuse
    $env:WINUSE_DIR='D:\x'    install somewhere other than C:\winuse
    $env:WINUSE_NOADMIN=1     run the winuse host without administrator rights
    $env:WINUSE_NOTASK=1      set up files only, no logon task (for testing)
    $env:WINUSE_SOURCE_ZIP='C:\path\src.zip'   install from a local source zip (offline / CI)

  This file is plain ASCII on purpose, so any download method decodes it the same way.
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$owner = 'lxhyl'; $branch = 'main'
$installDir = if ($env:WINUSE_DIR) { $env:WINUSE_DIR } else { 'C:\winuse' }
$t0 = Get-Date
function Say($m) { Write-Host ('[winuse {0,5:n1}s] {1}' -f ((Get-Date) - $t0).TotalSeconds, $m) }

if ($env:WINUSE_UNINSTALL -or ($args -contains '-Uninstall')) {
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    schtasks /end /tn winuse 2>&1 | Out-Null
    schtasks /delete /tn winuse /f 2>&1 | Out-Null
    $ErrorActionPreference = $eap
    Get-CimInstance Win32_Process -Filter "name='pythonw.exe' OR name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*winuse_host.py*' -or $_.CommandLine -like '*winuse_agent.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }
    Get-ChildItem $env:TEMP -Filter 'winuse-*' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Say 'winuse removed.'
    return
}

# Each run works in its own temp folder, so leftovers or a second run at the same time can't interfere.
$work = Join-Path $env:TEMP ('winuse-install-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null
try {
    # --- download the source zip. codeload serves it directly; github.com itself may be unreachable.
    $zip = Join-Path $work 'src.zip'
    $urls = @("https://codeload.github.com/$owner/winuse/zip/refs/heads/$branch")
    $curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
    $ok = $false
    if ($env:WINUSE_SOURCE_ZIP) {
        Say "using local source $($env:WINUSE_SOURCE_ZIP)"
        Copy-Item $env:WINUSE_SOURCE_ZIP $zip
        $ok = $true; $urls = @()
    }
    foreach ($u in $urls) {
        foreach ($attempt in 1, 2, 3) {
            Say "downloading $u (attempt $attempt)"
            try {
                if (Test-Path $zip) { Remove-Item -Force $zip }
                if ($curl) {
                    # curl.exe has reliable connect/transfer limits. Native stderr would be a terminating
                    # error under Stop on PowerShell 5.1, so run with Continue and check the exit code.
                    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
                    & $curl -fsSL --connect-timeout 15 --max-time 120 -o $zip $u 2>&1 | Out-Null
                    $code = $LASTEXITCODE; $ErrorActionPreference = $eap
                    if ($code -ne 0) { throw "curl exit $code" }
                } else {
                    Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing -TimeoutSec 60
                }
                if ((Test-Path $zip) -and (Get-Item $zip).Length -gt 1024) { $ok = $true; break }
                throw 'download was empty'
            } catch { Say "  failed: $($_.Exception.Message)" }
        }
        if ($ok) { break }
    }
    if (-not $ok) { throw "could not download the winuse source (network / proxy). Tried: $($urls -join ', ')" }

    # --- extract with .NET: faster than Expand-Archive and emits no progress records over SSH.
    Say 'extracting'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $x = Join-Path $work 'x'
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $x)
    $src = (Get-ChildItem $x -Directory | Select-Object -First 1).FullName
    $installer = Join-Path $src 'scripts\install.ps1'
    if (-not (Test-Path $installer)) { throw "download looks incomplete: $src" }

    # --- run the real installer in this process (no child PowerShell, no execution-policy prompt).
    Say 'running the installer'
    $run = [scriptblock]::Create((Get-Content $installer -Raw))
    $params = @{ Source = $src; InstallDir = $installDir }
    if ($env:WINUSE_NOTASK) { $params.NoTask = $true }
    if ($env:WINUSE_NOADMIN) { $params.NoAdmin = $true }
    & $run @params
    Say 'done'
} finally {
    Set-Location $env:TEMP
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
