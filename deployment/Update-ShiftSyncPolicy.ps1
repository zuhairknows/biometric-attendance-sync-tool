<#
.SYNOPSIS
  In-place update of an existing durable sync installation: Shift Type
  watermark policy (wait only for punches that can still arrive), the
  verified-absence resend of uncertain sends and the re-check of cards that had
  no Employee (resent once the Employee exists).

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

  -RecheckDays sets UNMATCHED_RECHECK_DAYS (0 turns the unmatched-card re-check off).

  -AddShifts adds Shift Types to the first shift_type_device_mapping entry. The
  config is loaded with the service's Python and one literal
  shift_type_device_mapping line is appended; it replaces the earlier definition.

  -MoveLogsTo copies the log folder (logs and status.json) to a new folder readable
  only by SYSTEM and Administrators, while the service is stopped, and points
  LOGS_DIRECTORY there. The old folder is left in place; a rollback restores the
  old setting.

  Run from an elevated Windows PowerShell 5.1 in the folder that holds this
  script together with the new service files.

.EXAMPLE
  .\Update-ShiftSyncPolicy.ps1
  .\Update-ShiftSyncPolicy.ps1 -IgnoreDevices FP1_DEVICE_06 -Apply
  .\Update-ShiftSyncPolicy.ps1 -ResendMissing -Apply -WaitMinutes 15
  .\Update-ShiftSyncPolicy.ps1 -ClearIgnoredDevices -RecheckDays 60 -AddShifts Test -MoveLogsTo C:\ProgramData\ERPNextBiometricPushService-Logs -Apply
#>
[CmdletBinding()]
param(
    [string]$AppRoot = "$env:ProgramFiles\FPFBiometricSyncPhase2",
    [string]$ServiceName = 'ERPNextBiometricPushService',
    [string[]]$IgnoreDevices = @(),
    [switch]$ClearIgnoredDevices,
    [ValidateRange(0, 168)][double]$RetryGraceHours = -1,
    [switch]$ResendMissing,
    [ValidateRange(-1, 365)][int]$RecheckDays = -1,
    [string[]]$AddShifts = @(),
    [string]$MoveLogsTo = '',
    [ValidatePattern('^[A-Za-z0-9 :.,_-]{3,120}$')][string]$ReviewReference = 'Approved 2026-11-21: verified-absence resend of uncertain sends',
    [switch]$Apply,
    [int]$WaitMinutes = 0
)
$ErrorActionPreference = 'Stop'

# SHA-256 of LF-normalized bytes. OLD = reviewed versions this update may replace
# (commit 4f20179, 2026-10-21, and the shift-sync/resend update of 2026-11-21);
# NEW = this update (unmatched-card re-check, 2026-11-22).
$Files = [ordered]@{
    'durable_sync.py'     = @{ Old = @('50becdebeb006b2f150ddff72dafac940201bc4d33ed52a5669f07f7aa18923f',
                                       'e1685346fed20e29d3b9de241078b92648d952c98c6f5401b5909f9f3e75d479')
                               New = 'c3884dddbd7709bda6d48eb42bec490f3c50fcc4ee75c7ee33f3ece64bf53719' }
    'sync_ledger.py'      = @{ Old = @('5e6713142f432b808c7bba50a5f55798733c5c981657e381f09576fde7bc7a2d',
                                       '422687e5c781d24474ad505d030c4ccef260ae78876348dafa5e5b97f20b7721')
                               New = 'a165553f6b44b23fb3cba0a4dfd6c1c0aac34c217bf5843a07c1967ec81cd274' }
    'durable_delivery.py' = @{ Old = @('ceeb65c3161de7d2ac4fd19f1544c3351f8d658e6fddf7fd640e90f49e31a82b',
                                       '7587b2dd85d08e506339059d4b489edd9d12d8b5892a16e0f00c4d3304c407d8')
                               New = 'd72bce59ccb249c46e2797b064d95c3ceba704b70f74e854f03a8d7f93d6dfd6' }
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
if ($IgnoreDevices.Count -and $ClearIgnoredDevices) { throw "Use -IgnoreDevices or -ClearIgnoredDevices, not both" }
if ($IgnoreDevices.Count -or $ClearIgnoredDevices) {
    foreach ($d in $IgnoreDevices) { if ($d -notmatch '^FP\d_DEVICE_\d{2,3}$') { throw "Not a device_id: $d" } }
    $settings['SHIFT_SYNC_IGNORED_DEVICES'] = '[' + ((@($IgnoreDevices) | ForEach-Object { "'$_'" }) -join ', ') + ']'
}
if ($RetryGraceHours -ge 0) {
    $settings['SHIFT_SYNC_RETRY_GRACE_HOURS'] = $RetryGraceHours.ToString([Globalization.CultureInfo]::InvariantCulture)
}
if ($ResendMissing) {
    $settings['DURABLE_CONFIRM_READS_VERIFIED'] = 'True'
    $settings['DURABLE_SERVER_REVIEW_REFERENCE'] = "'$ReviewReference'"
    $settings['DURABLE_RESEND_VERIFIED_MISSING'] = 'True'
}
if ($RecheckDays -ge 0) { $settings['UNMATCHED_RECHECK_DAYS'] = [string]$RecheckDays }

# Current values, read with the service's own Python (the config is the service's trusted file).
$python = [regex]::Match([string]$service.PathName, '^"([^"]+python\.exe)"').Groups[1].Value
if (-not $python) { throw 'Cannot find the service Python in the service command' }
# Python prints UTF-8 (-X utf8); decode it as such or Arabic shift names are garbled.
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
# Windows PowerShell 5.1 does not escape double quotes in native arguments: single quotes only.
$probe = 'import json, runpy, sys; c = runpy.run_path(sys.argv[1]); print(json.dumps(dict(logs=c.get(''LOGS_DIRECTORY'', ''logs''), mapping=c.get(''shift_type_device_mapping'', []))))'
function Invoke-ServicePython([string[]]$Arguments) {
    # Windows PowerShell 5.1 turns any stderr line from a program into an error; judge by exit code.
    $ErrorActionPreference = 'Continue'
    $out = & $python -I -X utf8 @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) { throw "The service Python failed (exit $LASTEXITCODE)" }
    ($out | Out-String).Trim()
}
$current = Invoke-ServicePython (@('-c', $probe, $config)) | ConvertFrom-Json
if (-not $current) { throw 'Could not read local_config.py with the service Python' }

$oldLogs = [string]$current.logs
if (-not [IO.Path]::IsPathRooted($oldLogs)) { $oldLogs = Join-Path $AppRoot $oldLogs }
$moveLogs = $false
if ($MoveLogsTo) {
    if (-not [IO.Path]::IsPathRooted($MoveLogsTo) -or $MoveLogsTo -match "['`"]") { throw "MoveLogsTo must be an absolute path without quotes: $MoveLogsTo" }
    $MoveLogsTo = [IO.Path]::GetFullPath($MoveLogsTo).TrimEnd('\')
    if ($MoveLogsTo -eq [IO.Path]::GetFullPath($oldLogs).TrimEnd('\')) { Say "  logs : already in $MoveLogsTo" 'Green' }
    else {
        if (-not (Test-Path -LiteralPath $oldLogs)) { throw "Current log folder not found: $oldLogs" }
        if ((Test-Path -LiteralPath $MoveLogsTo) -and @(Get-ChildItem -LiteralPath $MoveLogsTo -Force).Count) { throw "$MoveLogsTo exists and is not empty" }
        $moveLogs = $true
        $settings['LOGS_DIRECTORY'] = "r'$MoveLogsTo'"
        $size = (Get-ChildItem -LiteralPath $oldLogs -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
        Say ("  logs : will copy {0} ({1:N0} MB) to {2}; the old folder is kept" -f $oldLogs, ($size / 1MB), $MoveLogsTo)
    }
}

$mappingLine = $null
if ($AddShifts.Count) {
    $mapping = @($current.mapping)
    if (-not $mapping.Count) { throw 'local_config.py has no shift_type_device_mapping to add to' }
    $mapped = @($mapping | ForEach-Object { @($_.shift_type_name) })
    $missing = @($AddShifts | Where-Object { $mapped -notcontains $_ })
    if ($missing.Count) {
        $build = 'import runpy, sys; c = runpy.run_path(sys.argv[1]); m = c[''shift_type_device_mapping'']; n = m[0][''shift_type_name'']; m[0][''shift_type_name''] = ([n] if isinstance(n, str) else list(n)) + sys.argv[2:]; print(''shift_type_device_mapping = '' + repr(m))'

        $mappingLine = Invoke-ServicePython (@('-c', $build, $config) + $missing)
        if (-not $mappingLine.StartsWith('shift_type_device_mapping = [')) { throw 'Could not build the new shift mapping' }
        Say ("  local_config.py : will add {0} to the shift mapping (same devices as the first entry)" -f ($missing -join ', '))
    } else { Say ("  local_config.py : {0} already mapped" -f ($AddShifts -join ', ')) 'Green' }
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
if ($mappingLine) {
    $sep = if ($newConfig.EndsWith("`n")) { '' } else { "`r`n" }
    $newConfig += "$sep`r`n# Shift mapping with $($AddShifts -join ', ') added (Update-ShiftSyncPolicy.ps1, $(Get-Date -Format 'yyyy-MM-dd')); replaces the definition above.`r`n$mappingLine`r`n"
}
$configLine = if ($newConfig -ne $configText) { $newConfig } else { $null }

if (-not $Apply) {
    Say "`nDry run only. Nothing was changed. Re-run with -Apply to update." 'Cyan'
    return
}
if (-not $toCopy.Count -and -not $configLine -and -not $moveLogs) { Say "`nNothing to do." 'Green'; return }

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
    if ($moveLogs) { Say "Logs stay in $oldLogs; the copy in $MoveLogsTo can be deleted." 'Yellow' }
}

# 4. Stop, replace, start; roll back on any failure.
$started = Get-Date
try {
    Say 'Stopping service...'
    Stop-Service $ServiceName
    if (-not (Wait-ServiceState 'Stopped' 120)) { throw 'Service did not stop within 2 minutes' }
    if ($moveLogs) {
        if (-not (Test-Path -LiteralPath $MoveLogsTo)) { New-Item -ItemType Directory -Path $MoveLogsTo | Out-Null }
        # SYSTEM and Administrators only: logs name devices and employees.
        & icacls.exe $MoveLogsTo /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not set permissions on the new log folder' }
        & robocopy.exe $oldLogs $MoveLogsTo /E /COPY:DAT /DCOPY:T /R:1 /W:1 /NP /NFL /NDL /NJH /NJS | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copying logs failed (robocopy $LASTEXITCODE)" }
        $a = @(Get-ChildItem -LiteralPath $oldLogs -Recurse -File -Force).Count
        $b = @(Get-ChildItem -LiteralPath $MoveLogsTo -Recurse -File -Force).Count
        if ($a -ne $b) { throw "Log copy incomplete ($b of $a files)" }
        Say "  copied $b log files to $MoveLogsTo" 'Green'
    }
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
$logDir = if ($moveLogs) { $MoveLogsTo } else { $oldLogs }
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
