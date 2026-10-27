param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $PSScriptRoot
$target = 'C:\Program Files\FPFBiometricSyncPhase2'
$serviceName = 'ERPNextBiometricPushService'
$names = @('sync_ledger.py', 'durable_sync.py')
# Exact bytes of the inspected production pair and the reviewed replacement pair.
# Refuse a different release instead of overwriting someone else's changes.
$manifest = @{
    'sync_ledger.py' = @{
        before = '5E6713142F432B808C7BBA50A5F55798733C5C981657E381F09576FDE7BC7A2D'
        after = '15BF03152CE450B9B5D37CBB9E43D62AE863CDF6AB8B0D9CCD1846DAEEACD9B2'
    }
    'durable_sync.py' = @{
        before = '50BECDEBEB006B2F150DDFF72DAFAC940201BC4D33ED52A5669F07F7AA18923F'
        after = '48A7EA4DB376F2C2090E06BA7E87E7C84815AF5FF98C0BFA5D9DAD8C88267DBD'
    }
}
$alreadyInstalled = $true
foreach ($name in $names) {
    $installed = (Get-FileHash -LiteralPath (Join-Path $target $name) -Algorithm SHA256).Hash
    if ($installed -notin @($manifest[$name].before, $manifest[$name].after)) {
        throw "Installed file differs from the inspected version: $name"
    }
    if ($installed -ne $manifest[$name].after) { $alreadyInstalled = $false }
    if ((Get-FileHash -LiteralPath (Join-Path $source $name) -Algorithm SHA256).Hash -ne $manifest[$name].after) {
        throw "Repository payload hash mismatch: $name"
    }
}
$service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
if (-not $service -or $service.State -ne 'Running' -or
    $service.PathName -notlike '*C:\Program Files\FPFBiometricSyncPhase2\erpnext_sync_win.py*') {
    throw 'Expected running service installation was not found.'
}
if ($alreadyInstalled) {
    Write-Output 'Both corrected files are already installed. No changes made.'
    return
}
# A partial deployment needs investigation rather than another automatic attempt.
foreach ($name in $names) {
    if ((Get-FileHash -LiteralPath (Join-Path $target $name) -Algorithm SHA256).Hash -ne $manifest[$name].before) {
        throw 'Mixed installed versions found. Review the installation before applying.'
    }
}
if (-not $Apply) {
    Write-Output 'Preflight passed. No changes made. Use -Apply to back up, install and restart the service.'
    Write-Output 'Frappe processing dates must already match the agreed 2026-09-30 start date.'
    return
}
$identity = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run PowerShell as Administrator to install.'
}
# Back up only code. Never open the ledger, import configuration, or poll devices here.
$backup = Join-Path $target ('attendance-fix-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($name in $names) {
    Copy-Item -LiteralPath (Join-Path $target $name) -Destination (Join-Path $backup $name)
    if ((Get-FileHash -LiteralPath (Join-Path $backup $name) -Algorithm SHA256).Hash -ne $manifest[$name].before) {
        throw "Backup verification failed: $name"
    }
}
$replacementStarted = $false
try {
    Stop-Service -Name $serviceName
    (Get-Service -Name $serviceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(120))
    # Recheck while stopped so a concurrent change is never silently overwritten.
    foreach ($name in $names) {
        if ((Get-FileHash -LiteralPath (Join-Path $target $name) -Algorithm SHA256).Hash -ne $manifest[$name].before) {
            throw "Installed source changed during preflight: $name"
        }
    }
    $replacementStarted = $true
    foreach ($name in $names) {
        Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $target $name) -Force
        if ((Get-FileHash -LiteralPath (Join-Path $target $name) -Algorithm SHA256).Hash -ne $manifest[$name].after) {
            throw "Installed hash mismatch: $name"
        }
    }
    Start-Service -Name $serviceName
    (Get-Service -Name $serviceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(60))
    Write-Output "Installed; service is Running. Code backup: $backup"
    Write-Output 'Verify shift timestamps and Attendance after the next scheduled job. A running service alone does not prove attendance processing.'
} catch {
    $originalError = $_
    if ($replacementStarted) {
        # Restore the original pair only while the worker is stopped.
        Stop-Service -Name $serviceName -ErrorAction Stop
        (Get-Service -Name $serviceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(120))
        foreach ($name in $names) {
            Copy-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $target $name) -Force
            if ((Get-FileHash -LiteralPath (Join-Path $target $name) -Algorithm SHA256).Hash -ne $manifest[$name].before) {
                throw "Rollback verification failed: $name. Restore from $backup before starting the service."
            }
        }
        Start-Service -Name $serviceName
        (Get-Service -Name $serviceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(60))
    }
    throw $originalError
}
