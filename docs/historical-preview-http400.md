# Historical preview request diagnostics

## Findings

The reported scope (2026-10-16 00:01:00 through 12:59:59 inclusive,
FP1_DEVICE_01, snapshot acknowledged) passes the baseline route validation.
A synthetic snapshot regression exercises the same browser datetime-local values,
including omitted zero seconds, and includes both boundary punches while excluding
13:00:00. The production request body was not captured; its exact rejection cause
is unknown. No production access or ERPNext requests were used.

Confirmed defect: request validation collapsed date, device/floor, acknowledgement
and form-shape failures into the same generic HTTP 400 page. Fixed messages now
identify these categories without rendering submitted values or exceptions. All
existing validation rules remain in force. CSRF/origin/host/mode gates are unchanged.
Snapshot/hash/ACL failures occur in the queued preview job and retain their existing
safe blocked-job messages; they are distinct from route rejection.

## Manager-only correction

After independent review and explicit installation approval:

1. Verify the installed manager_app/web.py matches the reviewed baseline; stop if
   it has unrelated local changes. Retain its original bytes/hash and ACL metadata.
2. Close only the investigation manager console. Leave the synchronization Windows
   service and the separate working monitoring manager installation unchanged.
3. An administrator replaces only the protected Phase 2 manager_app/web.py with
   the reviewed correction, preserving trusted ownership and protected permissions.
   No installer, service registration, runtime, dependency, ledger, activation,
   snapshot, startup profile or ACL-policy changes are required.
4. Relaunch the same investigation-only command with the same private profile,
   then reload the preview page to obtain the current session/form.
5. Select the intended dates/devices and acknowledgement. A valid request opens a
   job and publishes protected local CSV/JSON; an invalid form displays its fixed
   category. These reports describe local evidence, not confirmed ERPNext absence.

Do not replace the immutable RC2 ZIP or claim it contains this later correction.
No new release evidence is authorized: any future recovery release must bind the
actual installed code hashes again. If needed, revert only the saved manager module
while the investigation manager is closed; no ledger restore or service restart.

## Validation boundaries

Synthetic fixtures use the existing scoped ACL test seam, not production ACLs.
Native browser rendering and the actual production POST are not reproduced by the
Flask request tests. No safety gate is bypassed, and production is untouched.

## Controlled deployment commands (IT-SUPPORT only; not executed here)

Compatibility GO is conditional on the installed baseline hash check below.
Relevant synthetic regressions: 86 passed (preview, web, monitoring, service handoff,
HTTP SQLite and thread diagnostics). No application changes since that test run.
No full-suite rerun is required for this manager-only diagnostic change.

Extract the approved hotfix ZIP into C:\ApprovedMedia\FPF-preview-http400.
The artifact contains only manager_app/web.py, its manifest, SHA256SUMS.txt and
these instructions. Verify the ZIP's independently supplied SHA256 before extraction.
Do not overlay a full release or run the portable installer.

Close ONLY the investigation manager console first. Confirm no process is running
that protected installation's scripts\biometric_manager.py. Do not stop the Windows
service or the separate monitoring manager. Use a separately authorized elevated
PowerShell for this file operation; return to the normal user for launching.

```powershell
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ine 'IT-SUPPORT') { throw 'Wrong host' }
$Source = 'C:\ApprovedMedia\FPF-preview-http400\manager_app\web.py'
$Target = 'C:\Program Files\FPFBiometricSyncPhase2\manager_app\web.py'
$Backup = $Target + '.before-preview-http400.bak'
$Expected = '501c4b124ce01362350afe5621ad0919484562f87585322cf5fee07ad82653bf'
$AllowedOld = @(
  'c0e63b1a1c8dfa722cf2fedc3ef7c7d941771f009b5bf5cb19d2b20da6a98bfa',
  'e096581e63c53461534ffc9f3e63af18e97a22f5ad535fd8388e153c93695713'
)
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
if ((Hash $Source) -cne $Expected) { throw 'Hotfix hash mismatch' }
$OldHash = Hash $Target
if ($AllowedOld -cnotcontains $OldHash) { throw 'Installed baseline mismatch; STOP' }
if (Test-Path -LiteralPath $Backup) { throw 'Backup already exists; STOP and review' }
# Existing-file writes preserve the target security descriptor and file identity.
$OldAcl = (Get-Acl -LiteralPath $Target).Sddl
$Original = [IO.File]::ReadAllBytes($Target)
$Stream = [IO.File]::Open($Backup, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try { $Stream.Write($Original, 0, $Original.Length) } finally { $Stream.Dispose() }
if ((Hash $Backup) -cne $OldHash) { throw 'Backup verification failed; target untouched' }
try {
  [IO.File]::WriteAllBytes($Target, [IO.File]::ReadAllBytes($Source))
  if ((Hash $Target) -cne $Expected) { throw 'Installed hash mismatch' }
  if ((Get-Acl -LiteralPath $Target).Sddl -cne $OldAcl) { throw 'Target security descriptor changed' }
} catch {
  [IO.File]::WriteAllBytes($Target, $Original)
  if ((Hash $Target) -cne $OldHash) { throw 'RESTORATION FAILED: keep investigation manager closed' }
  throw
}
Get-FileHash -LiteralPath $Target -Algorithm SHA256
```

As MYFPF\s.taskea (not elevated), relaunch ONLY the investigation manager:

```powershell
& 'C:\Program Files\FPFBiometricSyncPhase2\.venv\Scripts\python.exe' -u -B `
  'C:\Program Files\FPFBiometricSyncPhase2\scripts\biometric_manager.py' `
  --investigation-only --profile 'C:\Users\s.taskea\AppData\Local\MYFPF\BiometricRecoveryReview\web-profile.json' --no-browser
```

Reload the preview page to obtain the current session, then use only the approved
snapshot workflow. A new HTTP 400 category is diagnostic evidence, not permission
to change verification settings. Snapshot/profile/backup/reports remain unchanged
by deployment; a later explicitly initiated preview creates its own private reports.

### Rollback (only after closing the investigation manager again)

Run in an authorized elevated PowerShell. This restores the saved exact baseline
module only, and does not reset any ledger or service.

```powershell
$ErrorActionPreference = 'Stop'
if ([Environment]::MachineName -ine 'IT-SUPPORT') { throw 'Wrong host' }
$Target = 'C:\Program Files\FPFBiometricSyncPhase2\manager_app\web.py'
$Backup = $Target + '.before-preview-http400.bak'
$AllowedOld = @('c0e63b1a1c8dfa722cf2fedc3ef7c7d941771f009b5bf5cb19d2b20da6a98bfa', 'e096581e63c53461534ffc9f3e63af18e97a22f5ad535fd8388e153c93695713')
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
$BackupHash = Hash $Backup
if ($AllowedOld -cnotcontains $BackupHash) { throw 'Unrecognized backup; STOP' }
if ((@('501c4b124ce01362350afe5621ad0919484562f87585322cf5fee07ad82653bf') + $AllowedOld) -cnotcontains (Hash $Target)) { throw 'Installed file drift; STOP' }
$OldAcl = (Get-Acl -LiteralPath $Target).Sddl
[IO.File]::WriteAllBytes($Target, [IO.File]::ReadAllBytes($Backup))
if ((Hash $Target) -cne $BackupHash -or (Get-Acl -LiteralPath $Target).Sddl -cne $OldAcl) { throw 'Rollback verification failed; keep investigation manager closed' }
Get-FileHash -LiteralPath $Target -Algorithm SHA256
```

Relaunch the same normal-user investigation-only command if required. Retain the
backup; no cleanup or production service operation is part of these commands.
