<#
.SYNOPSIS
  In-place update of an existing durable sync installation: Shift Type
  watermark policy (wait only for punches that can still arrive) and the
  verified-absence resend of uncertain sends.

.DESCRIPTION
  Replaces service files (durable_sync.py, sync_ledger.py, durable_delivery.py)
  only when each installed copy is a reviewed version this update knows; files
  already at the new version are left alone. Without -Apply it only reports.

  With -Apply it: backs up the files and local_config.py, sets the requested
  settings in local_config.py (an existing line is replaced in place, otherwise
  the setting is appended), stops the service, copies the files, starts the
  service and rolls everything back if the service does not return to Running.
  The SQLite ledger, logs and devices are never touched.

  -ResendMissing sets DURABLE_CONFIRM_READS_VERIFIED, DURABLE_SERVER_REVIEW_REFERENCE
  and DURABLE_RESEND_VERIFIED_MISSING so uncertain sends are looked up in ERPNext
  and resent only when proven absent.

  Run from an elevated Windows PowerShell 5.1 in the folder that holds this
  script together with the new service files.

.EXAMPLE
  .\Update-ShiftSyncPolicy.ps1
  .\Update-ShiftSyncPolicy.ps1 -IgnoreDevices FP1_DEVICE_06 -Apply
  .\Update-ShiftSyncPolicy.ps1 -ResendMissing -Apply -WaitMinutes 15
#>
[CmdletBinding()]
param(
    [string]$AppRoot = "$env:ProgramFiles\FPFBiometricSyncPhase2",
    [string]$ServiceName = 'ERPNextBiometricPushService',
    [string[]]$IgnoreDevices = @(),
    [ValidateRange(0, 168)][double]$RetryGraceHours = -1,
    [switch]$ResendMissing,
    [ValidatePattern('^[A-Za-z0-9 :.,_-]{3,120}$')][string]$ReviewReference = 'Approved 2026-11-21: verified-absence resend of uncertain sends',
    [switch]$Apply,
    [int]$WaitMinutes = 0
)
$ErrorActionPreference = 'Stop'

# SHA-256 of LF-normalized bytes. OLD = reviewed versions this update may replace
# (unchanged since commit 4f20179, 2026-10-21); NEW = this update.
$Files = [ordered]@{
    'durable_sync.py'     = @{ Old = @('50becdebeb006b2f150ddff72dafac940201bc4d33ed52a5669f07f7aa18923f')
                               New = 'e1685346fed20e29d3b9de241078b92648d952c98c6f5401b5909f9f3e75d479' }
    'sync_ledger.py'      = @{ Old = @('5e6713142f432b808c7bba50a5f55798733c5c981657e381f09576fde7bc7a2d')
                               New = '422687e5c781d24474ad505d030c4ccef260ae78876348dafa5e5b97f20b7721' }
    'durable_delivery.py' = @{ Old = @('ceeb65c3161de7d2ac4fd19f1544c3351f8d658e6fddf7fd640e90f49e31a82b')
                               New = '7587b2dd85d08e506339059d4b489edd9d12d8b5892a16e0f00c4d3304c407d8' }
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
    elseif ($Files[$name].Old -contains $current) { Say "  $name : reviewed old version, will be replaced"; $toCopy += $name }
    else { throw "Installed $name is an unexpected version ($current). Stopping without changes." }
}

# 2. Config: requested settings; an existing single-line assignment is replaced in place.
$configBytes = [IO.File]::ReadAllBytes($config)
$hasBom = $configBytes.Length -ge 3 -and $configBytes[0] -eq 0xEF -and $configBytes[1] -eq 0xBB -and $configBytes[2] -eq 0xBF
$configText = [IO.File]::ReadAllText($config)
$settings = [ordered]@{}
if ($IgnoreDevices.Count) {
    foreach ($d in $IgnoreDevices) { if ($d -notmatch '^FP\d_DEVICE_\d{2,3}$') { throw "Not a device_id: $d" } }
    $settings['SHIFT_SYNC_IGNORED_DEVICES'] = '[' + (($IgnoreDevices | ForEach-Object { "'$_'" }) -join ', ') + ']'
}
if ($RetryGraceHours -ge 0) {
    $settings['SHIFT_SYNC_RETRY_GRACE_HOURS'] = $RetryGraceHours.ToString([Globalization.CultureInfo]::InvariantCulture)
}
if ($ResendMissing) {
    $settings['DURABLE_CONFIRM_READS_VERIFIED'] = 'True'
    $settings['DURABLE_SERVER_REVIEW_REFERENCE'] = "'$ReviewReference'"
    $settings['DURABLE_RESEND_VERIFIED_MISSING'] = 'True'
}
$newConfig = $configText
$appendLines = @()
foreach ($name in $settings.Keys) {
    $line = "$name = $($settings[$name])"
    $pattern = "(?m)^[ \t]*$name[ \t]*=[^\r\n]*"
    $existing = [regex]::Matches($newConfig, $pattern)
    if ($existing.Count -gt 1) { throw "local_config.py assigns $name more than once; fix it by hand first." }
    if ($existing.Count -eq 1) {
        $old = $existing[0].Value.Trim()
        if ($old -eq $line) { Say "  local_config.py : $name already set" 'Green'; continue }
        if ($old -match '[\(\[\{]\s*$' -or $old -match '\\\s*$') { throw "local_config.py: $name spans several lines; edit it by hand." }
        $newConfig = [regex]::Replace($newConfig, $pattern, $line.Replace('$', '$$'))
        Say "  local_config.py : will change  $old  ->  $line"
    } else {
        $appendLines += $line
        Say "  local_config.py : will append  $line"
    }
}
if ($appendLines.Count) {
    $sep = if ($newConfig.EndsWith("`n")) { '' } else { "`r`n" }
    $newConfig += "$sep`r`n# Sync policy (Update-ShiftSyncPolicy.ps1, $(Get-Date -Format 'yyyy-MM-dd')).`r`n" + ($appendLines -join "`r`n") + "`r`n"
}
$configLine = if ($newConfig -ne $configText) { $newConfig } else { $null }

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
        [IO.File]::WriteAllText($config, $configLine, [Text.UTF8Encoding]::new($hasBom))
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
