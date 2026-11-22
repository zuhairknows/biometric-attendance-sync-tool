# Controlled Phase 1 cutover runbook — not executed

**Hardening candidate notice:** this runbook's commit pin and activation steps predate
the mandatory ledger provisioning/cutover guards. For the reviewed service-hardening
candidate, review [the hardening requirements](phase1-service-hardening.md) first.

**Production deployment is NOT authorized by release preparation.** Execute the
mutation sections only after separate approval and completed staging evidence.
Outcome today: development verified, ready for controlled staging; live hardware,
real Frappe and Windows SCM validation are NOT EXECUTED.

Tested code candidate: **222440465ca35df7f811642984e37ba5bd905d78**. The subsequent
release-preparation commit adds documentation only. Pin this tested code commit for
the proposed installation; do not deploy whichever commit happens to be latest on
the branch. Service identity, direct Python hosting, device IDs and mappings remain
unchanged. This runbook does not create/reinstall a service or a database schema
upgrade. Schema remains 1; unsupported/altered schemas are refused intact.

## 1. Inventory and read-only production-PC verification

Commands below are prepared for the production PC and **were not executed**.
First confirm these proposed paths against the actual service/config inventory.
In particular, `$PythonExe` must match the interpreter in the existing service's
`PathName`; `$LogsRoot` must match the actual log/status directory. Do not move old
logs because the template suggests a different location. Use absolute log paths in
the approved config to avoid SCM working-directory surprises. These variables are
task-specific and never replace HOME or other system settings.

```powershell
$CodeRoot = 'C:\GitHub\biometric-attendance-sync-tool'
$PythonExe = 'C:\GitHub\biometric-attendance-sync-tool\.venv\Scripts\python.exe'
$LogsRoot = 'C:\GitHub\biometric-attendance-sync-tool\logs'
$LedgerPath = 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3'
$ApprovedCodeCommit = '222440465ca35df7f811642984e37ba5bd905d78'
$SvcName = 'ERPNextBiometricPushService'
$Svc = Get-CimInstance Win32_Service -Filter "Name='$SvcName'"
$Svc | Select-Object Name, State, StartMode, StartName, PathName
git -C $CodeRoot status --short
git -C $CodeRoot rev-parse HEAD
git -C $CodeRoot check-ignore local_config.py
git -C $CodeRoot ls-files -- local_config.py
& $PythonExe -c "import sys,sqlite3; print(sys.version); print(sqlite3.sqlite_version)"
& $PythonExe (Join-Path $CodeRoot 'scripts\apply_pyzk_speedface_patch.py') --check
```

The new helper is available from the reviewed candidate checkout, copied to a
protected tools directory if needed before deployment. It must not be confused
with changing the active checkout:

```powershell
# On the reviewed candidate checkout; no config execution, ACL edits or service operations.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\protected\phase1-tools\verify_phase1_pc.ps1' -CodeDirectory $CodeRoot -PythonExecutable $PythonExe -DatabasePath $LedgerPath
```

If the source helper is copied, compare its hash with the reviewed candidate first.
Omit `-ExpectedCommit` while examining the old installation; pass the approved code
commit only after deployment. The helper reports operational config literals and
device/shift mapping without printing API tokens or device passwords. It parses
config rather than executing it. A nonliteral config requires a protected manual
audit. It verifies a present ledger read-only and never creates a missing ledger.

Inventory: retain the old exact revision, interpreter, dependency versions, Windows
timezone/clock, service state/account/path, all 12 configured stable device IDs,
model/firmware/IP/port, import boundary, direction/geolocation configuration, and
every Shift Type mapping/watermark. Confirm one worker only, no legacy GUI launcher
or second sync process. Do not use GUI setup to rewrite the production config.

Port-only reachability checks (future operator commands; do not run on this PC):

```powershell
Test-NetConnection -ComputerName '10.0.0.20' -Port 4371 -InformationLevel Detailed
# Repeat for every IP/port in the inspected production config, including each mapped device.
```

TCP reachability alone does not prove decoding or attendance retrieval. All mapped
devices must be reachable from the approved worker. Do not silently remove a device
or shift mapping to pass a gate.

## 2. Before-mutation GO/NO-GO gates

GO requires all of these:

* Separate approval, maintenance window, owner and recovery contact recorded.
* 92/92 offline tests and ten loopback HTTP scenarios passed for the code candidate;
  all applicable [live staging tests](phase1-integration-tests.md) have actual
  evidence, including every configured device model, real Frappe response formats,
  backups/restoration and measured Windows SCM restart/stop behavior.
* Existing service/interpreter/account match inventory; exactly one worker; no
  local tracked edits except the ignored protected production config/logs.
* Device IDs/mappings unchanged; all deletion flags False; valid original import
  boundary; boolean durable flag; runtime timeout/lease/backoff settings valid.
* Database is external on local NTFS; only service identity/SYSTEM/authorized
  administrators can access it; backups/config are also protected. No broad Users
  or Everyone write/read grants to attendance/credential directories.
* At least 2 GiB free and capacity for two database backup copies plus 30 days of
  measured staging growth; monitoring/retention ownership assigned. Ledger retention
  is indefinite, so future capacity reviews are required.
* Every legacy dump inventoried and hashed; malformed, unknown-device, oversized or
  failed migration sources resolved. Unreviewed quarantine is a NO-GO. An explicitly
  accepted quarantine plan must acknowledge that watermarks stay blocked.
* Existing remote watermarks and eligible historical punches audited. No assumption
  that old success-log checkpoints prove delivery. An already overadvanced watermark
  needs a separately approved attendance correction plan, not an automatic reset.

Any failed/unexecuted gate means **NO-GO for production**. It does not prevent
controlled staging. Do not waive a defect by marking quarantined punches successful.

## 3. Stop safely and back up configuration, source evidence and logs

**Mutation section: requires deployment approval.** Make no config change while
the old worker is running. Record the stop time and wait for Stopped; if the stop
does not complete within the SCM limit proven on staging, abort and investigate.
Do not use a forced kill as the ordinary cutover procedure.

```powershell
# Provision/review this protected root before any credential copy.
$ProtectedBackupRoot = 'C:\protected\biometric-backups'
New-Item -ItemType Directory -Path $ProtectedBackupRoot -Force -ErrorAction Stop | Out-Null
icacls $ProtectedBackupRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: backup ACL provisioning failed' }
Get-Acl -LiteralPath $ProtectedBackupRoot | Select-Object Path, Owner, AccessToString
# Stop here if unrelated explicit Users/Everyone grants remain; do not copy credentials yet.
Stop-Service -Name $SvcName -ErrorAction Stop
(Get-Service -Name $SvcName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(120))
$CutoverBackup = Join-Path $ProtectedBackupRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $CutoverBackup -ErrorAction Stop | Out-Null
git -C $CodeRoot rev-parse HEAD | Set-Content -LiteralPath (Join-Path $CutoverBackup 'old-revision.txt')
Copy-Item -LiteralPath (Join-Path $CodeRoot 'local_config.py') -Destination (Join-Path $CutoverBackup 'local_config.py') -ErrorAction Stop
Copy-Item -LiteralPath $LogsRoot -Destination (Join-Path $CutoverBackup 'logs') -Recurse -ErrorAction Stop
Get-ChildItem -LiteralPath (Join-Path $CutoverBackup 'logs') -Filter '*_last_fetch_dump.json' | Get-FileHash -Algorithm SHA256 | Export-Csv -LiteralPath (Join-Path $CutoverBackup 'dump-hashes.csv') -NoTypeInformation
```

Provision `C:\protected\biometric-backups` with administrator-only access **before**
copying credentials. Take an existing ledger online backup as well if one exists,
using `sync_cli.py backup`; retain any hot rollback journal after an abnormal stop.
Never copy a changing SQLite file as a backup. No automatic dump/log deletion.

## 4. Provision external SQLite directory and permissions

Use the inventoried account, not an assumed new service user. Example below supports
LocalSystem or a resolvable existing service account, without changing that account.
It adds no broad interactive-user access. Record the resulting ACL and verify
read/write operation under the actual service identity on staging.

```powershell
$LedgerDirectory = Split-Path -Parent $LedgerPath
New-Item -ItemType Directory -Path $LedgerDirectory -Force -ErrorAction Stop | Out-Null
$ServiceSid = if ($Svc.StartName -eq 'LocalSystem') { 'S-1-5-18' } else {
    ([Security.Principal.NTAccount]::new($Svc.StartName)).Translate([Security.Principal.SecurityIdentifier]).Value
}
icacls $LedgerDirectory /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "*$($ServiceSid):(OI)(CI)M"
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: database ACL provisioning failed' }
Get-Acl -LiteralPath $LedgerDirectory | Select-Object Path, Owner, AccessToString
```

Review pre-existing explicit ACEs: `/grant:r` does not remove unrelated explicit
grants. If Users/Everyone retain access, stop for an approved ACL correction. Do not
recursively rewrite unrelated directory ACLs. Reject a database path inside the
checkout, a network share, cloud-sync folder or directory that belongs to another app.

## 5. Deploy the pinned source with durable mode still disabled

Do not merge, reset, force-push, install or update the Windows service. These are
future deployment commands, not part of development publication:

```powershell
git -C $CodeRoot fetch origin feature/phase1-durable-sync
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed' }
git -C $CodeRoot cat-file -e "$ApprovedCodeCommit^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Approved source commit unavailable' }
# Only after confirming the old tracked tree is clean and the backups are usable:
git -C $CodeRoot checkout --detach $ApprovedCodeCommit
if ($LASTEXITCODE -ne 0) { throw 'Checkout failed; do not overwrite/stash local work automatically' }
```

All runtime modules must be deployed together. `local_config.py` is ignored and
must stay protected with its original credentials/device settings/mappings.
Preserve the old Python environment and installed SpeedFace patch. Do not run
`install.py`, GUI setup, dependency upgrades or service install/update commands.

## 6. Preview and apply legacy migration while the service is stopped

Use the protected backup directory as source. The CLI loads the trusted original
config only for mapping; it never contacts devices/Frappe or reads log checkpoints.
Preview opens an existing DB read-only or creates nothing if it is absent.

```powershell
Set-Location -LiteralPath $CodeRoot
$LegacySource = Join-Path $CutoverBackup 'logs'
& $PythonExe .\sync_cli.py --db $LedgerPath import-legacy --config (Join-Path $CutoverBackup 'local_config.py') --directory $LegacySource
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: legacy preview failed' }
# Review every file/count/hash. Then apply only as part of the approved cutover:
& $PythonExe .\sync_cli.py --db $LedgerPath import-legacy --config (Join-Path $CutoverBackup 'local_config.py') --directory $LegacySource --apply
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: legacy migration failed; originals retained' }
& $PythonExe .\sync_cli.py --db $LedgerPath verify
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: ledger integrity/schema failed' }
& $PythonExe .\sync_cli.py --db $LedgerPath status
& $PythonExe .\sync_cli.py --db $LedgerPath backup --output (Join-Path $CutoverBackup 'before-activation.sqlite3')
if ($LASTEXITCODE -ne 0) { throw 'NO-GO: pre-activation database backup failed' }
```

Gate: file failures 0; source hashes unchanged; first imported/known/excluded/invalid
counts reviewed and match inventory. Re-run preview: newly importable records must
be 0 for unchanged sources (already-known/excluded counts may be nonzero). Invalid
counts are a subset, not additional imported rows. Empty source directory is not
proof that expected dumps were migrated; compare with the pre-stop inventory.

Backups must be usable: verify the backup file independently and compare total and
state counts with the source. A failed file transaction does not undo earlier
successful file imports; each report/digest is committed per file. Retain all files.

## 7. Activate durable mode and verify startup

In the approved production config, **manually** add/update the durable options from
the template. Set `DURABLE_SYNC_ENABLED = True` (a boolean) and the inspected absolute
`DURABLE_DB_PATH`. Keep credentials, all stable IDs, date boundary, direction,
geolocation, device flags and Shift Type mappings unchanged. Preserve valid legacy
status as a read-only watermark floor; a malformed status snapshot blocks Shift
Type updates until audited and the worker is restarted under approval.

Verify config syntax without importing it (the helper does not print secrets),
then verify candidate SHA/path/ACLs before starting the existing service:

```powershell
& .\scripts\verify_phase1_pc.ps1 -CodeDirectory $CodeRoot -PythonExecutable $PythonExe -DatabasePath $LedgerPath -ExpectedCommit $ApprovedCodeCommit
if (-not $?) { throw 'NO-GO: deployment verification failed' }
Start-Service -Name $SvcName -ErrorAction Stop
(Get-Service -Name $SvcName).WaitForStatus('Running', [TimeSpan]::FromSeconds(120))
Get-Service -Name $SvcName | Select-Object Name, Status
& $PythonExe .\sync_cli.py --db $LedgerPath status
```

No first-start dump replay occurs. **Ordinary polling still re-observes retained
eligible device history from IMPORT_START_DATE.** A fresh ledger may POST those
historical punches once and complete them as known remote duplicates. That expected
cutover workload is not a success-log migration and must be sized/reconciled.

## 8. First cycles, per-device backlog and Checkin reconciliation

Observe at least **three complete poll cycles** and compare snapshots. Require all
configured devices to have `fetch_ok=1` for the latest scans, no critical storage
errors, capture count stable on repeated unchanged history and zero additional
POSTs for completed source tuples. Pending/retry age should decrease once the
destination is healthy; sustained 401/403, unknown responses, failed scans, unreviewed
quarantine or a stalled watermark are NO-GO for declaring cutover successful.

Read-only per-device SQL audit (no user IDs/payloads printed):

```powershell
$DeviceAudit = @'
import json,sys
from sync_ledger import Ledger
with Ledger(sys.argv[1],readonly=True) as db:
    rows=db.db.execute("SELECT d.device_id,d.fetch_ok,d.last_fetch_attempt,d.last_successful_fetch,COUNT(p.id) AS captured,COALESCE(SUM(p.state NOT IN ('synced','duplicate')),0) AS unresolved FROM device_state d LEFT JOIN punches p ON p.device_id=d.device_id GROUP BY d.device_id").fetchall()
    print(json.dumps([dict(r) for r in rows],indent=2))
'@
& $PythonExe -c $DeviceAudit $LedgerPath
& $PythonExe .\sync_cli.py --db $LedgerPath verify
Get-Content -LiteralPath (Join-Path $LogsRoot 'error.log') -Tail 40
Get-Content -LiteralPath (Join-Path $LogsRoot 'logs.log') -Tail 40
```

Use a protected read-only database inspection/export for full reconciliation, not
Git or chat. In Frappe Desk, export Checkins for the cutover capture timestamp range
and relevant employees/devices plus records created before cutover that could be
duplicates. Resolve original source pins against the audited Employee Attendance
Device ID crosswalk. Every `synced` row must match its confirmed Checkin name,
resolved Employee/time and submitted fields. Every `duplicate` row must match an
existing Employee/time without modifying that document. Duplicate records may be
older or have another source device ID: server identity is not the full source
tuple. Do not equate completed ledger row count to newly created remote Checkin
count. Account for every eligible cutover punch, including quarantined/unresolved.

For every Shift Type mapping, verify real `last_sync_of_checkin` against the earliest
unresolved eligible timestamp and scan-start minimum. An unknown timestamp/failed
mapped scan blocks updates entirely; permanent errors also cap advancement. Check
remote watermarks in Desk; do not automatically decrease already overadvanced
historical watermarks or modify existing Checkins to make the audit pass.

Successful cutover requires full interval accounting, no unexpected duplicates,
correct Checkin fields and safe watermarks, plus a post-cutover verified backup.
Staging fault injection proves retry behavior; do not deliberately disable production
Frappe, rotate production tokens or fill the production disk for tests.

## 9. Rollback and forward recovery

If any post-start gate fails: **stop the single worker, record the interval and take
a fresh protected ledger backup before any code/config rollback.** Preserve latest
database, any journal, source history, legacy dumps, logs and both pre/post backups.
Do not overwrite the latest ledger with the pre-activation backup. That would lose
knowledge of punches captured after activation and of remote commits awaiting local
confirmation. Do not delete Checkins or mark unresolved ledger rows successful.

Preferred recovery: deploy a reviewed durable-compatible correction or the pinned
tested durable candidate against the **latest intact ledger**, correct external
availability/permissions, then let pending/retry/expired leases resume. Resolve
quarantine through separately reviewed targeted operator work; Phase 1 has no mass
requeue or historical recovery UI. Never change the Frappe destination for a ledger
containing records from another site; use separate staging/production databases.

If the database is damaged, retain original files and investigate under maintenance
approval. Restore a verified backup to a **new** recovery file first. Reconstruct
the interval after that backup from retained devices/dumps and protected exports,
then reconcile remote creations and missing confirmations before switching the
worker to that reviewed recovery ledger. Do not create an empty replacement DB to
report healthy status. A hot-journal read-only verification failure also needs an
approved SQLite recovery step, not journal deletion.

A return to the legacy engine is allowed only after every post-cutover captured
punch is reconciled and all six unresolved state counts (`pending`, `retry`,
`delivering`, `unmatched_employee`, `rejected`, `invalid_record`) are zero, or a
separately approved complete recovery procedure accounts for each retained row.
After approved service stop/backup, check out the recorded old exact revision,
restore the prior protected config and preserve the ledger indefinitely. Legacy
logs were not updated as delivery checkpoints by durable mode; expect retained
history to be re-observed and verify duplicate behavior again. Do not allow legacy
checkpoints/watermarks to advance while SQLite-only unresolved records are stranded.
If interval accounting cannot be proved, stay stopped and use forward recovery.

Do not reinstall/update the service, reset/merge stable/main, alter mappings, delete
device history or downgrade a newer database schema in place during this procedure.
