# Windows staging — one execution step at a time

> Historical procedure: core Phase 1 deployment and integration-account GET checks have completed. Do not rerun the commands below as a current next step. See [current Phase 1 status](phase1-status.md); Auto Attendance remains blocked pending a separately verified safeguard.

Runtime baseline: `feature/phase1-service-hardening`, commit `4256cd6`.
After the accepted Step 5C2 result, use the
[consolidated final procedure](phase1-final-staging-procedure.md).
Do not rerun Steps 1 through 5C2 or add more intermediate staging rounds.
The separate staging helpers are development fixtures, not production integration changes.
No production service operations, device connections or real Frappe submissions are allowed.
The PC may remain online; isolation applies to the staging process, not the whole host.

## Step 2: private directory, localhost HTTPS and isolation self-test

Administrator privileges: **not required**. On the development PC, from the checkout:

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_phase1_windows_staging.ps1
```

The helper checks the pinned HEAD, creates a new `FPF-Phase1-Staging-<random>` folder
under the current user's local application-data directory, protects its ACL, and copies
only the standalone fixture. It uses existing Git OpenSSL to generate a two-day localhost
certificate and private key inside that folder. No package, root certificate or Windows
service is installed. Internet connectivity and host network/firewall settings are unchanged.

OpenSSL's expected stderr is suppressed only inside its invocation helper for Windows
PowerShell 5.1 compatibility. Every call must return exit code zero. Before the HTTPS
self-test, the helper checks that the certificate and key are nonempty, the certificate
parses and has not expired, the private key passes OpenSSL's consistency check, and their
public keys match. TLS hostname and certificate verification remain required. Any failed
check stops the step; the caller's strict PowerShell error handling remains enabled.
Retrying creates a new private directory and leaves the failed attempt intact.

The copied Python fixture runs with `-I -S -B`. A non-removable audit hook in that process
restricts TCP connections to its exact ephemeral 127.0.0.1 mock port, rejects external DNS,
UDP/device traffic, subprocess/native-library access, production integration/config imports,
and file access outside staging except Python-runtime reads. The HTTP client ignores proxy
environment variables, refuses redirects, and verifies TLS using only its generated local
CA file. Synthetic data uses fixed `STAGE-` identifiers; the mock stores its one Checkin in
memory. Production hostname probes are rejected by the hook before DNS resolution.

PASS requires JSON `status: PASS`, every `checks` value true, `mock_records: 1`, and
`service_operations: false` / `production_access: false`. The mock stops after testing.
Retain `https-self-test.json` and the staging path for the next step. Do not share the PEM
private key. FAIL means stop and review the reported result; no insecure TLS fallback exists.

Five automated development tests cover guard allow/deny rules, the real TLS/audit self-test
with hostile proxy settings, the bootstrap's lack of service/network-setting commands,
and actual Windows PowerShell 5.1 OpenSSL execution. The latter covers expected stderr,
nonzero exit status, launch failure, missing/empty/malformed files, mismatched keys,
and strict caller error handling after success and failure.

This tests only the standalone trusted fixture's process-local isolation. It does not
isolate arbitrary PC programs, certify a future service process, validate device SDKs or
prove deployed Frappe semantics. Future engine/service adapters must enforce isolation
themselves before running. No service has been installed or operated by this step.

## Step 3: prepare the Windows service boundary (administrator required)

Open **Windows PowerShell as administrator using the same Windows user** as Step 2.
This step prepares permissions and firewall policy before a separate staging service
is installed. It does not install, start, stop, query or alter the production service.

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_phase1_staging_boundary.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
```

The helper verifies the pinned checkout, existing 13-check Step 2 report, local NTFS
and absence of reparse paths. It calculates the SID for the future, separate virtual
account `NT SERVICE\FPFPhase1Staging` using read-only `sc.exe showsid`. It creates only
`service-boundary` beneath the supplied stage. Administrators and SYSTEM have full
control; that service SID has read/execute on `code` and modify on `data`. No password
is created or requested. Existing Step 2 files, certificates and report remain unchanged.

Six new Windows Firewall rules are scoped ONLY to these reserved staging paths:

- `service-boundary\code\FPFPhase1Staging.exe`
- `service-boundary\code\runtime\python.exe`

For each executable, inbound and outbound non-loopback IPv4/IPv6 traffic is blocked,
and outbound UDP is blocked to all addresses. Profiles must already be enabled and
permit local rules; the helper refuses rather than changing profile policy. It verifies
rule action, profile, direction, protocol, addresses and executable scope in ActiveStore.
No rule affects the shared Python interpreter or other PC applications. No connectivity
probes or production/device requests occur. This reserves paths only; no executable or
service is installed in this step, and no blanket allow rule is added for localhost.

**PASS for preparation only:** exit code 0 and `STEP 3 PREPARED`, with private
`service-boundary\boundary-preparation.json` recording the service SID, rule names,
paths and hashes. **FAIL:** any exception/nonzero exit; stop and retain partial artifacts.
Do not delete/recreate directories or disable firewall policy. Use only the validated
resume procedure below for partial state; a plain rerun still refuses existing directories.

This does **not** prove effective access or network denial under a service token.
Those remain mandatory before crash/restart tests. The later service must run as the
dedicated virtual account, use only these exact executable paths, install application
guards before any worker/config import, prohibit unprotected child processes and proxy
routing, and verify localhost TLS and denied external/device operations under SCM.
Program firewall rules alone do not control brokered DNS or other processes. The next
gate must also verify directory traversal, allowed data writes and denied code writes
under the actual service token. Do not run the real integration entry point yet.

Development validation: nine staging tests pass, including Windows PowerShell syntax,
real .NET ACL construction/verification with mocked filesystem commands, rejection of
an extra broad ACL grant, address coverage, and the existing HTTPS/isolation tests.
Administrator firewall application and real service-token checks are not yet executed.

### Resume an interrupted Step 3 (same user, administrator required)

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare_phase1_staging_boundary.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019' `
  -Resume
```

Resume recalculates the staging SID and verifies all three existing directory ACLs
without changing them. The reserved code/data directories must still be empty. Only
validated preparation reports are accepted beside them; unexpected files, reparse
points, owners, grants, service identities or report destinations block the operation.
Any existing staging firewall rules must match the intended executable, filters and
block policy in both PersistentStore and ActiveStore before any missing rule is added.
Duplicates, mismatches or unexpected group members are refused, never repaired.

Windows returns the singleton IPv6 range `::-::` as `::`. The validator normalizes
only these equivalent spellings before comparing address lists; changed or missing
coverage still fails. The full validator passed read-only against all six existing
ZUHAIR-PC staging rules in both PersistentStore and ActiveStore after this correction.

Zero existing rules is supported, as is an interruption after some rules were created.
Only rules absent from both stores are created. Existing rules are not edited or
recreated. A failure preserves progress and stops immediately. Success creates a new
`boundary-preparation-<random>.json` with exclusive file creation; earlier evidence is
never overwritten. Existing report source hashes are retained as provenance, not
reinterpreted as runtime certification. No service is installed or started.

Synthetic Windows PowerShell tests exercise the zero-rule partial state, interrupted
creation, repeat runs with no duplicates, unchanged reports and refusal before mutation
for invalid ACLs/SIDs/owners, files, report contents, rule scope and effective policy.
The tests mock filesystem and firewall operations; real administrator execution remains
the operator's next gate. On failure, preserve the exception output for review.

## Step 4: actual staging service token (administrator required)

Use **PowerShell as administrator under the same Windows user**. This installs, starts
and stops **only `FPFPhase1Staging`**, using `NT SERVICE\FPFPhase1Staging`. It does not
query, register or operate the production integration service. No new account password,
Python environment, package, firewall rule or certificate-store entry is created.

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_phase1_staging_service.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
```

The helper validates the existing ACLs, Step 3 evidence and all six firewall rules in
both stores before creating any probe files. The reserved code/data directories must
still be empty. It compiles a small standalone .NET service into the already protected
`code\FPFPhase1Staging.exe` path. It uses the installed .NET Framework compiler, not a
package installation or the integration's Python/service modules.

The operator helper runs a one-exchange localhost HTTPS mock with existing Python
(`-I -S -B`). The mock binds only `127.0.0.1`, denies outbound sockets/DNS/processes,
uses the original Step 2 synthetic certificate/key read-only, and stops after one
synthetic request. The service launches no child and receives only the public
certificate; the private key is not copied or granted to the service. TLS 1.2 is
required, with exact temporary-certificate pinning, validity and hostname checks.
Only that pinned self-signed root is accepted; other TLS failures are rejected.

All permission checks and the HTTPS **client** run under the real SCM service token:

- User SID equals the dedicated staging virtual-account SID; token is not SYSTEM/admin.
- Code can be read, but write, delete, ACL-change and child-file creation are denied.
- Synthetic data can be created, read and written, and opened with modify/delete rights.
- DNS names, external addresses, other loopback ports and UDP are rejected by the
  service's application guard before resolution/socket operations.
- One pinned localhost HTTPS exchange succeeds with fixed synthetic bytes.

**PASS:** exit code 0 and final `STEP 4 PASS`, with all of the following:

- `data\step4-result.json`: `status: PASS`, exactly **14 checks**, all boolean true.
- `data\mock-result.json`: `status: PASS`, `synthetic_exchanges: 1`.
- Service is **Stopped**, startup **Manual**, exit code 0, compiled executable unchanged.
- The existing six staging firewall rules still pass validation in both stores.
- `code\probe-provenance.json` retains source/executable/helper hashes and the SID.

Original Step 2/3 evidence and existing permissions/rules are not overwritten. New
synthetic files and private mock diagnostics are retained. The helper stops its own
staging service/mock on failure where possible. **FAIL:** any exception, missing result,
false check, timeout or nonzero exit. Preserve all artifacts; do not rerun, delete,
change the account, broaden permissions or reinstall automatically.

**Scope:** this verifies the trusted probe's application restrictions under its real
service token, plus effective firewall configuration. It deliberately attempts **no
off-host packets** and therefore does not prove kernel packet drops. It does not run
the durable engine or certify a future service adapter. The reserved Python service
executable is unused; only the .NET probe runs as a service. There is no crash/restart,
device, production Frappe or attendance-submission test in Step 4. Stop after verification.

### Hold after the failed service-creation attempt

Do **not** rerun Step 4 or discard its partial artifacts. Query error 1060 confirms
absence of the service; it is not the error returned by the earlier creation call.
The old helper discarded `Win32_Service.Create.ReturnValue`. WMI provider return
codes are distinct from Win32 `GetLastError` codes and must not be decoded as such.

Source review found `StartPassword=''` for the virtual account. The native Windows
API requires a NULL password for virtual accounts:
[CreateServiceW documentation](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-createservicew).
This is a confirmed parameter-contract defect, not proof of the historical native
error, which was not retained. No staging-specific SCM events were found during review.

The prepared source now uses a narrowly scoped native registration helper in the
existing standalone probe assembly, with a fixed staging name/account, NULL password,
Manual startup and no retry/fallback. It captures `GetLastError` immediately on failure
and reports the native code and Windows message through wrapped .NET exceptions.
Loading the assembly does not run its service entry point. No creation was retried
during development, and no existing artifact was replaced with the changed source.

Minimum read-only diagnostic, in the **original PowerShell window**, without admin:

```powershell
$savedCreation = Get-Variable -Name creation -ValueOnly -ErrorAction SilentlyContinue
if ($null -eq $savedCreation) {
    'Original CIM result was not retained. Do not retry creation.'
} else {
    $savedCreation | Select-Object ReturnValue
}
```

When the helper was run through `powershell.exe -File`, its variable normally existed
only in that now-ended child process. A missing result cannot be reconstructed by
querying the absent service. Preserve the failure output; this diagnostic never
invokes creation or changes accounts, permissions, policy or staging files.

### Approved Step 4 creation resume: frozen artifacts and NULL password

After the operator confirms the three recorded hashes, existing ACLs and absent
staging service, use the dedicated resume mode below. Do not empty directories,
rerun Steps 1–3, replace the original executable, or change accounts/permissions/rules.
Run in **administrator PowerShell as the same Windows user**:

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_phase1_staging_service.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019' `
  -ResumeCreation
```

Before any creation, the helper checks the directory/file permissions, complete
retained inventory, provenance and all three hashes; reviewed probe source; exact
service SID, canary, public certificate and valid retained endpoint; absence of a
prior service result/probe process; a free retained port; and the existing firewall
policy. Any unexpected state blocks the attempt. The original certificate must
remain valid; it is never regenerated automatically.

The original executable, source, mock, endpoint, logs and provenance remain unchanged.
The corrected registration code is compiled to a uniquely named DLL in protected
`code`, loaded only by the operator helper. It passes a literal NULL password to
`CreateServiceW` for the fixed `NT SERVICE\FPFPhase1Staging` identity. The service
continues to use the frozen original executable at the existing firewall-rule path.
There is one creation call, with native error reporting and no fallback or auto-retry.

A new guarded mock rebinds **the original loopback port** without rewriting its old
endpoint file. It must successfully bind and publish a NEW readiness report before
creation is attempted. No other port is selected if the retained port is occupied.
Its logs, result and resume provenance go to a new `data\step4-resume-<random>` folder;
new registration/mock sources have unique names under `code`. They inherit the
existing permissions; no ACL or firewall edits occur. The service's first result is
created at `data\step4-result.json`, which must not already exist.

PASS remains all 14 service-token checks true, one synthetic HTTPS exchange, stopped
Manual staging service, unchanged original file hashes and valid existing firewall
rules. The helper performs only this Step 4 verification, then stops. On any failure,
preserve both old and new evidence and review the native error; do not blindly retry.

Synthetic tests cover accepted frozen legacy source, mocked registration reached only
after validation, bad hashes/SID/canary/ACLs/endpoint, occupied port/process, prior result
refusal, unchanged evidence and a real retained-port localhost TLS exchange. These
tests never create an SCM service or change real accounts, ACLs or firewall rules.

### Read-only finalization after the mock completion false failure

Windows PowerShell 5.1 can return a Process object with an unavailable `ExitCode`
after redirected `Start-Process`, even though `WaitForExit()` returns true. This was
reproduced locally using children with known exit codes 0 and 7. Reading/caching
`.Handle` immediately after launch preserved both codes. The runner now does this,
drains redirected output after a bounded confirmed exit, and distinguishes timeout,
missing exit code and nonzero exit. Missing exit codes are never accepted as zero.
Future successful runs also preserve a new `mock-completion.json`.

Do not repeat the successful service tests. Once the retained service report shows
all 14 checks true, the retained mock report shows one successful exchange, and
stdout/stderr are empty, run this **read-only** command in administrator PowerShell
as the same Windows user:

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\finalize_phase1_staging_service.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
```

The finalizer checks the actual stopped/Manual service identity and path, both zero
service exit codes, absence of active probe/mock processes, exact passing report
checks, mock port/result/logs, original snapshot and artifact hashes, private
permissions and all six existing firewall rules in both stores. It requires exactly
one retained resume attempt; ambiguity or any failed/missing evidence stops it.
It starts/stops no service or mock, compiles nothing and writes no files or settings.

Expected output: `STEP 4 FUNCTIONAL EVIDENCE VERIFIED`. This closes functional
verification from retained evidence while preserving the original harness failure.
The historical mock native exit code was not recorded and is not reconstructed or
fabricated. Kernel packet-drop proof, durable-engine integration and production
readiness remain outside this result. Stop after finalization.

### Step 5B: actual durable engine, isolated localhost HTTPS

After Step 5A passes, run this single command in ordinary PowerShell as the same
Windows user. Administrator privileges are not required. No service is operated.

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
py -3 -I -S -B .\scripts\phase1_staging_engine_https.py --stage-root 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
if ($LASTEXITCODE -ne 0) { throw 'Step 5B failed. Preserve the new attempt; stop here.' }
```

PASS requires exit 0, JSON `status: PASS`, all 22 named checks true and
`mock_records: 1`. The mock must stop before PASS is published. Failure is any
exception, nonzero exit, false check or different count. Preserve the new attempt
and stop; do not rerun earlier steps, regenerate TLS files or relax permissions.

The helper verifies the exact five Step 5A source hashes, copies them to a new
`step5b-<random>\code`, and creates private operator/SYSTEM/Administrators-only
storage in that new attempt. Existing evidence and ACLs are unchanged. Source,
harness and certificate hashes are retained in `data\provenance.json`; the
synthetic ledger, activation marker and aggregate `result.json` remain in `data`.
The original files are never copied wholesale and configuration is never loaded.

The actual pinned `durable_sync.run()` executes one cycle, including its real
storage, activation, destination and global mutex safeguards. Synthetic adapters
replace device input and the HTTP transport only. A future synthetic timestamp
is intentionally used after a newly approved synthetic cutover; it is not real
attendance. One synthetic record must persist and become synced through the real
FrappeClient/delivery path to a fresh literal-127.0.0.1 TLS mock.

A non-removable Python audit hook restricts connections to that mock's exact port,
rejects external DNS/TCP, UDP, other loopback ports, production imports, unrelated
SQLite paths and old-evidence writes. Only the pinned engine's exact read-only
ACL-verifier PowerShell command is allowed as a child. Its local executable search
path is restricted for this process. Native lookup is limited to the mutex API.
The transport rejects HTTP and foreign origins, disables environment proxies and
redirects, and requires the synthetic employee/device pair. TLS verification uses
the retained certificate; tests require rejection of untrusted certificates and
wrong hostnames. No certificate store or firewall settings change.

This is trusted-code application isolation under the foreground operator token,
not an OS sandbox or proof of service-token/kernel firewall enforcement. The
existing service firewall paths are not claimed to cover this Python interpreter.
Crash, timeout, retry, device-failure, disk-failure and backup/restore scenarios
are later steps; do not run them yet.

### Step 5C1: process crash before/after capture commit, then restart

After Step 5B passes, run in ordinary PowerShell as the same Windows user. No
administrator privileges or service operations are needed.

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
py -3 -I -S -B .\scripts\phase1_staging_engine_crash.py --stage-root 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
if ($LASTEXITCODE -ne 0) { throw 'Step 5C1 failed. Preserve the new attempt and stop.' }
```

PASS requires exit 0, JSON `status: PASS`, all **23 checks** true,
`mock_records: 1`, and these exact worker exit codes:
`provision: 0`, `before_commit: 71`, `after_commit: 72`, `restart: 0`.
The two nonzero worker codes are intentional crash evidence; the outer command
must exit zero. Any other result is FAIL. Preserve the new attempt and stop.

This creates a separate `step5c1-<random>` attempt. It pins the unchanged Step 5B
helper by SHA-256 and uses its HTTPS transport, private-storage preparation and
audit policy. The parent guard additionally permits only four fixed commands for
its frozen, isolated Python worker, with exact arguments, environment and working
directory. Each worker reinstalls the original guard before using the actual
engine. No service, ACL or firewall setting from earlier steps is changed.

Four fresh processes provision a synthetic ledger, hard-exit during real
`Ledger.ingest` after an insert but before commit, hard-exit on return from real
`capture` after commit but before submission, then run real `durable_sync.run()`
to drain the committed pending punch. `os._exit` skips normal Python cleanup;
startup guards, SQLite commit code and the Windows mutex are not replaced.
The capture retry interval is 100ms; restart uses one minute to prove delivery
without another device scan. No persisted timers are manually edited.

The parent retains raw crash-time ledger/journal copies before SQLite recovery,
per-phase aggregate observations and separate stdout/stderr, fixed worker exit
codes, source/harness hashes and a final report in the new attempt's `data`.
It verifies integrity, rollback to zero punches with failed-fetch watermark
blocked, survival of one committed pending punch with a bounded watermark, and
exactly one synthetic HTTPS submission on restart. No Shift Type writes occur.
On a worker timeout, only that exact child handle may be terminated; no process
is found or controlled by service/process name.

This tests foreground process termination, not power failure, OS crash, service
restart or service-account execution. Timestamps remain synthetic; the watermark
assertion is a local ledger bound, not a live Shift Type policy certification.
Remote acceptance followed by timeout and uncertain-outcome confirmation are
**Step 5C2**, not covered by this result. Stop after Step 5C1.

### Step 5C2: acceptance followed by timeout and positive confirmation

After Step 5C1 passes, run this single step in ordinary PowerShell as the same
Windows user. No administrator privileges or service operations are required.

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
py -3 -I -S -B .\scripts\phase1_staging_engine_timeout.py --stage-root 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
if ($LASTEXITCODE -ne 0) { throw 'Step 5C2 failed. Preserve the new attempt and stop.' }
```

PASS requires exit 0, JSON `status: PASS`, all **42 checks** true,
`mock_records: 2`, `post_count: 2`, `confirmation_reads: 6`, and a stopped mock.
Any other result is FAIL: preserve the new attempt and stop without changing
earlier evidence or settings.

The helper creates a private `step5c2-<random>` attempt and pins the unchanged
Step 5B/5C1 helpers and five engine modules by SHA-256. It reuses their literal
loopback HTTPS transport, non-removable audit guard and real storage, activation,
mutex and `durable_sync.run()` checks. No existing artifact, ACL, firewall rule
or service is modified. Execution remains under the foreground operator token.

Two synthetic punches for the synthetic employee are ten seconds apart, so they
have unrelated destination identities. The first POST creates a mock Checkin
before the mock withholds its entire response for longer than the client's 0.5s
socket timeout. A real timeout must be observed. The ledger must record `retry`
with `error_category: transport`, one attempt and one uncertain submission; the
second punch must initially remain pending. This engine uses `transport` for a
completed timed-out attempt, not the expired-lease category `uncertain_submission`.

Three automatic cycles must hold the first punch as `confirmation_pending`
without a second POST or any unattested reads, while the unrelated second punch
becomes synced. Real retry deadlines are awaited (synthetic base 0.1s, maximum
0.2s); no ledger timestamps, attempts, states or cooldowns are manually edited.
The initial transport failure briefly applies the engine's real destination
cooldown; the later local confirmation hold must permit unrelated delivery.

Only after this is proved, the in-memory synthetic configuration enables reads
against the reviewed local mock contract. That flag and its explicit SYNTHETIC
reference do not attest production Frappe. An empty Checkin result and a returned
NULL log type must each leave the uncertainty held. An exact employee + second
timestamp + empty-string log-type match must resolve it as `duplicate` with
`destination_confirmed` and the actual synthetic Checkin name. The mock checks
the real client's requested fields, filters and limit; it never deduplicates POSTs,
so an extra write would create another mock document and fail the test. A final
cycle must issue no requests for either completed record.

New `data` reports retain source/helper/harness hashes, per-phase aggregate ledger
observations, an event trail without credentials or URLs, the synthetic ledger
and activation marker, and `result.json`. Every observation checks SQLite integrity,
unchanged source payloads and no Shift Type writes. The six confirmation reads
are Employee + Checkin for each of empty, NULL and positive modes. No device is
polled; a synthetic adapter supplies both punches exactly once.

This proves the pinned engine's conservative behavior under a synthetic mock
contract, not production endpoint/hook verification, complete live exports,
historical recovery authorization, service-token isolation or a power-loss test.
Stop after Step 5C2; failed-device, disk-failure and backup/restore tests remain.
