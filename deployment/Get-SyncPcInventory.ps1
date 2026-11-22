<#
.SYNOPSIS
  Read-only inventory of a biometric sync PC, to plan a clean setup.

.DESCRIPTION
  Lists the Windows edition, sync services and the folders they run from, the
  folders under C:\GitHub, Program Files and ProgramData that belong to the sync
  tool, ledgers, scheduled tasks, Python installations and free disk space.
  Changes nothing: no service, file, ACL or task is modified. Prints no secrets:
  for config files it reports only that they exist, their size and date.

  Run in an elevated Windows PowerShell (needed to read the protected data folders):
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-SyncPcInventory.ps1
  The report is written to the Desktop as sync-pc-inventory-<date>.txt.
#>
[CmdletBinding()]
param([string]$Output = (Join-Path ([Environment]::GetFolderPath('Desktop')) ("sync-pc-inventory-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmm'))))

$ErrorActionPreference = 'Continue'
$lines = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$text = '') { $lines.Add($text) }
function Add-Section([string]$title) { Add-Line ''; Add-Line ('=== ' + $title) }
function Get-FolderSummary([string]$path) {
    try {
        $files = @(Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue)
        $size = ($files | Measure-Object Length -Sum).Sum
        $latest = ($files | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
        '{0} files, {1:N1} MB, newest file {2:yyyy-MM-dd HH:mm}' -f $files.Count, ($size / 1MB), $latest
    } catch { 'unreadable: ' + $_.Exception.GetType().Name }
}
function Get-GitCommit([string]$path) {
    $head = Join-Path $path '.git\HEAD'
    if (-not (Test-Path -LiteralPath $head)) { return 'not a git checkout' }
    $ref = (Get-Content -LiteralPath $head -TotalCount 1).Trim()
    if ($ref -like 'ref: *') {
        $name = $ref.Substring(5)
        $file = Join-Path $path ('.git\' + $name.Replace('/', '\'))
        $sha = if (Test-Path -LiteralPath $file) { (Get-Content -LiteralPath $file -TotalCount 1).Trim() } else {
            $packed = Join-Path $path '.git\packed-refs'
            if (Test-Path -LiteralPath $packed) { ((Select-String -LiteralPath $packed -Pattern (' ' + [regex]::Escape($name) + '$') | Select-Object -First 1).Line -split ' ')[0] } }
        return 'git branch {0} at {1}' -f $name.Replace('refs/heads/', ''), ([string]$sha).Substring(0, [Math]::Min(7, ([string]$sha).Length))
    }
    'git detached at ' + $ref.Substring(0, 7)
}
function Add-Folder([string]$path) {
    Add-Line ('* ' + $path)
    Add-Line ('    ' + (Get-FolderSummary $path))
    Add-Line ('    ' + (Get-GitCommit $path))
    foreach ($name in 'local_config.py', 'erpnext_sync_win.py', 'release-info.json', 'pyvenv.cfg') {
        $hits = @(Get-ChildItem -LiteralPath $path -Recurse -Force -Filter $name -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\(\.git|site-packages|\.acceptance|dist|node_modules)\\' } | Select-Object -First 5)
        foreach ($h in $hits) { Add-Line ('    has {0}  ({1:yyyy-MM-dd HH:mm}, {2} bytes)' -f $h.FullName.Substring($path.Length).TrimStart('\'), $h.LastWriteTime, $h.Length) }
    }
    foreach ($db in @(Get-ChildItem -LiteralPath $path -Recurse -Force -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in '.sqlite3', '.db' -and $_.FullName -notmatch '\\(\.git|site-packages|node_modules)\\' } | Select-Object -First 10)) {
        Add-Line ('    ledger {0}  ({1:N1} MB, {2:yyyy-MM-dd HH:mm})' -f $db.FullName.Substring($path.Length).TrimStart('\'), ($db.Length / 1MB), $db.LastWriteTime)
    }
    $logs = Join-Path $path 'logs'
    if (Test-Path -LiteralPath $logs) { Add-Line ('    logs folder: ' + (Get-FolderSummary $logs)) }
}

Add-Line ('Sync PC inventory, ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' (read-only)')
$elevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Add-Line ('Elevated: ' + $elevated + $(if (-not $elevated) { '  (protected folders may show as unreadable; re-run as administrator)' }))

Add-Section 'Windows'
$os = Get-CimInstance Win32_OperatingSystem
Add-Line ('{0}, version {1}, build {2}, {3}' -f $os.Caption, (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').DisplayVersion, $os.BuildNumber, $os.OSArchitecture)
Add-Line ('Product type: ' + @{1 = 'Workstation'; 2 = 'Domain controller'; 3 = 'Server'}[[int]$os.ProductType])
Add-Line ('PowerShell: ' + $PSVersionTable.PSVersion)
foreach ($d in Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3') { Add-Line ('Disk {0} {1}: {2:N1} GB free of {3:N1} GB' -f $d.DeviceID, $d.FileSystem, ($d.FreeSpace / 1GB), ($d.Size / 1GB)) }
$sleep = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>$null | Select-String 'Current AC Power Setting Index' | Select-Object -First 1)
if ($sleep) { Add-Line ('Sleep after (AC, seconds): ' + [Convert]::ToInt32(($sleep.Line -split ':')[-1].Trim(), 16)) }

Add-Section 'Sync services'
$services = @(Get-CimInstance Win32_Service | Where-Object { $_.Name -match 'ERPNext|Biometric|FPF' -or $_.PathName -match 'erpnext_sync|biometric' })
if (-not $services) { Add-Line 'none found' }
foreach ($s in $services) {
    Add-Line ('* {0} ({1}): {2}, start {3}, account {4}' -f $s.Name, $s.DisplayName, $s.State, $s.StartMode, $s.StartName)
    Add-Line ('    command: ' + $s.PathName)
}

Add-Section 'Sync tool folders under C:\GitHub'
$found = @(Get-ChildItem 'C:\GitHub' -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(biometric|fpf)|attendance|zkteco' })
if (-not $found) { Add-Line 'none found' }
foreach ($f in $found) { Add-Folder $f.FullName }

Add-Section 'Install folders (Program Files)'
$found = @(foreach ($root in $env:ProgramFiles, ${env:ProgramFiles(x86)}) { if ($root) { Get-ChildItem $root -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'FPF|Biometric|ERPNext' } } })
if (-not $found) { Add-Line 'none found' }
foreach ($f in $found) { Add-Folder $f.FullName }

Add-Section 'Data folders (ProgramData)'
$found = @(Get-ChildItem $env:ProgramData -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'FPF|Biometric|ERPNext' })
if (-not $found) { Add-Line 'none found' }
foreach ($f in $found) { Add-Folder $f.FullName }

Add-Section 'Dashboard folders (user profiles)'
$found = @(foreach ($p in Get-ChildItem 'C:\Users' -Directory -Force -ErrorAction SilentlyContinue) {
    Get-ChildItem (Join-Path $p.FullName 'AppData\Local') -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'FPF|Biometric' } })
if (-not $found) { Add-Line 'none found' }
foreach ($f in $found) { Add-Line ('* {0}: {1}' -f $f.FullName, (Get-FolderSummary $f.FullName)) }
foreach ($p in Get-ChildItem 'C:\Users' -Directory -Force -ErrorAction SilentlyContinue) {
    foreach ($lnk in @(Get-ChildItem (Join-Path $p.FullName 'Desktop') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'Dashboard|Monitor|Biometric' })) { Add-Line ('  shortcut: ' + $lnk.FullName) }
}

Add-Section 'Scheduled tasks'
$tasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'FPF|Biometric|ERPNext' -or (($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' ') -match 'biometric|erpnext_sync|monitor_snapshot' })
if (-not $tasks) { Add-Line 'none found' }
foreach ($t in $tasks) {
    Add-Line ('* {0}{1}: {2}, runs as {3}' -f $t.TaskPath, $t.TaskName, $t.State, $t.Principal.UserId)
    foreach ($a in $t.Actions) { Add-Line ('    ' + $a.Execute + ' ' + $a.Arguments) }
}

Add-Section 'Python installations'
$pythons = @(foreach ($root in $env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs\Python')) {
    if ($root) { Get-ChildItem $root -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^Python\d' } } })
foreach ($p in $pythons) {
    $exe = Join-Path $p.FullName 'python.exe'
    if (Test-Path $exe) { Add-Line ('* {0}: {1}' -f $exe, ((& $exe --version) 2>&1)) }
}
if (-not $pythons) { Add-Line 'none found under Program Files or the current user' }

Add-Section 'Sync PC network'
foreach ($a in Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }) { Add-Line ('{0}  {1}/{2}' -f $a.InterfaceAlias, $a.IPAddress, $a.PrefixLength) }

$lines | Set-Content -LiteralPath $Output -Encoding UTF8
$lines
Write-Host ''
Write-Host ('Report saved to ' + $Output)
