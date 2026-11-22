<#
.SYNOPSIS
  Keeps a refreshed, read-only copy of the sync ledger for the monitoring dashboard
  on the sync PC, without changing the service, its folder or the ledger ACLs.

.DESCRIPTION
  The live ledger folder is private on purpose: the service refuses to start if any
  other account can read it. Instead, a scheduled task running as SYSTEM copies the
  ledger every -IntervalMinutes (SQLite online backup from a read-only connection;
  the service keeps running) into <Root>\snapshot, together with a secret-free
  local_config.monitor.py and refresh-status.json.

  <Root> gets a protected ACL: SYSTEM and Administrators full control, -Reader read
  only. The refresh helper is copied to <Root>\bin, writable by administrators only,
  because SYSTEM runs it. The task uses the service's own Python (-I).

  The service's Python and local_config.py are read from the registered service
  command ("<python.exe>" -B "<code>\erpnext_sync_win.py"), so both the manual
  Phase 2 layout (<AppRoot>\.venv, <AppRoot>\local_config.py) and the portable
  installer's layout (<AppRoot>\<slot>\runtime, <AppRoot>\<slot>\code) work.
  -AppRoot forces the manual Phase 2 layout instead.

  Without -Apply it only reports what it would do. -Remove deletes the task and
  <Root>. Run from an elevated Windows PowerShell 5.1.

.EXAMPLE
  .\Install-MonitorSnapshotTask.ps1
  .\Install-MonitorSnapshotTask.ps1 -Apply
  .\Install-MonitorSnapshotTask.ps1 -Remove -Apply
#>
[CmdletBinding()]
param(
    [string]$ServiceName = 'ERPNextBiometricPushService',
    [string]$AppRoot,
    [string]$Reader,
    [ValidateRange(5, 60)][int]$IntervalMinutes = 10,
    [string]$Root = "$env:ProgramData\FPFBiometricMonitor",
    [switch]$Remove,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$TaskName = 'FPF Biometric Monitor Snapshot'
function Say($Text, $Color = 'Gray') { Write-Host $Text -ForegroundColor $Color }

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this from an elevated PowerShell (Run as administrator).'
}
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($Remove) {
    Say "Scheduled task '$TaskName': $(if ($task) { 'will be removed' } else { 'not present' })"
    Say "Folder $Root : $(if (Test-Path -LiteralPath $Root) { 'will be deleted' } else { 'not present' })"
    if (-not $Apply) { Say 'Dry run: nothing changed. Add -Apply to remove.' 'Yellow'; return }
    if ($task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force -Confirm:$false }
    Say 'Removed. The dashboard (if installed) will report the ledger as missing.' 'Green'
    return
}

if ($AppRoot) {
    $config = Join-Path $AppRoot 'local_config.py'
    $python = Join-Path $AppRoot '.venv\Scripts\python.exe'
} else {
    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
    if (-not $service) { throw "Service $ServiceName is not installed; pass -AppRoot for a manual Phase 2 folder." }
    $m = [regex]::Match([string]$service.PathName, '^"(?<py>[^"]+\\python\.exe)" (?:-\w+ )*"(?<code>[^"]+)\\erpnext_sync_win\.py"$')
    $phase2 = "$env:ProgramFiles\FPFBiometricSyncPhase2"
    if ($m.Success) {
        $python = $m.Groups['py'].Value
        $config = Join-Path $m.Groups['code'].Value 'local_config.py'
    } elseif (Test-Path -LiteralPath (Join-Path $phase2 'local_config.py')) {
        $config = Join-Path $phase2 'local_config.py'
        $python = Join-Path $phase2 '.venv\Scripts\python.exe'
    } else { throw "Unrecognized service command: $($service.PathName). Pass -AppRoot." }
}
$helperSource = Join-Path $PSScriptRoot 'monitor_snapshot_refresh.py'
foreach ($p in $config, $python, $helperSource) { if (-not (Test-Path -LiteralPath $p)) { throw "Not found: $p" } }

if (-not $Reader) { $Reader = (Get-CimInstance Win32_ComputerSystem).UserName }
if (-not $Reader) { throw 'No user is signed in at the console; pass -Reader DOMAIN\user.' }
$readerSid = ([Security.Principal.NTAccount]$Reader).Translate([Security.Principal.SecurityIdentifier])
if ($readerSid.Value -notmatch '^S-1-5-21-') { throw "$Reader ($readerSid) is not an ordinary user account." }

$bin = Join-Path $Root 'bin'; $snapshot = Join-Path $Root 'snapshot'
$helper = Join-Path $bin 'monitor_snapshot_refresh.py'
Say "Reader (read-only access to $Root): $Reader  $readerSid"
Say "Service configuration: $config"
Say "Refresh every $IntervalMinutes minutes as SYSTEM: $python -I $helper"
Say "Snapshot: $snapshot\sync.sqlite3 and local_config.monitor.py"
Say "Scheduled task '$TaskName': $(if ($task) { 'will be replaced' } else { 'will be created' })"
Say 'The service, its folder, the live ledger and its ACLs are not changed.'
if (-not $Apply) { Say 'Dry run: nothing changed. Add -Apply to install.' 'Yellow'; return }

# Protected ACL on the root; bin and snapshot inherit it.
if (-not (Test-Path -LiteralPath $Root)) { New-Item -ItemType Directory -Path $Root | Out-Null }
$inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$none = [Security.AccessControl.PropagationFlags]::None
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetOwner([Security.Principal.SecurityIdentifier]'S-1-5-32-544')
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @(
        @([Security.Principal.SecurityIdentifier]'S-1-5-18', 'FullControl'),
        @([Security.Principal.SecurityIdentifier]'S-1-5-32-544', 'FullControl'),
        @($readerSid, 'ReadAndExecute'))) {
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($rule[0], $rule[1], $inherit, $none, 'Allow')))
}
Set-Acl -LiteralPath $Root -AclObject $acl
foreach ($d in $bin, $snapshot) { if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d | Out-Null } }
Copy-Item -LiteralPath $helperSource -Destination $helper -Force
Say "Prepared $Root" 'Green'

$action = New-ScheduledTaskAction -Execute $python -Argument ('-I "{0}" "{1}" "{2}"' -f $helper, $config, $snapshot)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$runAs = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $runAs -Settings $settings `
    -Description 'Read-only copy of the biometric sync ledger for the monitoring dashboard.' -Force | Out-Null
Say "Registered scheduled task '$TaskName'" 'Green'

$status = Join-Path $snapshot 'refresh-status.json'
$started = Get-Date
Start-ScheduledTask -TaskName $TaskName
Say 'First refresh running (large ledgers take a few minutes) ...' 'Cyan'
while ((Get-Date) -lt $started.AddMinutes(15)) {
    Start-Sleep -Seconds 5
    if ((Test-Path -LiteralPath $status) -and (Get-Item -LiteralPath $status).LastWriteTime -ge $started) { break }
}
if (-not ((Test-Path -LiteralPath $status) -and (Get-Item -LiteralPath $status).LastWriteTime -ge $started)) {
    throw "No refresh result after 15 minutes. Check the task's Last Run Result in Task Scheduler."
}
$result = Get-Content -LiteralPath $status -Raw -Encoding UTF8
Say $result
if (($result | ConvertFrom-Json).ok -ne $true) { throw 'The first refresh failed (see the error above).' }
Say 'Done. Install the dashboard as the reader, pointing at:' 'Green'
Say "  -Ledger `"$snapshot\sync.sqlite3`" -DeviceConfig `"$snapshot\local_config.monitor.py`""
