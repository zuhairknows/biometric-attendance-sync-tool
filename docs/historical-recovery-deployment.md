# Historical recovery: deployment, operation and rollback

This is a release procedure for a **separately approved deployment**. Development
acceptance does not authorize using these commands on IT-SUPPORT. Recovery ships
disabled. See [development status and evidence](historical-recovery-development.md).

## Optional monitoring ACL compatibility

The baseline validator rejects the additional production monitoring reader.
For the reviewed opt-in development fix, see
[monitoring ACL compatibility](monitoring-acl-compatibility.md). Provision
DURABLE_MONITOR_READONLY_SID only in administrator-controlled service
configuration, after actual SID, effective access and isolated LocalSystem SCM
acceptance. Do not change ACLs, copy a placeholder SID or enable recovery to make
startup pass. Additional immediate children and configuration/ancestor trust can
still block deployment.

## Preserve the working installation

The production reference supplied for this mission is:

- Service checkout: `C:\GitHub\biometric-attendance-sync-tool`.
- Manager checkout: `C:\GitHub\biometric-sync-manager-29a440c`.
- Ledger: `C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3`.
- Existing private monitoring profile and narrow read grant to `MYFPF\s.taskea`.

Do not overwrite, stop, restart or modify these during development. Keep their
existing configuration, API credentials, device IDs, activation marker, cutover,
queue, holds, checkpoints, service account and ACLs. Never initialize a populated
ledger, raw-copy an open SQLite file, delete device logs or release all holds.

## Requirements and package verification

Acceptance baseline: Windows 11, Python **3.11 x64**, native Windows PowerShell
5.1 and local NTFS private storage. The service uses `requirements-service.txt`;
the manager uses `requirements-manager.txt`. `requirements.txt` additionally
installs the legacy Qt configuration UI. OpenSSL and the Windows .NET compiler
are test-only dependencies for the archived loopback/staging harnesses. Native
pywin32 must match the selected Python architecture/version; the staging packager
now reads pywin32 from the selected venv rather than requiring a base install.
The exact combined environment used for clean acceptance is frozen in
`docs/requirements-windows311-accepted.txt`; use that file to reproduce this
Windows/Python 3.11 validation without selecting newer transitive packages.

Use an approved Python executable path when `py` is an inaccessible Store alias.
From a **new development/installation directory**, not the active service folder:

The supplied `.bundle` carries the local reviewed `recovery` commits and history
without requiring remote publication. Verify its separate SHA-256 and run
`git bundle verify PATH_TO_REVIEWED.bundle`, then clone with
`git clone --branch recovery PATH_TO_REVIEWED.bundle biometric-attendance-sync-tool`.
Use the GitHub clone below only after the reviewed commit has been published.

```powershell
git clone --branch recovery --single-branch https://github.com/zuhairknows/biometric-attendance-sync-tool.git
Set-Location biometric-attendance-sync-tool
# Select the reviewed commit recorded in the acceptance report; do not deploy
# an unreviewed moving branch tip.
git checkout --detach REVIEWED_COMMIT
& 'C:\approved\Python311\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r docs/requirements-windows311-accepted.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

The source ZIP produced by `scripts/build_recovery_release.py` is an alternative
to clone for installation. It contains tracked source and templates, not a venv,
private profile, credentials or ledger. Verify its separate SHA-256 and every
entry in `release-info.json` before using it. It is not a preinstalled service or
an offline dependency wheelhouse. Frozen Phase 1 certification tests require a
Git clone containing their pinned historical objects; runtime does not need Git.
Build on a clean checkout with `python -B scripts/build_recovery_release.py`.
The builder also creates that Git bundle and its independent checksum.

## Backup and isolated service acceptance

Before any approved service change, record the actual SCM ImagePath/account,
loaded deployment revision, existing startup settings and effective token/ACLs
privately. Preserve private configuration outside the release archive. Obtain an
online consistent backup with the existing read-only SQLite backup command:

```powershell
.\.venv\Scripts\python.exe -B sync_cli.py --db 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3' backup --output 'C:\approved-private-backups\before-update.sqlite3'
.\.venv\Scripts\python.exe -B sync_cli.py --db 'C:\approved-private-backups\before-update.sqlite3' status
```

Run only after the appropriate host/read authorization. Output must be a new
absolute filename. Retain its independently approved hash and activation binding.
Validate integrity, schema version, delivery/hold counts, device watermarks and
retry metadata. A backup is evidence; it must never be provisioned as the active
worker while another writer is running.

Before production, install this release under an isolated Windows service identity
on a staging machine with synthetic devices and a staging destination. Verify the
actual service-token read/write access, full worker lifetime mutex ownership,
startup, clean stop, in-flight cancellation, restart and reboot. Python adapter
tests and the archived native host import checks do not certify actual SCM account
permissions for the new service. Inspect `private_storage` against the proposed
ACL: it intentionally rejects unknown principals. A successful administrator or
manager read does not establish service compatibility. Do not broaden the current
monitor grant or weaken the storage policy to make an install pass.

## Approved service installation/update

Only in the separately authorized maintenance window, install the reviewed code
and its venv. Restore the existing approved private `local_config.py` without
printing it. Keep recovery settings false. For a fresh installation only, follow
the existing [Phase 1 provisioning/cutover guide](phase1-durable-sync.md); an
existing ledger must retain its activation file and must not be reprovisioned.

The existing wrapper hosts SCM through its configured venv's `python.exe`, not
`pythonservice.exe`. From the approved deployment checkout with the intended
venv, the commands are:

```powershell
# Choose exactly one during an approved deployment:
.\.venv\Scripts\python.exe -B erpnext_sync_win.py install
# OR, for the existing registration:
.\.venv\Scripts\python.exe -B erpnext_sync_win.py update
```

These are real SCM mutations; this mission did not execute them. Do not run debug,
an ad hoc sender, or a second worker beside the registered service. Preserve the
service name/account and review its resulting executable, arguments and startup
directory. A separately approved stop/start and subsequent reboot must confirm
that this exact reviewed code is loaded and continuous polling resumes for all
12 identities without queue/checkpoint resets.

## Manager permissions and Windows login

Keep the current working monitoring manager/profile until replacement has been
separately accepted. Its `--monitor-only --profile PRIVATE_PROFILE --no-browser`
mode needs no recovery credentials or inbox writes and retains server-side route
denials. A per-user Windows Startup shortcut may run the existing
`Start Biometric Manager.bat` with those arguments; set its working directory to
that manager checkout. Create/update this shortcut only during approved rollout.
After login, verify the manager starts once, Dashboard/Devices refresh, recovery
URLs return 403, and the original service continues independently.

For an authorized recovery operator, use a separate private startup profile
containing approved `backup`, `backup_hash`, `contract`, `contract_hash`, `site`,
`output`, `device_config`, `recovery_ledger`, `recovery_inbox`, `recovery_summary`,
`recovery_summary_hash`, `recovery_service_sid`, `recovery_release` and
`recovery_release_hash`. Paths cannot come from browser forms. Keep diagnostics,
reports, snapshots and release evidence in dedicated private directories outside
the checkout and separate from each other and from the active ledger.

The operator needs only read access to the canonical ledger/activation and evidence,
write access to their reports/diagnostics and the approved inbox. The service needs
read access to pinned evidence and read/write to its canonical ledger and inbox.
Use the existing strict private-storage policy and independently verify effective
access under both real tokens. The manager never opens the ledger writable and
never obtains a POST transport. Do not expose `local_config.py` to the manager.

A normal recovery manager opens a private one-use `/unlock?token=...` launcher URL.
The launcher prints it locally for `--no-browser`; do not share or log that URL.
Opening `/recovery` in an unrelated browser session returns 403. The capability
establishes the launcher's session; file ACLs and service-side evidence validation
remain the execution authority. Reusing an unlock URL fails. Restart the manager
to authorize a new session; monitor-only and demo startup are unchanged.

## Release authorization and live recovery activation

**No release evidence was approved for production in this mission.** The template
`docs/recovery-release.template.json` is deliberately incomplete and disabled.
Follow the detailed source, permission and metadata review in
[the earlier readiness checklist](phase2-final-readiness.md). Require independently
reviewed evidence for:

1. Exact deployed GET/POST handlers, hooks, overrides, scripts and their side effects.
2. Complete Employee/Employee Checkin/get_count visibility, including terminal
   pages, independent control sets and restricted/inactive employee cases.
3. Employee database equality, mapping uniqueness, timezone, timestamp precision,
   NULL/blank log types and acceptance of the historical payload.
4. Destination duplicate atomicity or independently controlled absence of other
   writers for the selected identities. Repeat scans alone do not prove this.
5. Actual service/operator token, protected ACLs, single writer, restart/reboot,
   existing-state preservation and rollback compatibility.

After approval, an administrator prepares a private release document with exact
site origin, independently reviewed **numeric destination address**, canonical
ledger path, activation hash, comparison contract hash, approval reference and
evidence references for every template field. Numeric addressing avoids DNS in
the recovery polling lane; TLS still verifies the HTTPS hostname. No insecure TLS,
proxy, redirect or automatic POST retry is available. Site IP rotation requires
fresh address evidence and release approval; this is an intentional availability
tradeoff. All requests share the destination's existing payload/identity rules.

Generate `code_sha256` over every file returned by
`historical_attendance.release.code_files()` **on the reviewed installed package**.
It includes the service, historical modules and scripts. Hash actual file bytes,
not a Git tree or the ZIP itself. Record `approved_at` and `expires_at` as finite
Unix seconds with a maximum 24-hour lifetime, set `enabled` only after review,
independently approve the document's SHA-256 and protect it against modification.
Do not populate evidence references with placeholders or turn unknown claims true.

Pin the same document/hash in the service's private configuration and manager's
private profile. Set `HISTORICAL_RECOVERY_CONTRACT_SHA256`, approved inbox and
`HISTORICAL_REQUEST_AUTHOR_SID`; only then, during approved activation, set both
`HISTORICAL_RECOVERY_COORDINATION` and `HISTORICAL_RECOVERY_ENABLED` true. A flag
alone cannot open the gate. The service rechecks release hash, expiry, code,
activation, ledger, contract and evidence before transport. A mismatched/expired
release blocks recovery; ordinary unrelated delivery continues. Existing intent
fences remain effective even with recovery disabled.

## Operator workflow

1. Allow the existing service to retain available device history in its durable
   ledger, including cutover holds. The manager does not query devices directly.
   A missing/overwritten device log cannot be reconstructed from absence. The
   older incident-capture tool remains restricted to its approved two-device
   maintenance workflow and is not automatically broadened or invoked here.
2. Obtain and approve a fresh protected SQLite backup. Choose original device
   wall-time start/end (inclusive) and one/multiple/all device identities.
3. Run Historical Investigation. Without release evidence, production reads
   remain compatibility-only and cannot establish missing records. With a current
   approved release, production acknowledgement and all verified contract claims,
   the GET-only investigation can produce complete comparison evidence. Unknown
   visibility still yields unverifiable records, never a missing classification.
4. Inspect categories and private CSV/JSON. Pin the separately reviewed completed
   summary/hash in the startup profile. Preview itself is never authorization.
5. Open Historical Recovery Preview; select at most ten individually eligible
   records. Prepare the request with an independent investigation review reference.
6. Approve only checked records. Selected held rows require their individual hold
   checkbox and a separate hold-exception reference. Unselected holds and the
   global cutover boundary stay unchanged.
7. Supply the separate dispatch confirmation/reference. Dispatch writes an inert
   private request file; the manager never POSTs Checkins or starts a sender.
8. The existing writer processes at most one request per tick, after device capture.
   It records intent/audit before POST and independently compares the destination
   afterward. Only that proof can report Recovered/Already Exists.
9. Refresh results and export the private CSV/JSON audit. Queued, expired, cancelled
   and Needs Review outcomes must not be counted as recovered. Cancellation remains
   available after release expiry/revocation; it stops future work, not a committed
   remote request. An expired/cancelled uncertain intent requires independent review.

Limits: ten records, 128 persisted HTTP attempts across restarts, a 120-second
dispatch deadline, per-request absolute I/O limit of at most ten seconds, and a
three-second TCP connection limit. Cancellation is checked at most 100 ms apart
while waiting for I/O. No DNS, detached sender or retry thread is used. These are
I/O deadlines subject to OS scheduling; they are not hard real-time bounds for the
entire tick, which also performs hashing, SQLite, ACL checks and existing normal
delivery. Source verification is finite; normal capture runs before recovery.
The normal-overlap guard has its own finite budget. Do not promise an exact
worst-case polling interval or enlarge limits to force a large recovery through.

## Monitoring, interruption and rollback

Observe Dashboard/Devices, fetch freshness for each of the 12 IDs, queue/retry
counts, cooldowns, hold counts, private audit and service process continuity.
Investigate private diagnostics by reference; do not paste credentials or employee
rows into logs. Any failure retains durable intents and source evidence.

If release validation fails, preserve the evidence and keep recovery disabled.
If a POST outcome is uncertain, do not resubmit, delete intent/audit, reset to
pending, restore an older ledger or increase the approval deadline. Independently
review the destination and reconcile only with complete proof. A crash before
POST may consume submission permission; this intentionally favors no duplicate.

Rollback of the manager means stopping only the replacement manager and returning
to the preserved monitoring shortcut/profile. Rollback of the service needs a
separately approved stop/update/start and preservation of the **current** ledger,
activation, queue and audit. The schema additions are optional additive `sr_*`
tables; schema version remains 1 and checkpoints are not migrated. This does not
make arbitrary old senders safe: an older Phase 1 release predating intent fencing
must not run against unresolved recovery intents. Keep the reviewed fenced
delivery/ledger code, or block rollback until every affected identity is resolved
independently. Never drop `sr_intents` to achieve compatibility. Only an explicitly
approved disaster-recovery plan may restore a backup while all writers are stopped.

After reboot verify exact loaded code/venv/account, service running with one writer,
12-device polling/delivery, preserved queue/cutover/holds, private manager login,
monitoring-only route denials, disabled recovery by default and absence of replay.
Record evidence; a clean development suite cannot substitute for these checks.
