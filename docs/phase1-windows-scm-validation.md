# Final Windows SCM validation — prepared, not executed

> Historical procedure: core Phase 1 deployment and integration-account GET checks have completed. Do not rerun the commands below as a current next step. See [current Phase 1 status](phase1-status.md); Auto Attendance remains blocked pending a separately verified safeguard.

**Current next action: one service-token mutex diagnostic below. Do not repeat
idle startup or recovery. The recovery evidence already shows one synced, zero
pending, intact integrity and the expected service SID.**

## One actual service-token mutex diagnostic

Run once in **Administrator PowerShell on ZUHAIR-PC under the staging user**:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\GitHub\biometric-attendance-sync-tool\scripts\run_phase1_service_mutex.ps1 `
  -StageRoot C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019 `
  -AttemptName scm-524378a7758046dfa7d518a62ed79259
```

The standalone host imports no integration/engine/ledger modules or configuration.
It starts once under `NT SERVICE\FPFPhase1Staging`, verifies the actual process
TokenUser SID, makes one unowned `CreateMutexW` call, immediately captures its
numeric last error, closes any returned handle and exits. It never waits for or
acquires mutex ownership. Its audit guard rejects sockets, subprocesses, registry
access and SQLite connections, and permits file writes only to its unique report.
No ledger is opened, including for hashing. Only runtime program files are hashed.

The runner reuses the existing private Python/runtime and unchanged firewall
rules. It adds a unique source copy to the existing protected code directory; the
actual service creates its report in the existing data directory, without ACL
changes. Only the staging ImagePath is temporarily changed, then restored in
`finally` after a clean stop. Account, startup mode and other registration values
are not changed; final registration fields are compared with their original
values. No service is installed/deleted or forcibly terminated. Existing source,
reports, attendance evidence and runtime are not overwritten.

**PASS:** exit 0 and `SERVICE MUTEX DIAGNOSTIC PASS`, with the expected actual SID
and service-owned report, handle opened/closed, clean stopped service and restored
original registration. `win32_error=0, existed=false` means this attempt created
a temporary unowned mutex; `183, existed=true` means an existing mutex was opened.
Neither result alone reproduces the earlier administrator-versus-service-owner
condition. If the last handle closes, the temporary mutex disappears.

**FAIL:** preserve the printed report/result paths and stop. A NULL handle gives
the actual numeric `diagnostic.win32_error` captured under the service token.
Do not rerun recovery, change permissions or relax validation. The command never
controls the production service or connects to Frappe/devices.

One focused mocked test passed for API failure 5, success with a full 64-bit
handle, rejection of a wrong token, exclusive/sanitized reporting and PowerShell
syntax/scope. It performed no actual service, mutex, permission or ledger action.

Reviewed SHA-256:

- Service probe: `e6a1e93b29f5b6c5bcb21d50611d6d8578414bfc4da79533fa438a6c120bb9d3`.
- Runner: `71b38878cffff372caf8be1b94032497e49333bf915ab06014913ad99920f7ff`.

## Mutex inspection diagnostic only

Run in **Administrator PowerShell on ZUHAIR-PC**, matching the failed inspector's
account/elevation. This does not start/stop any service:

```powershell
py -3 -I -S -B C:\GitHub\biometric-attendance-sync-tool\scripts\phase1_staging_scm_host.py --mutex-error-only
```

The staging inspector declares `CreateMutexW(void*, BOOL, LPCWSTR) -> HANDLE`
using `WinDLL('kernel32', use_last_error=True)`. It captures
`ctypes.get_last_error()` immediately after that call, before any other Windows
call or cleanup, and includes the numeric `win32_error` in JSON. A NULL handle
returns FAIL with fixed code `worker_lock_unavailable`. The normal staging
`--inspect` path uses the same capture; the pinned durable engine is unchanged.

This standalone mode loads no bundle, configuration or ledger and never waits
on/acquires mutex ownership. A successful handle is immediately closed. If the
object was absent, CreateMutexW briefly creates an unowned object and closing
its final handle destroys it. `existed: false` cannot establish the earlier
service-held condition. If the failure is no longer present, the original
Windows error cannot be recovered retrospectively; do not restart recovery to
reproduce it. No permission, ledger, service or production changes are made.

One focused mocked-API test passed for NULL/error 5, existing-object/error 183,
and new-object/error 0. It verifies 64-bit HANDLE typing, immediate capture,
preservation through cleanup and no configuration/ledger access. No real mutex
probe or SCM session was executed by the test.

API references: [CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw)
and [ctypes last-error handling](https://docs.python.org/3.14/library/ctypes.html#ctypes.get_last_error).

## Remaining SCM session: inflight stop and restart recovery

Run once on ZUHAIR-PC in **Administrator PowerShell under the staging user**:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\GitHub\biometric-attendance-sync-tool\scripts\run_phase1_staging_scm.ps1 `
  -StageRoot C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019 `
  -RemainingFromAttempt scm-e020445d0ad9458fab04f9589c22f6f4 `
  -IdleEvidence C:\GitHub\biometric-attendance-sync-tool\.scm-idle-bootstrap-7624e5b6a1a94cf3a22f7f99c03ae7ef.json
```

This reuses the accepted idle PASS report, its private runtime and existing
firewall rules. It skips runtime packaging, native import preflight, idle startup
and all earlier acceptance/offline scenarios. Existing read-only startup safety
assertions remain in place; ACL/firewall acceptance tests are not rerun. Only new
private session directories/evidence receive the existing reviewed permissions;
old directories, runtime, ACLs and firewall rules are never changed. A new empty
synthetic ledger is provisioned once with a fresh future cutover; both service
starts reuse that exact ledger, marker, settings and localhost HTTPS mock.

- **Inflight:** start only `FPFPhase1Staging` with `inflight`. After real SQLite
  INSERT but before COMMIT, request stop and observe SCM STOP_PENDING. The bounded
  capture finishes, one pending punch remains, zero punches are sent, the service
  stops cleanly within 30 seconds and its mutex is released with ledger integrity
  intact.
- **Restart:** start the same staging ImagePath with `recover`. One synthetic
  punch is delivered to the localhost HTTPS mock; pending becomes zero, synced
  becomes one. Confirm actual SID/PID and lifetime mutex ownership, then a clean
  stop under 10 seconds and released mutex/integrity.

**PASS:** exit 0 and `REMAINING SCM PASS: 14 checks`; the printed new
`scm-...\data\result.json` has all 14 checks true. Require exactly one mock HTTPS
record, stopped mock/service, restored original staging registration, and unchanged
old evidence plus accepted idle report. This completes the remaining SCM scenarios
only; production activation remains separately gated.

**FAIL:** anything else. Preserve the new session directory and stop. Do not
retry, overwrite evidence or force-kill a service. The runner gracefully stops
only its own staging ImagePath/account and restores registration only when
stopped. No production service is queried/controlled and no production/device
connections are allowed.

A new preparation test checks PowerShell syntax, the exact two-phase selection,
and exclusion of runtime/firewall creation from remaining mode. It invokes no
SCM, ACL or firewall operations and does not repeat previous successful tests.

## Confirmed ancestor metadata permission failure

The retained adapter hash matches its report. Its lines 437/115 call `context()`
and then `lstat()` for every ancestor. The code and attempt directories grant
the staging SID ReadAndExecute. The next path, the staging root
`C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019`,
does not grant that SID metadata read access. Neither do its `Local`, `AppData`
and user-profile ancestors. This is an ancestor ACL mismatch, before any bundle
file read; the location is correct and full ancestor validation remains required.

`repair_phase1_staging_ancestor_metadata.ps1` prepares the narrow correction:
ReadAttributes plus Windows' automatic Synchronize, for this staging SID on
exactly those four directories, **this folder only**. No listing, contents,
ReadPermissions, write rights or inheritance are added. Existing descriptors
are backed up to a new repository-local JSON before applying; all existing ACEs,
owners and inheritance settings are preserved and the resulting descriptors
are checked. No firewall/service/engine changes are performed. The script is
read-only without `-Apply`; application requires administrator PowerShell.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\GitHub\biometric-attendance-sync-tool\scripts\repair_phase1_staging_ancestor_metadata.ps1 `
  -Resume `
  -OriginalBackup C:\GitHub\biometric-attendance-sync-tool\.scm-ancestor-acl-backup-8e2ba803760f4db1a16eed429ca8c512.json `
  -Apply
```

The first repair completed the staging root and `Local`, then stopped because
Windows added `D:AI` on `Local`. The corrected verifier permits only addition of
that control flag, comparing owners/groups, every ACE (including order, rights,
SID and inheritance), DACL protection, other control flags and SACL entries
strictly. It does not generally ignore SDDL differences.

Resume validates all four directories against the preserved original backup
before any write, deriving allowed grants independently from the original
descriptors. The completed staging root and `Local` are verified without writes.
Only the missing `AppData` and profile grants are planned; changed/unexpected
permissions stop preflight. A new backup records pending changes while the
original backup remains unchanged. Already completed grants are never reapplied.

This resume is prepared, **not applied by Codex**. A read-only plan against the
current four descriptors confirms exactly those two pending writes. Three
focused tests passed: strict verifier cases, synthetic partial-state/no-op/drift
resume with mocked ACL writes, and exact metadata-only .NET rights. Stop after
the administrator command; do not start SCM. Effective service-token success
after correction remains pending.

## Focused idle-only bootstrap diagnostic

Startup registration validation records previous SCM exit codes as history:
the restored/stopped staging registration can retain `1066/1` from the prior
constructor failure. Identity, exact original ImagePath, Manual mode, Stopped
state and PID zero remain mandatory. The NEW attempt must still finish with
both exit codes zero. A focused mocked-registration test passed without SCM.

The `bfbc32bca00a4df4b2cc0d88051e036f` attempt passed bundle validation,
service-token verification and control-handler registration. It failed at
`synthetic_provision_begin`: pinned `durable_startup.py:171` raises
`new_activation_requires_future_cutover`. The reused settings file's cutover had
expired. The adapter now creates an in-memory settings copy with a fresh local,
second-precision boundary two minutes after each bootstrap attempt begins.
The retained settings file and pinned future-cutover validation are unchanged;
this applies only to the new synthetic bootstrap ledger. Two focused tests
reproduce stale-cutover refusal and verify fresh provisioning plus evidence
preservation without SCM, network calls or ACL changes.

Run on ZUHAIR-PC in **administrator PowerShell under the original staging user**:

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_phase1_scm_bootstrap_idle.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019' `
  -AttemptName 'scm-e020445d0ad9458fab04f9589c22f6f4'
if ($LASTEXITCODE -ne 0) { throw 'Preserve bootstrap evidence and stop. Do not retry.' }
```

This reuses the retained, hash-verified private Python runtime and checks its
existing firewall rules in both policy stores. It does not create/change rules,
accounts, ACLs or Windows timeouts. It verifies the stopped original staging
registration before changing only its ImagePath, then starts **idle once**, stops
gracefully and restores that registration. The pinned engine/wrapper/base remain
unchanged. There is no mock startup or attendance submission in this diagnostic;
the additional idle audit guard rejects every socket operation.

Only a uniquely named reviewed adapter copy is added to the existing `code`
directory. The service process creates its own unique `bootstrap-<id>.jsonl`,
empty synthetic ledger/activation and prefixed idle reports in the existing
protected `data` directory. It provisions only that new synthetic ledger under
its own token; old ledgers and reports are not reused for writes. Existing file
hashes are checked again after stopping. The operator result is a new
`.scm-idle-bootstrap-<id>.json` in the development repository, keeping
administrator-owned report files out of the ledger directory.

The bootstrap log records script entry/Python initialized, standard-library and
bundle loading, native dependency loading, dispatcher initialization/registration,
entry into the dispatcher, service constructor/control-handler registration,
synthetic provisioning, idle completion and clean return. It records PID/time,
exception class, integer OS error codes and traceback **file basenames/line
numbers only**. It never includes exception messages, source lines, locals,
command arguments, environment or settings. Log ownership must match the actual
staging SID; readiness SID/PID must match SCM. Python cannot log before the
interpreter starts, nor if Windows denies creation of its log: missing first
marker is diagnostic evidence, not proof of a particular failure cause.

**PASS:** exit 0, `IDLE BOOTSTRAP PASS: 7 checks`, all seven result checks true:
actual token/process, empty intact synthetic ledger, clean stop under 10 seconds,
original registration restored/stopped, service-owned log, complete bootstrap
markers without exceptions, and unchanged previous evidence. Firewall validation
must also succeed. This proves idle bootstrap only, not inflight/restart acceptance
or production readiness.

**FAIL:** anything else. Preserve the printed diagnostic log and result paths.
If a log exists, its last marker and sanitized exception locate the failure.
Never force-kill a service, increase timeouts, repair permissions or blindly rerun.
Cleanup requests a graceful stop only for the exact diagnostic ImagePath/account;
it restores registration only when safely stopped. No production service is
queried or controlled.

Reviewed SHA-256 (2026-10-16):

- Adapter: `486067a8a79a79f9296eb9e99734f6b6f6df73c99b47f6708ff5cb74fecfd9a3`.
- Idle runner: `32da6d93b8bdb15e373bc06fc45cfbb54cd841e75c49c2062e7158265af20fa9`.

The idle runner uses the enum-based administrator membership check, matching
the other staging runners. The string `Administrator` can fail account-name
lookup even when the built-in administrator role is effective. Four focused
bootstrap regression tests, including this guard check and PowerShell syntax,
passed after the correction; no SCM session was executed.

Four focused automated tests passed, including the real private runtime/native
registration with the dispatcher replaced, deliberate socket denial, early
failure/redaction/no-overwrite checks and PowerShell parse/scope checks. No real
SCM session or staging permission/firewall operation was executed by these tests.

This closes only the real SCM gate. The pinned engine/wrapper/base files remain
unchanged at `4256cd6201b17dcda3f9d078552acf1d3c58b3f0`. No previous staging
scenario or final offline test is rerun. Frappe compatibility and production
cutover approval remain separate.

## One execution command

On ZUHAIR-PC, open **PowerShell as administrator under the same staging user**:

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_phase1_staging_scm.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
if ($LASTEXITCODE -ne 0) { throw 'SCM staging failed. Preserve the new evidence and stop.' }
```

No additional confirmation is required: this is the staging-only setup/session
authorized by the task. Do not invoke the production wrapper's install/update/
debug commands or manually start the old probe as a substitute.

### Retry when the reserved runtime already exists

Read-only administrator inspection on 2026-10-16 found the retained runtime to
contain 818 files, all matching its manifest, with no missing/mismatched/extra
files, restricted ACL principals and no process executing from it. The staging
service was stopped/Manual at its original probe path, with zero exit codes and
a matching original executable hash. This is a complete hash-consistent runtime
copy, not evidence that an SCM acceptance session passed. The line-52 refusal
happens before the runner makes a new attempt or changes the service.

Use the same command with **`-FreshRuntime`** for the reviewed retry:

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_phase1_staging_scm.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019' `
  -FreshRuntime
if ($LASTEXITCODE -ne 0) { throw 'SCM staging failed. Preserve the new evidence and stop.' }
```

This first repeats only the necessary read-only service/runtime safety inspection.
It creates `code\runtime-<new GUID>` and three new, equivalent per-program firewall
blocks for that executable, validated with the unchanged strict rule checker in
both stores. The original runtime, original six firewall rules and all old files
remain untouched. No existing runtime is executed or adopted. The new rules have
their own namespace; collisions or partial rule creation stop the run instead of
overwriting/repairing anything. PASS remains the same 18 SCM checks below. No SCM
session or real firewall modification was performed while preparing this retry;
two focused path/rule tests passed using mocked firewall commands.

## What the command changes

- Validates the stopped Manual `FPFPhase1Staging` registration/account, retained
  probe/TLS provenance, private boundary ACLs and all six existing firewall rules
  in both policy stores. Takes hashes of previous evidence without running tests.
- Creates one protected `scm-<random>` directory with read-only code and service-
  writable data. Copies the pinned sources, synthetic adapter and public certificate;
  the service never receives the original TLS private key or real configuration.
- Copies the installed Python stdlib and required pywin32 files into the previously
  reserved `service-boundary\code\runtime\python.exe` location. No downloads,
  pip, system-wide package changes or registry installation are used. A private
  `_pth` plus `-I -S -B` excludes user packages, PYTHONPATH and sitecustomize.
  The runtime and dependencies are hashed; an existing runtime directory is refused.
- Starts a foreground localhost HTTPS mock with no outbound sockets/DNS. Creates
  a new explicitly provisioned synthetic ledger/marker. Only new files get ACL/
  ownership adjustments; original directories/permissions remain unchanged.
- Temporarily changes **only the existing staging service ImagePath**, retaining
  `NT SERVICE\FPFPhase1Staging` and Manual startup. Neither the production service
  nor its registration/configuration is queried or changed.
- Executes the three phases below. After a clean stop it restores the exact old
  staging ImagePath and leaves both service and mock stopped. The original probe
  executable is never overwritten, rebuilt or restarted.

## Acceptance session and PASS/FAIL

| Phase | Required new evidence |
| --- | --- |
| Idle stop | Actual service token/SID and SCM process match; empty ledger opens with real storage guards; lifetime mutex is held while idle; stop wakes promptly, completes in under 10 seconds and releases ownership. |
| Inflight stop | Synthetic generator signals after the real INSERT, inside the uncommitted capture, then waits five seconds. SCM must show STOP_PENDING after stop. Capture finishes/commits, no POST is made, one pending punch survives, and the service exits within 30 seconds. |
| Restart recovery | The same ledger/activation is reused. The actual engine delivers the one pending synthetic punch through verified localhost HTTPS, records synced, and again stops within 10 seconds with ownership released. |

**PASS:** command exits 0 and prints `SCM STAGING PASS: 18 checks`; the new
`scm-...\data\result.json` has `status: PASS` and all 18 checks true. Require one
synthetic mock record, clean service exit codes, integrity checks, unchanged old
evidence hashes, stopped mock/service and restored original staging registration.
Readiness/phase files are atomically published; old reports cannot satisfy the run.

**FAIL:** any other result, missing evidence, timeout, mismatched source/SID/path,
failed storage or changed firewall state. Preserve the new directory, private
runtime and logs. Do not rerun, delete artifacts, relax permissions or force-kill
the service. Failure cleanup requests a graceful stop only for this exact staging
ImagePath/account and restores the old registration only when stopped. If that is
not possible, it reports FAIL and preserves the unresolved staging state. Only its
own foreground helper process may be killed by its retained process handle.

## Scope and implementation

`phase1_staging_scm_host.py` loads the unchanged real wrapper/base and derives a
class with only staging identity metadata and phase setup. Its `main`, `start`,
`stop`, `SvcStop` and `SvcDoRun` remain inherited. The wrapper's integration import
is satisfied by an in-memory synthetic module; the real `erpnext_sync.py`,
`local_config.py` and SDK are never imported. Synthetic tick calls the actual
`durable_sync.run()`, real Windows mutex, private-storage and activation checks.

The reviewed Step 5B audit guard confines service TCP to the exact mock IP/port,
rejects production imports, UDP and arbitrary child/native access, and restricts
file writes to this new data directory. The only child exception is the unchanged
read-only local ACL verifier. The transport requires HTTPS, pinned local trust,
no environment proxies/redirects and the synthetic employee/device pair. Existing
program-scoped firewall rules apply to the **actual private Python executable**.
This is trusted-code application isolation plus the existing firewall boundary,
not a claim of an adversarial Python/native sandbox.

Reports include account SID, PID, stop durations, mutex/integrity observations,
source/bundle/runtime/settings hashes, original registration, previous evidence
hashes and separate helper stdout/stderr. Failures record fixed phase/category
and available native/CIM numeric codes, not exception text or credentials.

The five-second synthetic capture justifies these staging stop limits. It does
not certify the duration of real SDK fetches; production must retain reviewed
bounded device/request settings and allow complete capture/commit time.

New automated tests cover private-runtime native imports without SCM entry,
guarded provisioning/inspection, unchanged inherited handlers, synthetic lifecycle
over real localhost TLS with fake Win32 controls, bad source/arguments refusal,
PowerShell syntax and quoted child paths/native exit capture. They never register
or control a real service. The actual SCM gate remains unverified until the
operator executes this command successfully. No production activation or
historical recovery is enabled.
