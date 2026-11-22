# Phase 1 consolidated remaining checks and activation gates

> Historical procedure: core Phase 1 deployment and integration-account GET checks have completed. Do not rerun the commands below as a current next step. See [current Phase 1 status](phase1-status.md); Auto Attendance remains blocked pending a separately verified safeguard.

The 14 offline checks are now accepted. Do not rerun this procedure; use the
[final readiness handoff](phase1-final-readiness.md) for the two remaining gates
and cutover decision.

Candidate: `feature/phase1-service-hardening`, engine commit
`4256cd6201b17dcda3f9d078552acf1d3c58b3f0`.
No deployment, production service operation or historical recovery is authorized.

## Evidence already accepted: reuse, do not rerun

Steps 1–4 established the isolated development environment and actual staging
service-token filesystem/localhost TLS checks. Step 5A pinned the engine sources;
5B passed all 22 HTTPS/application-isolation checks. Step 5C1 passed 23 process
crash/commit/restart checks. Step 5C2 passed 42 timeout/uncertainty/confirmation
checks. Those reports and hashes stay where they are. The registered staging
service hosts a probe; it does not yet host the hardened Python service wrapper.

## One execution procedure

On ZUHAIR-PC, use ordinary PowerShell as the same Windows user. No administrator
privileges are needed. This runs only 14 remaining offline checks, without a
listener, DNS, socket connection, device SDK access or real SCM API calls.

```powershell
Set-Location 'C:\GitHub\biometric-attendance-sync-tool'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_phase1_remaining.ps1 `
  -StageRoot 'C:\Users\zuhair-fpf\AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
if ($LASTEXITCODE -ne 0) { throw 'Remaining checks failed. Preserve the evidence and stop.' }
```

**PASS:** `REMAINING OFFLINE CHECKS PASS: 14 tests`, exit 0, and a new private
`final-remaining-<random>\result.json` with `status: PASS`. All 14 tests must run;
skips/failures are not accepted. Keep the new report, stdout/stderr and SHA-256
manifest. Existing evidence and ACLs are untouched; only the new report directory
receives permissions for this user, SYSTEM and Administrators. Temporary synthetic
test files are cleaned up normally; source and backup evidence from earlier steps
is never opened for writing. No branch, service or firewall change occurs.

**FAIL:** any other result, timeout or missing report. Stop and preserve the new
directory. Do not erase/reprovision a ledger, relax permissions or repeat earlier
steps to mask the failure. A PASS is an offline result: `activation_ready` remains
false until the two external gates below are closed.

## What these 14 tests prove

| Remaining requirement | Evidence from this run | Limit |
| --- | --- | --- |
| Storage full and integrity | Real SQLite `SQLITE_FULL` via a per-connection page limit rolls back the entire capture, leaves failed-fetch status/watermark blocked, and reopens with integrity OK. Existing integration tests verify critical sanitized logging and zero POSTs on storage failure. | Does not fill the PC's disk or simulate NTFS/controller failure. Critical logging on an unwritable log disk still needs external monitoring. |
| Corruption/startup refusal | Corrupt files and altered schema/indexes are refused intact; missing/replaced DB/marker and changed destination fail closed. Nonempty ledgers cannot be adopted silently. | Never replace a failed ledger with an empty one. |
| Backup/restore | Actual SQLite CLI backup, preserved activation marker, restore to a new file, destination/cookie validation, integrity check and delivery of a restored pending record through the real delivery component with an in-memory response. Source and backup remain unchanged. | Matching cookie does not prove freshness. The test deliberately captures another record after backup and demonstrates a one-record restore gap requiring interval accounting. |
| Cutover | Historical payload/evidence remains retained as `cutover_hold`, never marked delivered; exact-boundary records are allowed. Delivery also enforces the boundary on already-pending/imported records. | Held history can cap Shift Type watermarks indefinitely. Its operational impact must be accepted; no mass release/requeue is supplied. |
| Windows wrapper | Actual `SMWinservice`/`erpnext_sync_win` handlers with synthetic Win32 adapters: stop sets STOP_PENDING/event and callback, lets inflight work finish, skips another idle wait, releases the worker lock; a fresh instance starts and stops from idle. Existing tests preserve pending capture on stop and stop further claims during pacing. | No real SCM registration, service-token durable-engine execution or real SCM stop latency is certified. |

The engine, wrapper and base service hashes must match the pinned candidate.
The procedure intentionally does not rerun the earlier staging harnesses or the
complete test suite. Tests use synthetic fixtures and in-memory transport only.

## Gate 1: one outstanding real Windows service acceptance

Before activation, obtain separately authorized staging execution of the **actual
hardened Python wrapper and durable engine** under the dedicated staging service
identity, retaining the established localhost-only restrictions. Do not repurpose
or restart the old probe and call that durable-service proof. No new service host
is installed or configured by the procedure above, consistent with no deployment.

One acceptance session must show:

- Actual service-token access to the candidate ledger/marker and worker mutex;
  the foreground user results and the old probe are supporting evidence only.
- Idle stop wakes promptly; stop during bounded synthetic fetch/HTTPS work reports
  STOP_PENDING, completes/preserves current work, makes no further claims, exits
  within the approved bound and releases ownership.
- Starting that same staging service resumes its existing pending queue safely.

Record source/interpreter/identity hashes, stop duration, before/after ledger
counts and service exit status. Stop latency is bounded by inflight work: the
current capture does not poll the stop callback inside an SDK attendance fetch.
Verify deployed SDK/request timeouts and allow for full capture/commit time;
do not assume the 15-second idle wait is the worst-case stop duration.
This remains **unverified**, not a request to operate the production service.

## Gate 2: minimum read-only Frappe evidence before activation

Request one administrator-approved bundle from the actual running site, plus a
small permission check using the intended integration account. A large export,
live insertion test or another infrastructure setup is not required.

1. **Effective deployed code and customizations:** confirm current app revisions
   against the reviewed pinned source; identify the effective insertion method
   and Employee Checkin controller, method/controller overrides, wildcard and
   Employee Checkin document hooks, enabled applicable Server Scripts, relevant
   defaults/custom validations and database constraints. Include effective hook
   registrations and relevant source hashes, not merely upstream `hooks.py`.
   If this cannot be inspected, keep it unverified and activation blocked.
2. **Identity and normalization:** confirm the effective employee lookup is exact
   `attendance_device_id`, how inactive/duplicate mappings behave, the outcome of
   explicit JSON null log type, second-truncation/precision, site timezone/local
   timestamp assumptions and the actual duplicate key. Explain SQL NULL versus
   empty-string behavior. Review biometric User ID aliases across devices that
   could map to one employee. Do not infer these from one connector-visible record.
3. **Read-back authority:** using GETs only, show the integration account can read
   the exact Employee and Checkin fields/filters used by `FrappeClient.confirm`,
   across all devices, including blank device IDs. An administrator must compare
   a known, bounded identity result with an unfiltered authoritative count/result
   to rule out permission filtering or hidden ambiguity. Redact employee details
   from the evidence shared here. Empty/denied/NULL/ambiguous results stay held.
4. **Refusals and attendance effects:** from deployed authentication/rate-limit
   middleware/source, confirm 401/403/429 occur before insertion (the client may
   retry those). Record relevant Shift Type log-type determination, current remote
   watermarks and expected effect of blank log types/held history. No insertion,
   Shift Type update or configuration change is permitted for this review.

Keep confirmation settings false/empty until that review is genuinely complete;
the local mock's synthetic verification flag is not production evidence. Do not
certify write permission or successful inserts using read-only GETs.

## Activation decision

Offline PASS plus accepted Steps 1–5C2 is substantial code/staging evidence, but
activation stays blocked on the actual-service acceptance and effective Frappe
evidence above. Before a separately approved cutover, also record the unchanged
service identity/configuration and clocks, a future local boundary, the maintenance
gap accounting, approved handling of held-history watermark impact, private NTFS
storage, current DB+marker backup, free-space/critical-log monitoring and rollback
ownership. Preserve the latest queue; an older backup cannot silently replace it.

Do not use the older cutover runbook's obsolete commit/activation commands. Follow
[the hardened cutover requirements](phase1-service-hardening.md) when a deployment
is separately approved. No production activation or historical recovery is enabled
by this document or the test procedure.
