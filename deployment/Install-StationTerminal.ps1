<#
.SYNOPSIS
  Installs and configures the station-terminal realtime punch push (ADMS) on an
  existing durable sync installation.

.DESCRIPTION
  Replaces erpnext_sync.py and adds realtime_capture.py, only when the installed
  erpnext_sync.py is the reviewed version this script knows. Without -Apply it
  only reports what it would do.

  With no station settings the new files are inert: no port is opened and
  nothing is posted, so step 1 can be installed on its own.

  -PunchMethod   sets STATION_PUNCH_METHOD (the whitelisted ERPNext method:
                 app.module.function, or a Server Script api_method name).
  -Stations      sets STATION_DEVICES, the complete list of station terminals,
                 as device_id=serial, e.g. FP2_DEVICE_12=CKJX201960123.
                 A device_id in devices is also polled for attendance as
                 before; any other device_id is a station-only terminal,
                 recognised by its serial and never polled.
  -MaxAgeSeconds sets STATION_PUNCH_MAX_AGE_SECONDS (punches further than this
                 from the sync PC clock are not posted; default 300 in code).
  -AllowFrom     creates or updates an inbound firewall rule for the listener
                 port, limited to these addresses or subnets.
  -Disable       sets STATION_DEVICES = {} and removes the firewall rule. The
                 files stay; with no station device they do nothing.

  With -Apply it backs up the files and local_config.py, stops the service,
  copies the files, writes the settings, starts the service and rolls
  everything back if the service does not return to Running. The SQLite
  ledger, logs and devices are never touched.

  Run from an elevated Windows PowerShell 5.1 in the folder that holds this
  script together with the new erpnext_sync.py and realtime_capture.py.

.EXAMPLE
  .\Install-StationTerminal.ps1
  .\Install-StationTerminal.ps1 -Apply
  .\Install-StationTerminal.ps1 -PunchMethod myapp.api.station_punch -Stations FP2_DEVICE_12=CKJX201960123 -AllowFrom 10.0.1.0/24 -Apply
  .\Install-StationTerminal.ps1 -Disable -Apply
#>
[CmdletBinding()]
param(
    [string]$AppRoot = "$env:ProgramFiles\FPFBiometricSyncPhase2",
    [string]$ServiceName = 'ERPNextBiometricPushService',
    [ValidatePattern('^$|^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$')][string]$PunchMethod = '',
    [string[]]$Stations = @(),
    [ValidateRange(0, 3600)][int]$MaxAgeSeconds = 0,
    [string[]]$AllowFrom = @(),
    [switch]$Disable,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$RuleName = 'FPF Biometric Station ADMS'

# SHA-256 of LF-normalized bytes. Old = the reviewed erpnext_sync.py installed by
# the Phase 2 release (unchanged from 4f20179 to 88ee83a) and the first station
# release (598242c); New = this update (station-only terminals).
# realtime_capture.py is new: the installed copy may be absent, the first station
# release (598242c), or this version.
$Files = [ordered]@{
    'erpnext_sync.py'     = @{ Old = @('07a320b5f2f3b38f8567b85098c0ff2ed2ead96002c3f7f0f1067b2ce7d53be0',
                                       '9fe5c07f33c543472264aa693edeab33864d103cb49d33c48e02b403669ce5d7')
                               New = '05e03f87af33dad080d9ff2ecdec4d3394513b72b4261c5cea15e7e0bb440429' }
    'realtime_capture.py' = @{ Old = @('de9ec6d647cd2ac6bf1aae16ee98e3ce9efd067bf67d8130eecb37d3850b7d3d'); MayBeAbsent = $true
                               New = '52a4cc613f414442e799683c5792dae9065082c0b78715c5787be0257ae21aa2' }
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
if ($Disable -and ($Stations.Count -or $AllowFrom.Count)) { throw 'Use -Disable on its own, without -Stations or -AllowFrom' }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$config = Join-Path $AppRoot 'local_config.py'
if (-not (Test-Path -LiteralPath $config)) { throw "local_config.py not found under $AppRoot" }
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $service) { throw "Service $ServiceName not found" }
if ($service.PathName -notlike "*$AppRoot*") { throw "Service runs from a different folder: $($service.PathName)" }
Say "Service: $ServiceName ($($service.State)) from $AppRoot"

# 1. Verify installed and new file versions before anything changes.
$toCopy = @()
$absent = @()
foreach ($name in $Files.Keys) {
    $installed = Join-Path $AppRoot $name
    $incoming = Join-Path $here $name
    if (-not (Test-Path -LiteralPath $incoming)) { throw "New $name is missing next to this script" }
    $newHash = Get-NormalizedHash $incoming
    if ($newHash -ne $Files[$name].New) { throw "New $name does not match the reviewed update ($newHash)" }
    if (-not (Test-Path -LiteralPath $installed)) {
        if (-not $Files[$name].MayBeAbsent) { throw "Installed $name is missing" }
        Say "  $name : new file, will be added"; $toCopy += $name; $absent += $name; continue
    }
    $current = Get-NormalizedHash $installed
    if ($current -eq $Files[$name].New) { Say "  $name : already updated" 'Green' }
    elseif ($Files[$name].Old -contains $current) { Say "  $name : reviewed old version, will be replaced"; $toCopy += $name }
    else { throw "Installed $name is an unexpected version ($current). Stopping without changes." }
}

# 2. Current settings, read with the service's own Python (the config is the service's trusted file).
$python = [regex]::Match([string]$service.PathName, '^"([^"]+python\.exe)"').Groups[1].Value
if (-not $python) { throw 'Cannot find the service Python in the service command' }
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
# Windows PowerShell 5.1 does not escape double quotes in native arguments: single quotes only.
$probe = 'import json, runpy, sys; c = runpy.run_path(sys.argv[1]); print(json.dumps(dict(logs=c.get(''LOGS_DIRECTORY'', ''logs''), ids=[str(d.get(''device_id'')) for d in c.get(''devices'', [])], port=c.get(''STATION_LISTENER_PORT'', 8090), stations=c.get(''STATION_DEVICES'', {}), method=c.get(''STATION_PUNCH_METHOD'', ''''))))'
function Invoke-ServicePython([string[]]$Arguments) {
    # Windows PowerShell 5.1 turns any stderr line from a program into an error; judge by exit code.
    $ErrorActionPreference = 'Continue'
    $out = & $python -I -X utf8 @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) { throw "The service Python failed (exit $LASTEXITCODE)" }
    ($out | Out-String).Trim()
}
$current = Invoke-ServicePython (@('-c', $probe, $config)) | ConvertFrom-Json
if (-not $current) { throw 'Could not read local_config.py with the service Python' }
$logDir = [string]$current.logs
if (-not [IO.Path]::IsPathRooted($logDir)) { $logDir = Join-Path $AppRoot $logDir }
$port = [int]$current.port
$deviceIds = @($current.ids)

$settings = [ordered]@{}
if ($PunchMethod) { $settings['STATION_PUNCH_METHOD'] = "'$PunchMethod'" }
if ($MaxAgeSeconds -gt 0) { $settings['STATION_PUNCH_MAX_AGE_SECONDS'] = [string]$MaxAgeSeconds }
if ($Disable) { $settings['STATION_DEVICES'] = '{}' }
elseif ($Stations.Count) {
    $pairs = @()
    $seen = @{}
    foreach ($entry in $Stations) {
        if ($entry -notmatch '^([A-Za-z0-9_-]+)=([A-Za-z0-9-]{3,40})$') { throw "Station must be device_id=serial: $entry" }
        $id = $Matches[1]; $serial = $Matches[2]
        if ($deviceIds -contains $id) { Say "  $id : polled device, also a station terminal" }
        else { Say "  $id : station-only terminal (not in devices, never polled for attendance)" }
        if ($seen.ContainsKey($id)) { throw "$id is listed twice" }
        $seen[$id] = $true
        $pairs += "'$id': '$serial'"
    }
    $settings['STATION_DEVICES'] = '{' + ($pairs -join ', ') + '}'
}

$stationsAfter = if ($settings.Contains('STATION_DEVICES')) { $settings['STATION_DEVICES'] -ne '{}' } else { @($current.stations.PSObject.Properties).Count -gt 0 }
$methodAfter = if ($PunchMethod) { $PunchMethod } else { [string]$current.method }
if ($stationsAfter -and -not $methodAfter) { Say '  note: station devices are set but STATION_PUNCH_METHOD is empty; punches are received and logged but not posted' 'Yellow' }

$configBytes = [IO.File]::ReadAllBytes($config)
$hasBom = $configBytes.Length -ge 3 -and $configBytes[0] -eq 0xEF -and $configBytes[1] -eq 0xBB -and $configBytes[2] -eq 0xBF
$configText = [IO.File]::ReadAllText($config)
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
    $newConfig += "$sep`r`n# Station terminals (Install-StationTerminal.ps1, $(Get-Date -Format 'yyyy-MM-dd')).`r`n" + ($appendLines -join "`r`n") + "`r`n"
}
$configLine = if ($newConfig -ne $configText) { $newConfig } else { $null }

# 3. Firewall: inbound TCP on the listener port, only from the given addresses.
$rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
$firewall = $null
if ($Disable) {
    if ($rule) { $firewall = 'remove'; Say "  firewall : will remove rule '$RuleName'" } else { Say '  firewall : no station rule' 'Green' }
} elseif ($AllowFrom.Count) {
    foreach ($a in $AllowFrom) { if ($a -notmatch '^\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?$') { throw "AllowFrom must be IPv4 addresses or subnets: $a" } }
    $firewall = 'set'
    Say ("  firewall : will {0} rule '{1}': inbound TCP {2} from {3}" -f $(if ($rule) { 'update' } else { 'create' }), $RuleName, $port, ($AllowFrom -join ', '))
} elseif ($stationsAfter -and -not $rule) {
    Say "  firewall : no rule yet; terminals cannot reach port $port until you add -AllowFrom" 'Yellow'
}

# Something else on the port would stop the listener (it logs and retries each cycle).
$owner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($owner) {
    $proc = (Get-Process -Id $owner.OwningProcess -ErrorAction SilentlyContinue).Path
    Say "  port $port : already listening ($proc)" $(if ($proc -like '*python*') { 'Green' } else { 'Yellow' })
}

if (-not $Apply) {
    Say "`nDry run only. Nothing was changed. Re-run with -Apply to update." 'Cyan'
    return
}
if (-not $toCopy.Count -and -not $configLine -and -not $firewall) { Say "`nNothing to do." 'Green'; return }

# 4. Backup, stop, replace, start; roll back on any failure.
$needsRestart = $toCopy.Count -or $configLine
if ($needsRestart) {
    $backup = Join-Path $AppRoot ('backup-station-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $backup | Out-Null
    foreach ($name in @($Files.Keys) + 'local_config.py') {
        $path = Join-Path $AppRoot $name
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination $backup }
    }
    Say "Backup: $backup"

    function Restore-Backup {
        Say 'Restoring backup...' 'Yellow'
        Stop-Service $ServiceName -ErrorAction SilentlyContinue
        [void](Wait-ServiceState 'Stopped' 120)
        foreach ($name in @($Files.Keys) + 'local_config.py') {
            $saved = Join-Path $backup $name
            if (Test-Path -LiteralPath $saved) { Copy-Item -LiteralPath $saved -Destination $AppRoot -Force }
            elseif ($absent -contains $name) { Remove-Item -LiteralPath (Join-Path $AppRoot $name) -Force -ErrorAction SilentlyContinue }
        }
        Start-Service $ServiceName -ErrorAction SilentlyContinue
        Say "Restored. Service state: $((Get-Service $ServiceName).Status)" 'Yellow'
    }

    $started = Get-Date
    try {
        Say 'Stopping service...'
        Stop-Service $ServiceName
        if (-not (Wait-ServiceState 'Stopped' 120)) { throw 'Service did not stop within 2 minutes' }
        # realtime_capture.py first: the new erpnext_sync.py imports it.
        foreach ($name in @($toCopy | Sort-Object { $_ -ne 'realtime_capture.py' })) {
            Copy-Item -LiteralPath (Join-Path $here $name) -Destination (Join-Path $AppRoot $name) -Force
            if ((Get-NormalizedHash (Join-Path $AppRoot $name)) -ne $Files[$name].New) { throw "Copy of $name failed verification" }
            Say "  installed $name" 'Green'
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
        Say 'Service is running with the update.' 'Green'
    } catch {
        Say "Update failed: $($_.Exception.Message)" 'Red'
        Restore-Backup
        throw
    }
}

if ($firewall -eq 'remove') {
    Remove-NetFirewallRule -DisplayName $RuleName
    Say "  firewall rule '$RuleName' removed" 'Green'
} elseif ($firewall -eq 'set') {
    if ($rule) { Remove-NetFirewallRule -DisplayName $RuleName }
    New-NetFirewallRule -DisplayName $RuleName -Description 'ZKTeco ADMS push from station terminals to the biometric sync service' `
        -Direction Inbound -Protocol TCP -LocalPort $port -RemoteAddress $AllowFrom -Action Allow -Profile Any | Out-Null
    Say "  firewall rule '$RuleName' allows TCP $port from $($AllowFrom -join ', ')" 'Green'
}

# 5. With station devices, the listener should be up within a cycle.
$log = Join-Path $logDir 'logs.log'
if ($stationsAfter -and $needsRestart) {
    $deadline = (Get-Date).AddMinutes(2)
    $line = $null
    while ((Get-Date) -lt $deadline -and -not $line) {
        $line = Get-Content -LiteralPath $log -Tail 400 -Encoding UTF8 -ErrorAction SilentlyContinue |
            Where-Object { $_ -match 'ADMS listener started' } |
            Where-Object { [datetime]::ParseExact($_.Substring(0, 19), 'yyyy-MM-dd HH:mm:ss', $null) -ge $started } |
            Select-Object -Last 1
        if (-not $line) { Start-Sleep -Seconds 10 }
    }
    if ($line) { Say $line 'Green' } else { Say "No 'ADMS listener started' line within 2 minutes; check $(Join-Path $logDir 'error.log')." 'Yellow' }
}
Say "Log to watch: $log  (ADMS handshake, Station punch delivered, Skipped ATTLOG rows)"
