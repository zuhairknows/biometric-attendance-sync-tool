# Phase 1 production clean cutover — awaiting final authorization

> Historical procedure: core Phase 1 deployment and integration-account GET checks have completed. Do not rerun the commands below as a current next step. See [current Phase 1 status](phase1-status.md); Auto Attendance remains blocked pending a separately verified safeguard.

**Current progress:** Section B completed successfully and its verified backup must
be preserved. Do not rerun Sections A or B. Their older boundary is historical;
Section C below explicitly supersedes it with the proposed activation boundary
**2026-10-17 08:58:22, Arab Standard Time (UTC+03:00)**, awaiting final execution review.

Do not execute sections B–D before final confirmation. No command below was run
against production while preparing this procedure. Use one elevated **Windows
PowerShell 5.1** session. No dependency installation, service reinstallation, Git
reset/checkout, historical import, recovery request, or configuration execution.

Pinned release: `4256cd6201b17dcda3f9d078552acf1d3c58b3f0`.
Legacy rollback baseline: `a23b8112fd1b87f3d2bb10f798b45e150aadcd7a`.
Unverified effective server hooks remain an explicitly accepted deployment risk.

## A. Read-only plan — display these values for final confirmation

The approved cutover is **2026-10-17 00:43:56, Arab Standard Time (UTC+03:00)**.
This block makes no filesystem or service changes and never calculates a replacement
boundary. Section A has already completed; do not repeat its preflight checks.
Continue Section B in that same session. Section B explicitly installs the newly
approved value into `$Cutover` and checks it immediately before stopping the service.
If it expires, stop and obtain a newly approved plan; never move or backdate it.

```powershell
$ErrorActionPreference = 'Stop'
$ServiceName = 'ERPNextBiometricPushService'
$Code = 'C:\GitHub\biometric-attendance-sync-tool'
$Zip = 'C:\Users\s.taskea\Downloads\FPF-Phase1-Preparation\phase1-release.zip'
$Backup = 'C:\ProgramData\ERPNextBiometricBackups\legacy-a23b8112-phase1-cutover'
$Release = Join-Path $Backup 'release-4256cd6'
$LedgerDir = 'C:\ProgramData\ERPNextBiometricPushService'
$Ledger = Join-Path $LedgerDir 'sync.sqlite3'
$Cutover = '2026-10-17 00:43:56'
$Review = 'Approved Phase1 clean cutover; server-hook risk accepted; confirmation disabled'
$Service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
$Match = [regex]::Match($Service.PathName, '^\s*(?:"([^"]+)"|(\S+))\s+(?:"([^"]+)"|(\S+))\s*$')
if (-not $Match.Success) { throw 'Unexpected service command; stop.' }
$Python = if ($Match.Groups[1].Success) { $Match.Groups[1].Value } else { $Match.Groups[2].Value }
$Launcher = if ($Match.Groups[3].Success) { $Match.Groups[3].Value } else { $Match.Groups[4].Value }
if ($Service.StartName -ne 'LocalSystem' -or $Service.State -ne 'Running' -or
    [IO.Path]::GetFullPath($Launcher) -ine (Join-Path $Code 'erpnext_sync_win.py') -or
    -not [IO.Path]::IsPathRooted($Python) -or
    [IO.Path]::GetFileName($Python) -ine 'python.exe') { throw 'Unexpected service baseline; stop.' }
if (Test-Path -LiteralPath $Backup) { throw 'Backup destination exists; preserve it and stop.' }
if (Test-Path -LiteralPath $LedgerDir) {
    throw 'Ledger directory already exists; inspect read-only before approving provisioning.'
}
$Environment = (& $Python -I -S -B -c "import pathlib,sys; p=pathlib.Path(sys.executable); print(p.parent.parent if p.parent.name.lower()=='scripts' else p.parent)").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify existing Python environment.' }
$BasePython = (& $Python -I -S -B -c 'import sys; print(sys.base_prefix)').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify base Python.' }
[pscustomobject]@{Backup=$Backup; Archive=$Zip; Python=$Python;
    CutoverLocal=$Cutover; WindowsTimezone=(Get-TimeZone).Id;
    Ledger=$Ledger; ConfirmationReads=$false}
```

**Stop here.** Obtain explicit approval of the displayed cutover/timezone, backup
destination, commands below, maintenance-gap holds, and watermark consequences.
Ensure no parallel integration worker or concurrent code/config/environment edits.

## B. Authorized stop and verified backup

Only run after approval, continuing the same session. These functions use existing
Windows/Python tools; they are not new installed utilities. New private directories
are owned by Administrators, avoiding service startup failure from an individual
administrator owning the ledger directory. Existing directory ACLs are not rewritten.

```powershell
$ErrorActionPreference = 'Stop'
# Explicitly replace the superseded Section A session value; never use AddMinutes.
$Cutover = '2026-10-17 00:43:56'
function Assert-ApprovedCutover {
    if ($Cutover -cne '2026-10-17 00:43:56' -or
        (Get-TimeZone).Id -ne 'Arab Standard Time') {
        throw 'Cutover/timezone differs from the approved boundary. Stop.'
    }
    $boundary = [datetime]::ParseExact($Cutover,'yyyy-MM-dd HH:mm:ss',
        [Globalization.CultureInfo]::InvariantCulture)
    if ((Get-Date) -ge $boundary) {
        throw 'Approved cutover expired. No service stop or activation permitted; obtain a new approval.'
    }
}
function New-PrivateDirectory([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw 'Destination already exists; preserve it.' }
    New-Item -ItemType Directory -Path $Path -ErrorAction Stop | Out-Null
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid),
            'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    $actual = Get-Acl -LiteralPath $Path
    $rules = @($actual.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if (-not $actual.AreAccessRulesProtected -or $rules.Count -ne 2 -or
        $actual.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-32-544') {
        throw 'Private directory verification failed.'
    }
    foreach ($rule in $rules) {
        if ($rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544') -or
            $rule.AccessControlType -ne 'Allow' -or $rule.IsInherited -or
            $rule.FileSystemRights -ne 'FullControl') { throw 'Unexpected directory permissions.' }
    }
}
function Stop-Confirmed([switch]$RequireFutureCutover) {
    $before = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
    if ($before.State -ne 'Stopped') {
        # Initial stop only: failure/rollback stops must remain possible after cutover.
        if ($RequireFutureCutover) { Assert-ApprovedCutover }
        Stop-Service -Name $ServiceName -ErrorAction Stop
        (Get-Service $ServiceName).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(120))
    }
    if ($before.ProcessId -gt 0) {
        $process = Get-Process -Id $before.ProcessId -ErrorAction SilentlyContinue
        if ($process -and -not $process.WaitForExit(30000)) { throw 'Service process has not exited.' }
    }
    $after = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
    if ($after.State -ne 'Stopped' -or $after.ProcessId -ne 0 -or
        ($before.ProcessId -gt 0 -and (Get-Process -Id $before.ProcessId -ErrorAction SilentlyContinue))) {
        throw 'Service process has not exited; no copying or replacement permitted.'
    }
}
function Copy-VerifiedTree([string]$Source,[string]$Destination) {
    $sourceFull = [IO.Path]::GetFullPath($Source).TrimEnd('\')
    $targetFull = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
    if ($targetFull -ieq $sourceFull -or $targetFull.StartsWith($sourceFull+'\',[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Backup cannot be inside its source.'
    }
    if ((Get-Item -LiteralPath $Source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Unexpected source reparse point; stop.'
    }
    & robocopy.exe $Source $Destination /E /COPY:DAT /DCOPY:DAT /XJ /R:0 /W:0 /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw 'Backup copy failed.' }
    # Byte comparison includes configuration, but never prints/hashes its contents.
    @'
import os, stat, sys
from pathlib import Path
try:
    def inventory(root):
        found = {}
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                p = Path(directory) / name
                s = p.lstat()
                if s.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError()
                found[str(p.relative_to(root))] = 'directory' if p.is_dir() else 'file'
        return found
    source, target = map(Path, sys.argv[1:])
    a, b = inventory(source), inventory(target)
    if a != b:
        raise ValueError()
    for name, kind in a.items():
        if kind != 'file':
            continue
        original = (source / name).stat()
        with (source / name).open('rb') as x, (target / name).open('rb') as y:
            while True:
                left, right = x.read(1048576), y.read(1048576)
                if left != right:
                    raise ValueError()
                if not left:
                    break
        final = (source / name).stat()
        if (original.st_size, original.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            raise ValueError()
    if inventory(source) != a:
        raise ValueError()
except Exception:
    print('BACKUP_VERIFICATION: FAIL'); sys.exit(1)
print('BACKUP_VERIFICATION: PASS')
'@ | & $Python -I -S -B - $Source $Destination
    if ($LASTEXITCODE -ne 0) { throw 'Backup verification failed; stay stopped.' }
}

Assert-ApprovedCutover
$StopRequestedAt = (Get-Date).ToString('o')
Stop-Confirmed -RequireFutureCutover
$StoppedAt = (Get-Date).ToString('o')
New-PrivateDirectory $Backup
Copy-VerifiedTree $Code (Join-Path $Backup 'legacy-code')
# The confirmed active logs directory is inside $Code, so it is included above.
foreach ($entry in @(@($Environment,'python-environment'), @($BasePython,'python-base'))) {
    $path = [IO.Path]::GetFullPath($entry[0]).TrimEnd('\')
    if ($path -ine $Code -and -not $path.StartsWith($Code+'\',[StringComparison]::OrdinalIgnoreCase)) {
        if ($path -ine $Environment -or $entry[1] -eq 'python-environment') {
            Copy-VerifiedTree $path (Join-Path $Backup $entry[1])
        }
    }
}
& reg.exe export 'HKLM\SYSTEM\CurrentControlSet\Services\ERPNextBiometricPushService' (Join-Path $Backup 'service-registration.reg')
if ($LASTEXITCODE -ne 0) { throw 'Service registration export failed; stay stopped.' }
foreach ($operation in @('qc','qfailure','qfailureflag','sdshow')) {
    & sc.exe $operation $ServiceName | Set-Content -LiteralPath (Join-Path $Backup ('service-'+$operation+'.txt'))
    if ($LASTEXITCODE -ne 0) { throw 'Service metadata capture failed; stay stopped.' }
}
$Service | Select-Object Name,StartName,StartMode,PathName |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Backup 'service-baseline.json')
$StoppedAt | Set-Content -LiteralPath (Join-Path $Backup 'stopped-at.txt')
$StopRequestedAt | Set-Content -LiteralPath (Join-Path $Backup 'stop-requested-at.txt')
'a23b8112fd1b87f3d2bb10f798b45e150aadcd7a' |
    Set-Content -LiteralPath (Join-Path $Backup 'legacy-commit.txt')
Stop-Confirmed
```

If anything fails, remain stopped and preserve the partial backup. Do not deploy.
The backup includes source/Git metadata, original config/credentials, `.venv` when
inside the checkout, all active logs/status/recovery dumps, and external Python
environment/base files when applicable. No dependency installation or upgrade.

## C. Authorized deployment, provisioning and activation

Continue in the same elevated session after the completed Section B. **Never rerun
Section B or overwrite its backup.** The exact proposed activation boundary is
**2026-10-17 08:58:22, Arab Standard Time (UTC+03:00)**. The block explicitly updates
both `$Cutover` and its guard; the old Section B guard must not be reused.

The ZIP hash was already verified. Extraction happens separately from production.
Only the ten runtime Python files are replaced, retaining legacy Git metadata,
configuration, environment, logs and dumps. The protected release record and file
comparisons identify the new runtime; **Git HEAD alone remains the legacy revision**.

```powershell
$ErrorActionPreference = 'Stop'
$Cutover = '2026-10-17 08:58:22'
# Validate retained session state without repeating completed backup/preflight tests.
$Expected = @{
    ServiceName='ERPNextBiometricPushService'
    Code='C:\GitHub\biometric-attendance-sync-tool'
    Zip='C:\Users\s.taskea\Downloads\FPF-Phase1-Preparation\phase1-release.zip'
    Backup='C:\ProgramData\ERPNextBiometricBackups\legacy-a23b8112-phase1-cutover'
    Release='C:\ProgramData\ERPNextBiometricBackups\legacy-a23b8112-phase1-cutover\release-4256cd6'
    LedgerDir='C:\ProgramData\ERPNextBiometricPushService'
    Ledger='C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3'
    Python='C:\GitHub\biometric-attendance-sync-tool.venv\Scripts\python.exe'
}
foreach ($name in $Expected.Keys) {
    $value = (Get-Variable -Name $name -ErrorAction Stop).Value
    if ($value -isnot [string] -or $value -ine $Expected[$name]) {
        throw "Missing or unexpected session variable: $name. Do not rerun Section B."
    }
}
$reference = (Get-Variable -Name Review -ErrorAction Stop).Value
if ($reference -isnot [string] -or [string]::IsNullOrWhiteSpace($reference) -or $reference.Length -gt 200) {
    throw 'Missing or invalid retained review reference.'
}
foreach ($helper in @('New-PrivateDirectory','Stop-Confirmed')) {
    $null = Get-Command -Name $helper -CommandType Function -ErrorAction Stop
}
$current = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
if ($null -eq $current -or $current.StartName -ne 'LocalSystem' -or
    $current.State -ne 'Stopped' -or $current.ProcessId -ne 0) {
    throw 'Existing LocalSystem service is not confirmed stopped. Do not activate.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Backup 'legacy-code\local_config.py') -PathType Leaf)) {
    throw 'Completed legacy backup is unavailable. Preserve existing evidence; do not rerun Section B.'
}
if ((Test-Path -LiteralPath $LedgerDir) -or (Test-Path -LiteralPath $Release)) {
    throw 'Activation artifacts already exist. Preserve them; no overwrite, resume or ledger recreation.'
}
function Assert-ApprovedCutover {
    if ($Cutover -cne '2026-10-17 08:58:22' -or
        (Get-TimeZone).Id -ne 'Arab Standard Time') {
        throw 'Activation boundary/timezone differs from the reviewed plan. Stop.'
    }
    $boundary = [datetime]::ParseExact($Cutover,'yyyy-MM-dd HH:mm:ss',
        [Globalization.CultureInfo]::InvariantCulture)
    if ((Get-Date) -ge $boundary) {
        throw 'Activation boundary expired. Stay stopped; preserve backup and any ledger/marker. Obtain a new approved plan.'
    }
}
try {
    Assert-ApprovedCutover
    Expand-Archive -LiteralPath $Zip -DestinationPath $Release -ErrorAction Stop
    New-PrivateDirectory $LedgerDir
    Assert-ApprovedCutover
    & $Python -B (Join-Path $Release 'sync_cli.py') --db $Ledger provision `
        --destination-url 'https://erp.myfpf.com' --erpnext-version 15 `
        --cutover-time $Cutover --review-reference $Review --service-sid 'S-1-5-18' --acknowledge-cutover
    if ($LASTEXITCODE -ne 0) { throw 'Provisioning failed; preserve database/marker and stop.' }
    # Newly provisioned files must also have an owner accepted under LocalSystem.
    foreach ($file in @($Ledger, ($Ledger+'.activation.json'))) {
        & icacls.exe $file /setowner '*S-1-5-32-544' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'New ledger-file owner verification required; stay stopped.' }
        if ((Get-Acl -LiteralPath $file).GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-32-544') {
            throw 'Unexpected new ledger-file owner; stay stopped.'
        }
    }
    & $Python -B (Join-Path $Release 'sync_cli.py') --db $Ledger verify
    if ($LASTEXITCODE -ne 0) { throw 'Ledger verification failed.' }
    & $Python -B (Join-Path $Release 'sync_cli.py') --db $Ledger backup --output (Join-Path $Backup 'before-activation.sqlite3')
    if ($LASTEXITCODE -ne 0) { throw 'Ledger backup failed.' }
    Copy-Item -LiteralPath ($Ledger+'.activation.json') -Destination (Join-Path $Backup 'before-activation.activation.json')
    Stop-Confirmed
    $Runtime = @('erpnext_sync.py','erpnext_sync_win.py','SMWinservice.py',
        'durable_sync.py','durable_delivery.py','durable_startup.py','sync_ledger.py',
        'punch_normalization.py','sync_cli.py','legacy_import.py')
    foreach ($file in $Runtime) {
        Copy-Item -LiteralPath (Join-Path $Release $file) -Destination (Join-Path $Code $file)
        if ((Get-FileHash -LiteralPath (Join-Path $Release $file)).Hash -ne
            (Get-FileHash -LiteralPath (Join-Path $Code $file)).Hash) { throw 'Runtime copy mismatch.' }
    }
    # Modify only these three operational settings, preserving the original file ACL.
    @'
import ast, os, sys, warnings
from pathlib import Path
warnings.simplefilter('ignore')
try:
    path, backup = map(Path, sys.argv[1:])
    original = path.read_bytes()
    if original != backup.read_bytes():
        raise ValueError()
    source = original.decode('utf-8-sig')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore'); tree = ast.parse(source)
    changes = {'DURABLE_SYNC_ENABLED': 'True',
               'DURABLE_DB_PATH': repr(r'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3'),
               'DURABLE_CONFIRM_READS_VERIFIED': 'False'}
    lines = source.splitlines(keepends=True)
    ending = '\r\n' if '\r\n' in source else '\n'
    edits, seen = [], set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in changes for t in node.targets):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                raise ValueError()
            key = node.targets[0].id
            if key in seen or node.col_offset != 0 or any(n is not node and n.lineno <= node.end_lineno and n.end_lineno >= node.lineno for n in tree.body):
                raise ValueError()
            seen.add(key); edits.append((node.lineno-1,node.end_lineno,key))
    for start, end, key in sorted(edits, reverse=True):
        lines[start:end] = [key+' = '+changes[key]+ending]
    updated = ''.join(lines) + ending + ''.join(key+' = '+value+ending for key,value in changes.items() if key not in seen)
    expected = {'DURABLE_SYNC_ENABLED': True,
                'DURABLE_DB_PATH': r'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3',
                'DURABLE_CONFIRM_READS_VERIFIED': False}
    def configuration_parts(text):
        parsed = ast.parse(text)
        other, settings = [], {}
        for node in parsed.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id in expected):
                key = node.targets[0].id
                if key in settings:
                    raise ValueError()
                settings[key] = ast.literal_eval(node.value)
            else:
                other.append(node)
        return ast.dump(ast.Module(body=other, type_ignores=[]), include_attributes=False), settings
    original_other, unused = configuration_parts(source)
    revised_other, settings = configuration_parts(updated)
    if original_other != revised_other or set(settings) != set(expected):
        raise ValueError()
    for key, value in expected.items():
        if type(settings[key]) is not type(value) or settings[key] != value:
            raise ValueError()
    encoded = (b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b'') + updated.encode('utf-8')
    with path.open('r+b') as stream:
        stream.write(encoded); stream.truncate(); stream.flush(); os.fsync(stream.fileno())
    if path.read_bytes() != encoded:
        raise ValueError()
    saved_other, saved_settings = configuration_parts(path.read_bytes().decode('utf-8-sig'))
    if saved_other != original_other or saved_settings != settings:
        raise ValueError()
except Exception:
    print('CONFIGURATION_UPDATE: FAIL'); sys.exit(1)
print('CONFIGURATION_UPDATE: PASS; ONLY_THREE_DURABLE_SETTINGS_CHANGED')
'@ | & $Python -I -S -B - (Join-Path $Code 'local_config.py') (Join-Path $Backup 'legacy-code\local_config.py')
    if ($LASTEXITCODE -ne 0) { throw 'Configuration update failed; stay stopped.' }
    @('4256cd6201b17dcda3f9d078552acf1d3c58b3f0', $Cutover,
        'DURABLE_CONFIRM_READS_VERIFIED=False; effective hooks UNVERIFIED; risk accepted') |
        Set-Content -LiteralPath (Join-Path $Backup 'deployed-release.txt')
    Assert-ApprovedCutover
    Start-Service -Name $ServiceName -ErrorAction Stop
    (Get-Service $ServiceName).WaitForStatus('Running',[TimeSpan]::FromSeconds(120))
    & $Python -B (Join-Path $Code 'sync_cli.py') --db $Ledger verify
    if ($LASTEXITCODE -ne 0) { throw 'Post-start ledger verification failed.' }
    & $Python -B (Join-Path $Code 'sync_cli.py') --db $Ledger status
    if ($LASTEXITCODE -ne 0) { throw 'Post-start ledger diagnostics failed.' }
} catch {
    # Do not let a stop-helper exception hide whether the sender may still run.
    $stopFailed = $false
    try { Stop-Confirmed } catch { $stopFailed = $true }
    $stopped = $false
    try {
        $observed = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
        $stopped = ($null -ne $observed -and $observed.State -eq 'Stopped' -and $observed.ProcessId -eq 0)
    } catch { $stopped = $false }
    if ($stopFailed -or -not $stopped) {
        throw 'CRITICAL: activation failed and stopped/process-exit state cannot be confirmed. Service may still run. No rollback or ledger copying until stopped state is established.'
    }
    Write-Warning 'Activation failed; sender confirmed stopped. Preserve backup, latest ledger, marker and logs. Never auto-start legacy.'
    throw 'NO-GO: activation failed; sender stopped; protected evidence retained.'
}
```

## D. Short acceptance checklist — no synthetic production submissions

Observe at least two complete device scan cycles, spanning the cutover and normal
new punches. Use existing aggregate logs/CLI diagnostics and read-only Frappe views.
Do not rerun staging tests or the already completed fixed-control GET verification.

- **PASS:** service/process remains running, all 12 device scans succeed, no critical
  storage/startup failures, and ledger integrity remains `ok`.
- **PASS:** real post-boundary punches persist locally and reach Frappe with the
  expected employee/time/log type; ledger shows matching completed records.
- **PASS:** no pre-boundary submissions; historical payloads remain `cutover_hold`,
  with original dumps retained and no device history deletion.
- **PASS:** no unexpected duplicate Checkins across devices. With confirmation
  disabled, duplicate responses/timeouts can remain `confirmation_pending`; do not
  clear or resubmit them. Failures remain durable, visible and assigned for review.
- **PASS:** record stop time, last verified legacy delivery, fixed cutover and first
  successful durable scan/delivery. Account for the entire maintenance interval;
  pre-boundary gap punches stay held, never replayed or claimed delivered. If exact
  legacy completion cannot be established, explicitly record that uncertainty.
- **PASS:** explicitly acknowledge held history can indefinitely cap Shift Type
  watermarks and delay auto-attendance. Do not edit floors or release holds.

Unexpected history submissions/duplicates, storage errors, failed scans, or failure
to deliver normal eligible punches across the observation cycles trigger section E.
After acceptance, use the existing `backup` CLI to create a new post-activation
backup in the protected backup folder and preserve its activation marker alongside.

### Post-activation online backup — no service stop

Use the existing verified private backup root below. Run only when the operator
authorizes creating the local backup. Do not copy the live SQLite file directly.
The CLI opens the source read-only, uses SQLite's online backup and checks integrity.
This captures a consistent point in time while subsequent production work continues.

```powershell
$ErrorActionPreference = 'Stop'
$Python = 'C:\GitHub\biometric-attendance-sync-tool.venv\Scripts\python.exe'
$Cli = 'C:\GitHub\biometric-attendance-sync-tool\sync_cli.py'
$Db = 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3'
$Parent = 'C:\ProgramData\ERPNextBiometricBackups\legacy-a23b8112-phase1-cutover'
if (-not (Test-Path -LiteralPath $Parent -PathType Container)) { throw 'Verified private backup root unavailable.' }
$Destination = Join-Path $Parent ('post-activation-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Destination -ErrorAction Stop | Out-Null
$CopyDb = Join-Path $Destination 'sync.sqlite3'
$CopyMarker = $CopyDb + '.activation.json'
Copy-Item -LiteralPath ($Db+'.activation.json') -Destination $CopyMarker -ErrorAction Stop
& $Python -B $Cli --db $Db backup --output $CopyDb
if ($LASTEXITCODE -ne 0) { throw 'Online backup failed. Preserve partial output; source remains untouched.' }
if ((Get-FileHash -LiteralPath ($Db+'.activation.json')).Hash -ne
    (Get-FileHash -LiteralPath $CopyMarker).Hash) { throw 'Activation marker changed; do not certify this backup.' }
@'
import json, sqlite3, sys
from pathlib import Path
try:
    db, marker = map(Path, sys.argv[1:])
    with marker.open('r', encoding='utf-8') as stream:
        activation = json.load(stream)
    connection = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
    try:
        cookie = connection.execute('PRAGMA application_id').fetchone()[0]
        if activation.get('schema') != 'durable-activation-v1' or not cookie or cookie != activation.get('ledger_cookie'):
            raise ValueError()
    finally:
        connection.close()
except Exception:
    print('BACKUP_MARKER_BINDING: FAIL'); sys.exit(1)
print('BACKUP_MARKER_BINDING: PASS')
'@ | & $Python -I -S -B - $CopyDb $CopyMarker
if ($LASTEXITCODE -ne 0) { throw 'Backup/marker binding failed; do not use this backup for restoration.' }
Write-Output "Verified post-activation backup: $Destination"
```

PASS requires the CLI's `integrity: ok`, unchanged marker comparison and
`BACKUP_MARKER_BINDING: PASS`. Retain both files and the output/time as a pair.
No source records are changed. A later restoration still needs interval accounting;
never overwrite the running/latest ledger with this older snapshot.

## E. Failure/rollback — stop and preserve, never automatically resume legacy

```powershell
Stop-Confirmed
$Failure = 'failure-' + [guid]::NewGuid().ToString('N')
& $Python -B (Join-Path $Release 'sync_cli.py') --db $Ledger backup --output (Join-Path $Backup ($Failure+'.sqlite3'))
if ($LASTEXITCODE -ne 0) {
    throw 'Ledger backup unavailable. Keep original database, journals and marker untouched; stay stopped.'
}
Copy-Item -LiteralPath ($Ledger+'.activation.json') -Destination (Join-Path $Backup ($Failure+'.activation.json'))
Copy-VerifiedTree (Join-Path $Code 'logs') (Join-Path $Backup ($Failure+'-logs'))
```

Keep the current ledger/marker and both old/new backups. Never restore an older
ledger over the latest one, delete a journal, reset a marker or create an empty
replacement to regain startup. If graceful stop/process exit cannot be proved,
stop the procedure without copying changing state or forcing termination.

The legacy source/config/environment backup is the rollback baseline, not an
automatic restart instruction. Legacy restart requires a separately approved
accounting of **every** post-cutover punch and uncertain remote outcome. Until then,
leave the sender stopped and preserve evidence; prefer a durable-compatible repair.
