<#
.SYNOPSIS
  Installs the monitoring-only manager dashboard next to (never inside) the sync
  service, from this bundle, without internet access.

.DESCRIPTION
  Layout: <Target>\code (application), <Target>\runtime (Python venv), the same
  layout deployment\Start-Monitor.ps1 expects. Dependencies come only from the
  bundled wheels folder (pip --no-index). A private user profile is created with
  New-MonitorProfile.ps1 unless one already exists. The service, its folder, the
  ledger and its ACLs are never changed. The dashboard binds to 127.0.0.1 only.

  Run as the normal user who will use the dashboard (not elevated). That user
  needs read access to the ledger.

.EXAMPLE
  # Sync PC (live ledger, real config; secrets are never displayed):
  .\Install-MonitorDashboard.ps1

  # Review PC with a snapshot from Export-MonitorSnapshot.ps1:
  .\Install-MonitorDashboard.ps1 -Ledger 'C:\Snapshots\monitor-snapshot-X\sync.sqlite3' `
      -DeviceConfig 'C:\Snapshots\monitor-snapshot-X\local_config.monitor.py' -ProfileDirectory "$env:LOCALAPPDATA\FPFBiometricSyncManager-Snapshot"
#>
[CmdletBinding()]
param(
    [string]$Python = "$env:ProgramFiles\Python311\python.exe",
    [string]$Target = "$env:LOCALAPPDATA\FPFBiometricManager",
    [string]$Ledger = "$env:ProgramData\ERPNextBiometricPushService\sync.sqlite3",
    [string]$DeviceConfig = "$env:ProgramFiles\FPFBiometricSyncPhase2\local_config.py",
    [string]$ProfileDirectory = "$env:LOCALAPPDATA\FPFBiometricSyncManager"
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 turns any native stderr line into an error under 'Stop';
# judge native commands by exit code only, and show their output.
function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$Failure) {
    $ErrorActionPreference = 'Continue'
    & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } |
        Where-Object { $_ -and $_ -notmatch 'platform independent libraries' } | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { throw $Failure }
}
$bundle = Split-Path -Parent $MyInvocation.MyCommand.Path
$code = Join-Path $bundle 'code'; $wheels = Join-Path $bundle 'wheels'
foreach ($p in $code, $wheels, $Python, $Ledger, $DeviceConfig) {
    if (-not (Test-Path -LiteralPath $p)) { throw "Not found: $p" }
}
if (Test-Path -LiteralPath $Target) { throw "Target exists: $Target. Choose a new -Target or remove the old copy yourself." }
Invoke-Native $Python @('-c', 'import sys; assert sys.version_info[:2] == (3, 11), sys.version') "Python 3.11 is required: $Python"

Write-Host "Copying application to $Target\code ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $Target | Out-Null
Copy-Item -LiteralPath $code -Destination (Join-Path $Target 'code') -Recurse

Write-Host 'Creating runtime and installing bundled wheels (offline) ...' -ForegroundColor Cyan
$runtime = Join-Path $Target 'runtime'
Invoke-Native $Python @('-m', 'venv', $runtime) 'venv creation failed'
$venvPython = Join-Path $runtime 'Scripts\python.exe'
Invoke-Native $venvPython @('-m', 'pip', 'install', '--no-index', '--find-links', $wheels, '-r',
    (Join-Path $Target 'code\requirements-manager.txt'), '--disable-pip-version-check', '-q') 'Offline dependency install failed'
Invoke-Native $venvPython @('-c', 'import flask, waitress, requests') 'Dependency import check failed'

$profilePath = Join-Path $ProfileDirectory 'web-profile.json'
if (Test-Path -LiteralPath $profilePath) {
    Write-Host "Using existing profile (not modified): $profilePath" -ForegroundColor Yellow
} else {
    $profilePath = & (Join-Path $Target 'code\deployment\New-MonitorProfile.ps1') -Ledger $Ledger -DeviceConfig $DeviceConfig -Directory $ProfileDirectory
    Write-Host "Created private profile: $profilePath" -ForegroundColor Green
}

$start = Join-Path $Target 'Start-Dashboard.cmd'
$launcher = Join-Path $Target 'code\deployment\Start-Monitor.ps1'
[IO.File]::WriteAllText($start, "@echo off`r`necho Dashboard: http://127.0.0.1:8765  (close this window to stop it)`r`n" +
    "start """" /min cmd /c ""timeout /t 4 >nul & start http://127.0.0.1:8765""`r`npowershell -NoProfile -ExecutionPolicy Bypass -File ""$launcher"" -ProfilePath ""$profilePath""`r`n")
Write-Host "Installed. Start it with: $start" -ForegroundColor Green
