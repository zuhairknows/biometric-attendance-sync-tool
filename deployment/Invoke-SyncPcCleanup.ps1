<#
.SYNOPSIS
  Archive and remove leftovers from earlier sync installs on the sync PC.

.DESCRIPTION
  Moves old checkouts, backups, evidence folders and diagnostic bundles to a
  protected archive (default D:\FPF-Biometric-Archive), then deletes them from
  their original place. Without -Apply it only reports what it would do.

  Never touched: the running service's folder, Python, local_config.py, ledger and
  log folder, the dashboard snapshot folder and its scheduled task, and the base
  Python. They are read from the service command and the service's own config. A
  leftover that is, or contains, any of them is skipped with the reason. The
  service is not stopped.

  Each item is copied with robocopy, then checked (same number of files and bytes)
  before the original is deleted. The archive is readable by SYSTEM and
  Administrators only, because old configs hold the ERPNext API key. A manifest
  (CSV) of what was archived is written next to it.

  Run from an elevated Windows PowerShell 5.1:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-SyncPcCleanup.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-SyncPcCleanup.ps1 -Apply
#>
[CmdletBinding()]
param(
    [string]$ArchiveRoot = 'D:\FPF-Biometric-Archive',
    [string]$ServiceName = 'ERPNextBiometricPushService',
    [string]$MonitorRoot = "$env:ProgramData\FPFBiometricMonitor",
    # Where to look; the defaults are the sync PC's. Changed only for testing.
    [string]$GitHubRoot = 'C:\GitHub',
    [string]$ProgramFilesRoot = $env:ProgramFiles,
    [string]$ProgramDataRoot = $env:ProgramData,
    [string]$UsersRoot = 'C:\Users',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
function Say([string]$Text, [string]$Color = 'Gray') { Write-Host $Text -ForegroundColor $Color }
function Get-FullPath([string]$Path) { [IO.Path]::GetFullPath($Path).TrimEnd('\') }
function Test-Within([string]$Child, [string]$Parent) {
    $Child -ieq $Parent -or $Child.StartsWith($Parent + '\', [StringComparison]::OrdinalIgnoreCase)
}
function Get-Size([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) { return [pscustomobject]@{ Files = 1; Bytes = [int64]$item.Length } }
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue)
    [pscustomobject]@{ Files = $files.Count; Bytes = [int64](($files | Measure-Object Length -Sum).Sum) }
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this from an elevated PowerShell (Run as Administrator).' }

# 1. What is live, from the service itself.
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $service) { throw "Service $ServiceName not found; refusing to guess what is live." }
$m = [regex]::Match([string]$service.PathName, '^"(?<py>[^"]+\\python\.exe)"(?: -\w+)* "(?<script>[^"]+\\erpnext_sync_win\.py)"')
if (-not $m.Success) { throw "Unrecognized service command: $($service.PathName)" }
$python = $m.Groups['py'].Value
$appRoot = Split-Path -Parent $m.Groups['script'].Value
$config = Join-Path $appRoot 'local_config.py'
if (-not (Test-Path -LiteralPath $config)) { throw "Service config not found: $config" }
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
# Windows PowerShell 5.1 does not escape double quotes in native arguments: single quotes only.
$probe = 'import json, runpy, sys; c = runpy.run_path(sys.argv[1]); print(json.dumps(dict(logs=c.get(''LOGS_DIRECTORY'', ''logs''), db=c.get(''DURABLE_DB_PATH'', ''''))))'
# Windows PowerShell 5.1 turns any stderr line from a program into an error; judge by exit code.
$ErrorActionPreference = 'Continue'
$out = & $python -I -X utf8 -c $probe $config 2>$null
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($code -ne 0) { throw 'Could not read the service config with the service Python' }
$values = $out | Out-String | ConvertFrom-Json
$logs = [string]$values.logs
if (-not [IO.Path]::IsPathRooted($logs)) { $logs = Join-Path $appRoot $logs }
$db = [string]$values.db
if (-not $db) { $db = Join-Path $env:ProgramData 'ERPNextBiometricPushService\sync.sqlite3' }
$live = [ordered]@{
    'service folder'   = $appRoot
    'service Python'   = Split-Path -Parent (Split-Path -Parent $python)
    'ledger'           = $db
    'log folder'       = $logs
    'dashboard data'   = $MonitorRoot
}
$cfg = Join-Path (Split-Path -Parent (Split-Path -Parent $python)) 'pyvenv.cfg'
if (Test-Path -LiteralPath $cfg) {
    $home_ = ((Get-Content -LiteralPath $cfg | Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1) -replace '^\s*home\s*=\s*', '').Trim()
    if ($home_) { $live['base Python'] = $home_ }
}
foreach ($task in @(Get-ScheduledTask -ErrorAction SilentlyContinue)) {
    foreach ($a in $task.Actions) {
        foreach ($p in [regex]::Matches(($a.Execute + ' ' + $a.Arguments), '"([A-Za-z]:\\[^"]+)"|([A-Za-z]:\\[^\s"]+)')) {
            $value = if ($p.Groups[1].Success) { $p.Groups[1].Value } else { $p.Groups[2].Value }
            if ($value -match 'biometric|erpnext|FPF') { $live["task $($task.TaskName)"] = Split-Path -Parent $value }
        }
    }
}
$livePaths = @($live.Values | ForEach-Object { Get-FullPath $_ })
Say "Service $ServiceName ($($service.State)) runs from $appRoot"
Say 'Live, never touched:'
foreach ($k in $live.Keys) { Say ("  {0,-16} {1}" -f $k, $live[$k]) }

# 2. Leftovers from earlier installs (2026-09 to 2026-11).
$candidates = New-Object System.Collections.Generic.List[string]
foreach ($p in @(
        "$GitHubRoot\biometric-attendance-sync-tool", "$GitHubRoot\biometric-sync-manager-29a440c",
        "$GitHubRoot\biometric-sync-service-phase2", "$ProgramFilesRoot\FPF Biometric Sync",
        "$ProgramDataRoot\BiometricAttendanceSync", "$ProgramDataRoot\ERPNextBiometricBackups",
        "$ProgramDataRoot\ERPNextBiometricConfirmation", "$ProgramDataRoot\ERPNextBiometricPushService-Backups",
        "$ProgramDataRoot\FPF-Biometric-Capture", (Join-Path (Split-Path -Parent $db) 'backups'))) { $candidates.Add($p) }
foreach ($g in @("$ProgramDataRoot\FPF-Diagnostic-*", (Join-Path $appRoot 'backup-shift-sync-*'))) {
    Get-Item -Path $g -Force -ErrorAction SilentlyContinue | ForEach-Object { $candidates.Add($_.FullName) }
}
foreach ($userDir in @(Get-ChildItem $UsersRoot -Directory -Force -ErrorAction SilentlyContinue)) {
    foreach ($g in 'AppData\Local\BiometricPhase1OfflineTest', 'AppData\Local\FPF-Phase1-Staging-*',
                   'Desktop\biometric-diag-*', 'Desktop\monitor-dashboard-*', 'Desktop\monitor-snapshot-*') {
        Get-Item -Path (Join-Path $userDir.FullName $g) -Force -ErrorAction SilentlyContinue | ForEach-Object { $candidates.Add($_.FullName) }
    }
}

$archiveFull = Get-FullPath $ArchiveRoot
$plan = @()
Say ''
Say 'Leftovers:'
foreach ($path in $candidates) {
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $full = Get-FullPath $path
    $blocked = @($live.Keys | Where-Object { Test-Within (Get-FullPath $live[$_]) $full })
    if ($blocked.Count) { Say ("  SKIP {0}: holds the live {1}" -f $full, ($blocked -join ', ')) 'Yellow'; continue }
    if ((Test-Within $full $archiveFull) -or (Test-Within $archiveFull $full)) { Say "  SKIP ${full}: overlaps the archive" 'Yellow'; continue }
    $size = Get-Size $full
    $label = ($full -replace '^([A-Za-z]):', '$1') -replace '[\\:]', '_'
    $plan += [pscustomobject]@{ Source = $full; Target = Join-Path $archiveFull $label; Files = $size.Files; Bytes = $size.Bytes; Status = 'planned' }
    Say ("  {0}  ({1:N0} files, {2:N1} MB)" -f $full, $size.Files, ($size.Bytes / 1MB))
}
$total = ($plan | Measure-Object Bytes -Sum).Sum
Say ("Total: {0} items, {1:N1} GB to archive in {2}" -f $plan.Count, ($total / 1GB), $archiveFull) 'Cyan'
if (-not $plan.Count) { Say 'Nothing to clean.' 'Green'; return }

$drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($archiveFull))
if (-not $drive.IsReady -or $drive.DriveType -ne 'Fixed' -or $drive.DriveFormat -ne 'NTFS') { throw "Archive drive $($drive.Name) must be a local NTFS disk" }
if ($drive.AvailableFreeSpace -lt $total * 1.1 + 1GB) { throw ("Not enough space on {0}: {1:N1} GB free" -f $drive.Name, ($drive.AvailableFreeSpace / 1GB)) }

if (-not $Apply) { Say "`nDry run only. Nothing was changed. Re-run with -Apply to archive and remove these." 'Cyan'; return }

# 3. Protected archive: SYSTEM and Administrators only; copies inherit it.
if (-not (Test-Path -LiteralPath $archiveFull)) { New-Item -ItemType Directory -Path $archiveFull | Out-Null }
& icacls.exe $archiveFull /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect the archive folder' }
$manifest = Join-Path $archiveFull ('manifest-{0}.csv' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

foreach ($item in $plan) {
    try {
        $source = Get-Item -LiteralPath $item.Source -Force
        if ($source.PSIsContainer) {
            & robocopy.exe $item.Source $item.Target /E /COPY:DAT /DCOPY:T /R:1 /W:1 /XJ /NP /NFL /NDL /NJH /NJS | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
        } else {
            New-Item -ItemType Directory -Path (Split-Path -Parent $item.Target) -Force | Out-Null
            Copy-Item -LiteralPath $item.Source -Destination $item.Target -Force
        }
        $copy = Get-Size $item.Target
        if ($copy.Files -ne $item.Files -or $copy.Bytes -ne $item.Bytes) {
            throw ("copy incomplete: {0} of {1} files, {2} of {3} bytes" -f $copy.Files, $item.Files, $copy.Bytes, $item.Bytes)
        }
        Remove-Item -LiteralPath $item.Source -Recurse -Force
        $item.Status = 'archived'
        Say ("  archived {0}" -f $item.Source) 'Green'
    } catch {
        $item.Status = 'failed: ' + $_.Exception.Message
        Say ("  FAILED {0}: {1} (original kept)" -f $item.Source, $_.Exception.Message) 'Red'
    }
}
if ((Test-Path -LiteralPath $GitHubRoot) -and -not @(Get-ChildItem -LiteralPath $GitHubRoot -Force).Count) { Remove-Item -LiteralPath $GitHubRoot -Force; Say "  removed empty $GitHubRoot" 'Green' }
$plan | Select-Object Source, Target, Files, Bytes, Status | Export-Csv -LiteralPath $manifest -NoTypeInformation -Encoding UTF8
$failed = @($plan | Where-Object { $_.Status -ne 'archived' }).Count
Say ("Done: {0} archived, {1} failed. Manifest: {2}" -f ($plan.Count - $failed), $failed, $manifest) $(if ($failed) { 'Yellow' } else { 'Green' })
Say ("Free space on C: now {0:N1} GB" -f ([IO.DriveInfo]::new('C').AvailableFreeSpace / 1GB))
