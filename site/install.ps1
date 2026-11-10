<#
  winuse one-line installer for the Windows PC being controlled.

      irm https://winuse.ozlab.xyz/install.ps1 | iex

  Downloads the winuse source (as a zip, no git needed), then finds or installs Python, creates
  winuse's own virtual environment, generates an auth token, registers a logon task, and starts the
  agent. Everything has a timeout, so it fails with a message instead of hanging.

  Uninstall:   $env:WINUSE_UNINSTALL=1; irm https://winuse.ozlab.xyz/install.ps1 | iex
  Test (no task, isolated dir):  $env:WINUSE_NOTASK=1; $env:WINUSE_DIR='C:\winuse_test'; irm ... | iex
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # the IWR progress bar makes downloads crawl on PS 5.1
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$owner = 'lxhyl'; $branch = 'main'
$installDir = if ($env:WINUSE_DIR) { $env:WINUSE_DIR } else { 'C:\winuse' }
$src = Join-Path $env:TEMP 'winuse-src'
$flush = { param($m) Write-Host $m; [Console]::Out.Flush() }

if ($env:WINUSE_UNINSTALL -or ($args -contains '-Uninstall')) {
    schtasks /end /tn winuse 2>$null | Out-Null
    schtasks /delete /tn winuse /f 2>$null | Out-Null
    Get-CimInstance Win32_Process -Filter "name='pythonw.exe' OR name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*winuse_agent.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    foreach ($d in @($installDir, $src)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
    & $flush "winuse removed."
    return
}

# --- fetch the source as a zip (works even where github.com itself is blocked) -------------------
& $flush "Fetching winuse source ..."
$zip = Join-Path $env:TEMP 'winuse-main.zip'
$dl = Join-Path $env:TEMP 'winuse-dl'
foreach ($d in @($src, $dl)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
if (Test-Path $zip) { Remove-Item -Force $zip }

# codeload serves the zip directly; the github.com/.../archive URL only redirects to it, so this
# works even on networks that can reach codeload/the site but not github.com.
$urls = @("https://codeload.github.com/$owner/winuse/zip/refs/heads/$branch")
$ok = $false
foreach ($u in $urls) {
    try {
        & $flush "  downloading $u"
        Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing -TimeoutSec 60
        if ((Get-Item $zip).Length -gt 1024) { $ok = $true; break }
    } catch { & $flush "  failed: $($_.Exception.Message)" }
}
if (-not $ok) { throw "could not download the winuse source (network / proxy issue). Tried: $($urls -join ', ')" }

& $flush "Extracting ..."
Expand-Archive -Path $zip -DestinationPath $dl -Force
$extracted = Get-ChildItem $dl -Directory | Select-Object -First 1
Move-Item $extracted.FullName $src
if (-not (Test-Path (Join-Path $src 'scripts\install.ps1'))) { throw "download looks incomplete: $src" }

# --- run the real installer ---------------------------------------------------------------------
$instArgs = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
              '-File', (Join-Path $src 'scripts\install.ps1'), '-Source', $src, '-InstallDir', $installDir)
if ($env:WINUSE_NOTASK) { $instArgs += '-NoTask' }
& powershell @instArgs
