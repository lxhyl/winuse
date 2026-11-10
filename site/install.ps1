<#
  winuse one-line installer for the Windows PC being controlled.

      irm https://winuse.ozlab.xyz/install.ps1 | iex

  Fetches the winuse repo from GitHub, then finds or installs Python, creates winuse's own virtual
  environment, generates an auth token, registers a logon task, and starts the agent.

  Uninstall:   $env:WINUSE_UNINSTALL=1; irm https://winuse.ozlab.xyz/install.ps1 | iex
#>
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$repo = 'https://github.com/lxhyl/winuse'
$installDir = if ($env:WINUSE_DIR) { $env:WINUSE_DIR } else { 'C:\winuse' }
$src = Join-Path $env:TEMP 'winuse-src'

if ($env:WINUSE_UNINSTALL -or ($args -contains '-Uninstall')) {
    schtasks /end /tn winuse 2>$null | Out-Null
    schtasks /delete /tn winuse /f 2>$null | Out-Null
    Get-CimInstance Win32_Process -Filter "name='pythonw.exe' OR name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*winuse_agent.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    foreach ($d in @($installDir, $src)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
    Write-Host "winuse removed."
    return
}

Write-Host "Fetching winuse from $repo ..."
if (Test-Path $src) { Remove-Item -Recurse -Force $src }
if (Get-Command git -ErrorAction SilentlyContinue) {
    git clone --depth 1 "$repo.git" $src 2>&1 | Out-Null
} else {
    $zip = Join-Path $env:TEMP 'winuse-main.zip'
    Invoke-WebRequest -Uri "$repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
    $dl = Join-Path $env:TEMP 'winuse-dl'
    if (Test-Path $dl) { Remove-Item -Recurse -Force $dl }
    Expand-Archive -Path $zip -DestinationPath $dl -Force
    Move-Item (Join-Path $dl 'winuse-main') $src
}
if (-not (Test-Path (Join-Path $src 'scripts\install.ps1'))) { throw "download looks incomplete: $src" }

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $src 'scripts\install.ps1') -Source $src -InstallDir $installDir
