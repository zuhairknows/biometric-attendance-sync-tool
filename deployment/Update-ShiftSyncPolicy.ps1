<#
.SYNOPSIS
  In-place update of an existing durable sync installation to the Shift Type
  watermark policy (wait only for punches that can still arrive).

.DESCRIPTION
  Replaces exactly two service files (durable_sync.py, sync_ledger.py) after
  verifying that the installed copies are the reviewed versions this update was
  written against. Without -Apply it only reports what it would do.

  With -Apply it: backs up both files and local_config.py, optionally appends
  SHIFT_SYNC_IGNORED_DEVICES to local_config.py, stops the service, copies the
  files, starts the service and rolls everything back if the service does not
  return to Running. The SQLite ledger, logs and devices are never touched.

  Run from an elevated Windows PowerShell 5.1 in the folder that holds this
  script together with the new durable_sync.py and sync_ledger.py.

.EXAMPLE
  .\Update-ShiftSyncPolicy.ps1
  .\Update-ShiftSyncPolicy.ps1 -IgnoreDevices FP1_DEVICE_06 -Apply
#>
[CmdletBinding()]
param(
    [string]$AppRoot = "$env:ProgramFiles\FPFBiometricSyncPhase2",
    [string]$ServiceName = 'ERPNextBiometricPushService',
    [string[]]$IgnoreDevices = @(),
    [ValidateRange(0, 168)][double]$RetryGraceHours = -1,
    [switch]$Apply,
    [int]$WaitMinutes = 0
)
$ErrorActionPreference = 'Stop'

# SHA-256 of LF-normalized bytes. OLD = reviewed installed versions (unchanged since
# commit 4f20179, 2026-10-21); NEW = Shift Type watermark policy.
$Files = [ordered]@{
    'durable_sync.py' = @{ Old = '50becdebeb006b2f150ddff72dafac940201bc4d33ed52a5669f07f7aa18923f'
                           New = 'e1685346fed20e29d3b9de241078b92648d952c98c6f5401b5909f9f3e75d479' }
    'sync_ledger.py'  = @{ Old = '5e6713142f432b808c7bba50a5f55798733c5c981657e381f09576fde7bc7a2d'
                           New = '422687e5c781d24474ad505d030c4ccef260ae78876348dafa5e5b97f20b7721' }
}

function Get-NormalizedHash([string]$Path) {
    $text = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8).Replace("`r`n", "`n")
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { ($sha.ComputeHash([Text.UTF8Encoding]::new($false).GetBytes($text)) | ForEach-Object { $_.ToString('x2') }) -join '' }
    finally { $sha.Dispose() }
}
function Say([string]$Text, [string]$Color = 'Gray') { Write-Host $Text -ForegroundColor $Color }
function Wait-ServiceState([string]$State, [int]$Seconds) {
    try { (Get-Service $ServiceName).WaitForStatus($State, [TimeSpan]::FromSeconds($Seconds)); return $true } catch { return $false }
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this from an elevated PowerShell (Run as Administrator).' }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$config = Join-Path $AppRoot 'local_config.py'
if (-not (Test-Path -LiteralPath $config)) { throw "local_config.py not found under $AppRoot" }
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $service) { throw "Service $ServiceName not found" }
if ($service.PathName -notlike "*$AppRoot*") { throw "Service runs from a different folder: $($service.PathName)" }
Say "Service: $ServiceName ($($service.State)) from $AppRoot"

# 1. Verify installed and new file versions before anything changes.
$toCopy = @()
foreach ($name in $Files.Keys) {
    $installed = Join-Path $AppRoot $name
    $incoming = Join-Path $here $name
    if (-not (Test-Path -LiteralPath $incoming)) { throw "New $name is missing next to this script" }
    $newHash = Get-NormalizedHash $incoming
    if ($newHash -ne $Files[$name].New) { throw "New $name does not match the reviewed update ($newHash)" }
    $current = Get-NormalizedHash $installed
    if ($current -eq $Files[$name].New) { Say "  $name : already updated" 'Green' }
    elseif ($current -eq $Files[$name].Old) { Say "  $name : reviewed old version, will be replaced"; $toCopy += $name }
    else { throw "Installed $name is an unexpected version ($current). Stopping without changes." }
}

# 2. Config: optional settings, each appended only when absent from local_config.py.
$configText = [IO.File]::ReadAllText($config)
$settings = [ordered]@{}
if ($IgnoreDevices.Count) {
    foreach ($d in $IgnoreDevices) { if ($d -notmatch '^FP\d_DEVICE_\d{2,3}$') { throw "Not a device_id: $d" } }
    $settings['SHIFT_SYNC_IGNORED_DEVICES'] = '[' + (($IgnoreDevices | ForEach-Object { "'$_'" }) -join ', ') + ']'
}
if ($RetryGraceHours -ge 0) {
    $settings['SHIFT_SYNC_RETRY_GRACE_HOURS'] = $RetryGraceHours.ToString([Globalization.CultureInfo]::InvariantCulture)
}
$configLines = @()
foreach ($name in $settings.Keys) {
    if ($configText -match "(?m)^\s*$name\s*=") { Say "  local_config.py already defines $name; edit it by hand if needed." 'Yellow' }
    else { $configLines += "$name = $($settings[$name])"; Say "  local_config.py : will append  $name = $($settings[$name])" }
}
$configLine = if ($configLines.Count) { $configLines -join "`r`n" } else { $null }

if (-not $Apply) {
    Say "`nDry run only. Nothing was changed. Re-run with -Apply to update." 'Cyan'
    return
}
if (-not $toCopy.Count -and -not $configLine) { Say "`nNothing to do." 'Green'; return }

# 3. Backup.
$backup = Join-Path $AppRoot ('backup-shift-sync-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($name in @($Files.Keys) + 'local_config.py') { Copy-Item -LiteralPath (Join-Path $AppRoot $name) -Destination $backup }
Say "Backup: $backup"

function Restore-Backup {
    Say 'Restoring backup...' 'Yellow'
    Stop-Service $ServiceName -ErrorAction SilentlyContinue
    [void](Wait-ServiceState 'Stopped' 120)
    foreach ($name in @($Files.Keys) + 'local_config.py') { Copy-Item -LiteralPath (Join-Path $backup $name) -Destination $AppRoot -Force }
    Start-Service $ServiceName -ErrorAction SilentlyContinue
    Say "Restored. Service state: $((Get-Service $ServiceName).Status)" 'Yellow'
}

# 4. Stop, replace, start; roll back on any failure.
$started = Get-Date
try {
    Say 'Stopping service...'
    Stop-Service $ServiceName
    if (-not (Wait-ServiceState 'Stopped' 120)) { throw 'Service did not stop within 2 minutes' }
    foreach ($name in $toCopy) {
        Copy-Item -LiteralPath (Join-Path $here $name) -Destination (Join-Path $AppRoot $name) -Force
        if ((Get-NormalizedHash (Join-Path $AppRoot $name)) -ne $Files[$name].New) { throw "Copy of $name failed verification" }
        Say "  replaced $name" 'Green'
    }
    if ($configLine) {
        $sep = if ($configText.EndsWith("`n")) { '' } else { "`r`n" }
        [IO.File]::AppendAllText($config, "$sep`r`n# Shift Type sync policy (Update-ShiftSyncPolicy.ps1, $(Get-Date -Format 'yyyy-MM-dd')).`r`n$configLine`r`n", [Text.UTF8Encoding]::new($false))
        Say '  updated local_config.py' 'Green'
    }
    Say 'Starting service...'
    Start-Service $ServiceName
    if (-not (Wait-ServiceState 'Running' 60)) { throw 'Service did not start within 1 minute' }
    Start-Sleep -Seconds 20
    if ((Get-Service $ServiceName).Status -ne 'Running') { throw 'Service stopped shortly after starting' }
    Say "Service is running with the update." 'Green'
} catch {
    Say "Update failed: $($_.Exception.Message)" 'Red'
    Restore-Backup
    throw
}

# 5. Optional: wait for the first shift_watermark log line from the new code.
$logDir = [regex]::Match($configText, "(?m)^\s*LOGS_DIRECTORY\s*=\s*r?['""]([^'""]+)['""]").Groups[1].Value
if (-not $logDir) { $logDir = Join-Path $AppRoot 'logs' } elseif (-not [IO.Path]::IsPathRooted($logDir)) { $logDir = Join-Path $AppRoot $logDir }
$log = Join-Path $logDir 'logs.log'
Say "Log to watch: $log  (look for ""event"": ""shift_watermark"")"
if ($WaitMinutes -gt 0) {
    $deadline = (Get-Date).AddMinutes($WaitMinutes)
    while ((Get-Date) -lt $deadline) {
        $line = Get-Content -LiteralPath $log -Tail 400 -Encoding UTF8 -ErrorAction SilentlyContinue |
            Where-Object { $_ -match '"event": "shift_watermark"' } | Select-Object -Last 1
        if ($line -and [datetime]::ParseExact($line.Substring(0, 19), 'yyyy-MM-dd HH:mm:ss', $null) -ge $started) {
            Say $line 'Green'
            $sent = Get-Content -LiteralPath $log -Tail 400 -Encoding UTF8 | Where-Object { $_ -match 'Shift Type last_sync_of_checkin Updated' } | Select-Object -Last 3
            if ($sent) { $sent | ForEach-Object { Say $_ 'Green' } }
            return
        }
        Start-Sleep -Seconds 15
    }
    Say "No shift_watermark line within $WaitMinutes minutes; check $log and error.log." 'Yellow'
}
