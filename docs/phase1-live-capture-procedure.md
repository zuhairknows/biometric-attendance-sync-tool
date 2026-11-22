# Safe historical capture — diagnostic utility, not synchronization

Development/publication only. **No live capture, Frappe operation, service operation
or production deployment was executed. We do not have a staging environment.**
Hardware behavior, actual SCM/ACL access under the production account and the
installed production SDK remain **NOT EXECUTED / NOT VERIFIED** by this task.

## Purpose and limits

Capture the currently stored, decoded attendance from only `FP2_DEVICE_08`
(`10.0.1.23:4378`) and `FP2_DEVICE_11` (`10.0.1.26:4381`) into protected local JSON
for offline reconciliation. The endpoints/passwords come from the selected devices'
literal approved config properties, not arbitrary command-line IP/port overrides.
Changing a permanent ID is not an IP-change workaround. Unknown/unapproved IDs fail.

Incident context supplied by the operator: old dumps end with empty-user/future-time
tail records, latest valid times October 13 01:13:43 (08) and October 11 01:15:55 (11),
and no attributed Frappe Checkins on October 14–15. This task did not independently
repeat that live audit. Only synthetic versions of those tails are used in tests.

The utility does not repair, remove, rename or replay those old dumps; restoring the
unchanged legacy service may encounter the same blocked dumps again. Captures are
evidence for a separately authorized reconciliation/recovery decision, not input
automatically fed to SQLite or Frappe. Do not change an import date or success-log
checkpoint to bypass the incident.
Do not rename/copy a capture to `<device_id>_last_fetch_dump.json` or put it in the
production retry/log directory; the evidence schema is not a synchronization dump.

There is no sync engine, `local_config` module import, Windows service entry point,
SQLite, HTTP/API client, Employee Checkin call or Shift Type update. The config is
parsed with `ast`; only selected device ID/IPv4/port/communication password values
are evaluated as literals. API values and unselected device credentials are not
evaluated. Dynamic device config is refused instead of executed.

## Required environment and reviewed diagnostic bundle

Use the existing service's Python interpreter and installed pyzk environment.
The existing checker requires pyzk 0.9 and the repository's SpeedFace 49-byte patch
marker. The utility checks compatibility but **never patches/installs a package**.
Do not run the patch script without `--check` during this procedure.

After separate approval, an operator can stage only these reviewed diagnostic
files in `C:\protected\biometric-capture-tools\scripts\`:

* `capture_device_history_readonly.py` — new capture CLI.
* `capture_history_safety.py` — new read-only SCM/ACL checks and file finalization.
* `apply_pyzk_speedface_patch.py` — existing unchanged checker helper.

Keep that `scripts/` layout and verify source hashes against the published Phase 1
commit in the final report. This does not require checking out/deploying Phase 1 on
the active production folder or changing any production service files. Never copy
the real production config/tokens into the bundle or Git. Use `-B` in the commands
below; the utility also suppresses SDK/helper bytecode cache writes.

Future operator variables (verify interpreter/log paths against real inventory):

```powershell
$SvcName = 'ERPNextBiometricPushService'
$PythonExe = 'C:\GitHub\biometric-attendance-sync-tool\.venv\Scripts\python.exe'
$CaptureTool = 'C:\protected\biometric-capture-tools\scripts\capture_device_history_readonly.py'
$DeviceConfig = 'C:\GitHub\biometric-attendance-sync-tool\local_config.py'
$EvidenceDir = 'C:\ProgramData\FPF-Biometric-Capture\20261015'
$Svc = Get-CimInstance Win32_Service -Filter "Name='$SvcName'"
$Svc | Select-Object Name, State, StartMode, StartName, PathName
Get-Date -Format o
Get-TimeZone
# Read-only SDK check; this flag is mandatory for the existing helper.
& $PythonExe -B 'C:\protected\biometric-capture-tools\scripts\apply_pyzk_speedface_patch.py' --check
```

`$PythonExe` is a proposed path: it must match the existing direct Python service
hosting interpreter in `PathName`. Do not update/reinstall the service to match it.

## Preflight and maintenance-window isolation

**All commands here are prepared, not executed.** Obtain explicit maintenance-window
approval before any live capture or service stop. Both `--capture` and the operator's
`--maintenance-window-confirmed` acknowledgment are required. A flag does not grant
approval by itself. There is no `--force` to bypass running-service detection.

Inspect recovery and launch policy read-only before the window:

```powershell
sc.exe queryex ERPNextBiometricPushService
sc.exe qc ERPNextBiometricPushService
sc.exe qfailure ERPNextBiometricPushService
sc.exe qfailureflag ERPNextBiometricPushService
```

Confirm intentional Stop will remain stopped: no pending failure-recovery restart,
reboot/startup task, watchdog, GUI launcher, foreground engine or other service.
Do not silently modify recovery settings. If quiescence cannot be assured, NO-GO;
obtain a separate approved operations plan. A clean manual stop does not mean all
possible external launchers have been excluded.

Additional read-only session checks for the operator:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python|attendance-sync|zk' } | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath
Get-ScheduledTask | Where-Object { $_.Actions.Execute -match 'python|attendance-sync|zk' } | Select-Object TaskName, TaskPath, State
Get-NetTCPConnection -RemoteAddress '10.0.1.23','10.0.1.26' -ErrorAction SilentlyContinue | Select-Object RemoteAddress, RemotePort, State, OwningProcess
```

Do not kill unrelated Python processes. Privately inspect relevant process/session
ownership and vendor polling clients, including other PCs. Do not export command
lines containing credentials. Empty TCP output cannot exclude UDP or a remote poller.
The stopped SCM check is enforced repeatedly, including immediately before connect
and after cleanup, but cannot lock out an uncoordinated external process or remove
the race between an SCM query and a later automatic restart.

## Protected evidence directory

Provision a dedicated local NTFS folder **only as separately approved**. It must
already exist; the utility creates no directories. It must be outside the checkout,
production folder, service data directory and tool bundle; no network/cloud share.
Folders containing status/dump/ledger/log markers are refused. Minimum initial free
space is 256 MiB; reserve substantially more for the estimated full device history.
Disk-full/write/flush failures return nonzero and never count as successful capture.

Example future administrator-only provisioning; do not run without approval:

```powershell
New-Item -ItemType Directory -Path $EvidenceDir -Force -ErrorAction Stop | Out-Null
icacls $EvidenceDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
if ($LASTEXITCODE -ne 0) { throw 'Evidence ACL provisioning failed' }
Get-Acl -LiteralPath $EvidenceDir | Select-Object Path, Owner, AccessToString
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID, FreeSpace
```

Review/remove unrelated broad explicit ACEs through an approved ACL change:
`/grant:r` does not remove every unrelated entry on a pre-existing folder. The
utility refuses unprotected inheritance and allow grants to Everyone, Users,
Authenticated Users, Guests, Interactive, Anonymous or domain Users/Guests. It
cannot infer the membership of every custom group; the operator must audit those.
It performs only a read-only ACL check in dry-run. Live mode additionally creates,
writes, flushes and removes a PII-free probe before opening each terminal. Run as
an authorized administrator or explicitly authorized identity with private access.

## Exact dry-run command

This does not import pyzk, connect to a device, write capture files, change service
state or touch production logs/state. It can inspect SCM status and directory ACLs.
A running service is reported as blocking live capture, but dry-run is still allowed.

```powershell
& $PythonExe -B $CaptureTool --config $DeviceConfig --devices FP2_DEVICE_08 FP2_DEVICE_11 --output $EvidenceDir --start '2026-10-14T00:00:00' --end '2026-10-15T23:59:59.999999' --timeout 10 --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Dry-run preflight failed; do not capture' }
```

Expect `mode: dry-run`, two device endpoints without passwords, directory/service
status and `writes_performed: false`. Omitting both `--dry-run` and `--capture` also
selects dry-run. There are no IP/port/password/clear/force overrides. Invalid arguments
are not echoed, so an accidentally supplied secret does not enter the report.

## Approved maintenance-window capture

First coordinate all polling/session owners and a physical terminal supervisor.
Only after separate authorization, stop the **existing** service manually:

```powershell
Stop-Service -Name $SvcName -ErrorAction Stop
(Get-Service -Name $SvcName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(120))
sc.exe queryex ERPNextBiometricPushService
```

Do not force-kill it on timeout. NO-GO if service stop is pending, paused, unknown,
unqueryable, absent, automatically restarting or another device session is active.
Keep the original retry dumps and all service files intact. Note start time and
device status. Verify the PC clock/timezone; filters and naive device times use the
PC's local wall clock, with no timezone conversion or device-time adjustment.

**Exact live command, only within that separately approved window:**

```powershell
& $PythonExe -B $CaptureTool --config $DeviceConfig --devices FP2_DEVICE_08 FP2_DEVICE_11 --output $EvidenceDir --start '2026-10-14T00:00:00' --end '2026-10-15T23:59:59.999999' --timeout 10 --capture --maintenance-window-confirmed
$CaptureExit = $LASTEXITCODE
```

Default capture never disables the terminal. Review of the [upstream pyzk read
method](https://raw.githubusercontent.com/fananimi/pyzk/master/zk/base.py) found no
enforced disable prerequisite; the current production wrapper does disable around
reads. This is SDK-source evidence, **not proof of these firmware models' behavior**.
If the operator/model requires a quiescent read, include `--pause-device` only in
the explicitly approved command/window. That sends disable/enable and can suspend
normal terminal interactions; it is not a zero-impact read. Enable is attempted in
`finally`, even if disable times out ambiguously. Cleanup finishes before serialization.
Do not test this mode on busy devices without a physical supervision plan.
A read while punches arrive is not proven to be a firmware-atomic snapshot; preserve
the returned sequence and cross-check anomalous tails against a device export.

The timeout is bounded to 1–30 seconds per configured socket operation, not a
whole-history deadline; SDK connection probes and repeated transfer chunks can
extend elapsed time. Do not interpret a slow full-history read as proof of a hung
process and kill it while the device may be paused.

## Output and validation

Each file is unique: `FP2_DEVICE_08_YYYYMMDD_HHMMSS_microseconds_uuid.json`, similarly
for 11, plus `capture_summary_...json`. No existing file is overwritten. Output uses
exclusive same-directory `.capture-*.part` files, flush/fsync and no-replace
finalization. Windows uses [MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
with WRITE_THROUGH and without REPLACE_EXISTING; POSIX uses a no-replace hard link.
An interrupted write leaves protected `.part` evidence, not a successful manifest.

Capture JSON contains source ID/host/port, start/end timestamps with PC offset,
cleanup state, full returned record sequence, total/valid/invalid/raw-incomplete
counts, oldest/newest valid times and requested-range counts. Aggregate reports
contain no employee IDs. Employee identifiers are inside protected evidence only.
UID is recorded as returned and is never treated as globally unique or deduplicated.

Raw fields use tagged JSON for datetime/date/time, bytes, tuples, mappings with
non-string keys and nonfinite floats. Ordinary values remain unchanged. Normalized
fields are separate. Cycles/opaque types are explicitly marked unavailable and
`raw_incomplete` increases; sensitive password/token/authorization-like fields are
redacted. The utility never executes repr/pickle to reconstruct an arbitrary object.
Do not certify raw completeness if `raw_incomplete` is nonzero.

Empty IDs, bad shapes/codes/timestamps and dates more than five minutes ahead of
the capture PC clock are flagged without dropping later records. Validity means
local structural/time sanity, not verified Frappe employee mapping. A record may
have several validation categories, so category counts can exceed invalid count.
The future-time rule is a diagnostic heuristic; raw future records remain intact.

**The complete device history is always retained.** `--start`/`--end` are inclusive
local datetime bounds for summary only; old and malformed rows are never discarded.
There is no `IMPORT_START_DATE` synchronization boundary in this diagnostic utility.
This is decoded SDK evidence, not a binary device backup. If pyzk fails while
decoding before returning a dataset, that device fails clearly; no empty successful
capture or guessed decoder repair is produced. Other selected devices continue.

Check stdout exit status (`0` completed capture/dry-run, `1` failed/blocked, `130`
operator interruption), per-device counts/cleanup and the stored aggregate summary.
The summary includes SHA-256 for each evidence file; its own hash is on stdout
because a file cannot contain its own content hash. A global failure can still leave
useful completed evidence for another device. Do not call the whole run successful.

Verify hashes read-only:

```powershell
Get-ChildItem -LiteralPath $EvidenceDir -Filter '*.json' | Get-FileHash -Algorithm SHA256
```

Compare each exact filename/hash to the manifest and stdout summary hash. Check
`total_fetched = valid + invalid`, record count/order, requested-range count and
oldest/newest times. Inspect employee-level JSON privately, never paste it into Git
or chat. Compare with a supervisor/device export where available; no live mapping,
device-to-Frappe reconciliation or Checkin creation is performed by this utility.

## Cleanup failures and post-capture restoration

Ordinary connect/read failure cleans up and continues to the next device. pyzk
cannot send a disconnect before a connection was established: after failed setup,
the utility closes its local socket and reports the unconfirmed connection instead.
An established session is explicitly disconnected. Failed re-enable/disconnect is critical: the run fails, reports manual
action and stops further capture. A lost connection/power loss/forced termination
cannot be made safe by Python `finally` alone. No terminal is intentionally left
disabled; a physical operator must confirm normal operation before proceeding.

If cleanup fails or an interruption occurs: keep the service stopped under the
approved window, verify/re-enable the terminal through authorized physical/vendor
operations, release stale sessions, confirm network reachability and preserve all
evidence. Do not blindly retry or restart polling while terminal state is unknown.
The utility never performs these manual operations or changes recovery policy.

After both captures and terminal/session checks, restore **only the service that
was running before the window**, with the operator's separate authorization:

```powershell
# Only after terminal enable/session verification; not an automatic utility action.
Start-Service -Name $SvcName -ErrorAction Stop
(Get-Service -Name $SvcName).WaitForStatus('Running', [TimeSpan]::FromSeconds(120))
Get-Service -Name $SvcName | Select-Object Name, Status
```

Record the restored state. If it was originally stopped, leave it stopped. Do not
edit/delete blocked dumps, install/update the service, enable durable mode or change
checkpoints/watermarks as part of restoration. This utility does not fix the blocked
production retry loop; recovery requires its own reviewed scope/approval.

There is no data rollback: the utility never modifies attendance or synchronization
state. Retain completed and partial files in protected storage; failed flush/finalize
or summary writes return nonzero. Check disk/ACLs and resolve the cause before a
new unique capture. Never overwrite earlier evidence or promote a partial file as
complete. Hash any separately retained partial evidence and label it incomplete.

## Security, retention and development verification

Access is limited to authorized reconciliation staff/administrators; protect copies
and backups equivalently. Use local NTFS, avoid shared/cloud folders and protect
against other privileged processes changing ACLs/files during capture. The utility
checks broad ACL grants but cannot prove custom group membership or stop an external
watchdog. Keep full snapshots for the incident's approved retention period; assign
an owner and obtain explicit approval for any eventual deletion. Nothing is deleted
automatically except the empty preflight probe / successfully finalized temp name.

Development changes: two new script modules, one fake-device test file, this guide,
README link and capture-evidence ignore patterns. Production engine/service/config,
legacy dumps, logs, SQLite and Frappe modules are unchanged.

Tests (all fake ZKTeco/SCM/ACL clients and isolated temporary folders):

```powershell
py -m unittest discover -s tests -p test_capture_device_history_readonly.py -v
py -m unittest discover -s tests -q
```

They cover dry-run isolation, allowlist/config safety, running/missing/unknown SCM,
no destructive method/override, two-device snapshots, synthetic incident tails,
date-range retention, unsupported types, restore/disconnect failures, file failures,
no overwrite, matching hashes, no credential/employee leakage in reports, protected
paths and SDK-check-only behavior. Native SCM/ACL APIs are mocked, not live-proven.
Actual Windows temp-file finalization is exercised on the development host.
October 15 verification: **129/129 complete-suite tests passed** in 34.535 seconds
(92 existing + 37 capture tests); **22 Python files** syntax-compiled without imports.
The procedure's PowerShell blocks and embedded read-only ACL script were parsed,
not executed. One test caught a Windows short-path/long-path comparison issue in
protected-root detection; canonical root resolution corrected it before the passing
run. No production capture was run. The final report supplies the published commit.
