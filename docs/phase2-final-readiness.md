# Historical recovery: final readiness gate

**NO-GO for production recovery.** This is a development review, not deployment
evidence or permission to send. Keep the existing Windows service running. Do not
change contracts, ACLs, service configuration, queue states or historical holds.

## Development findings

- The 120-second dispatch deadline is **cooperative**, not a hard execution bound.
  `Budget` checks wall and monotonic time, durably counts at most 128 HTTP attempts,
  enforces connect/read inactivity limits of three/ten seconds (each further
  limited by remaining budget), denies redirects and refuses late success. Requests can
  nevertheless block beyond the deadline (DNS or a continuously trickling body).
  Input/ACL validation also takes elapsed time; the budget cannot interrupt it.
  The loopback-only deadline regression demonstrates the transport limitation.
  Cancellation stops subsequent work; it cannot recall an in-flight remote commit.
  Do not detach a sender, terminate the service or retry POST to enforce a deadline.
  A hard bound needs separately reviewed cancellable I/O in the existing service;
  it is not supplied by changing Requests' timeout argument. The selected minimal
  policy is finite requests, not a hard elapsed-time guarantee. Controlled-test
  approval must explicitly accept possible normal-delivery delay during an
  in-flight call. Do not claim a worst-case duration of 128 times ten seconds.
  The budget adds no automatic retries; uncertain POST permission stays consumed.
- Confirmed and corrected: the prior-intent, Already Exists path checked current
  quarantine before remote reads but accepted any later state when committing the
  local confirmation. It now repeats the supported-state check inside BEGIN
  IMMEDIATE. A synthetic crash/remote-commit/quarantine race failed before this
  correction and passes afterward, without another POST or quarantine overwrite.
- Canonical identity, activation cookie, evidence/file hashes and current untouched
  state are checked before a first send. The service's existing global writer
  mutex and atomic unique destination/source intent fence repeated requests and
  normal delivery. Intent and Needs Review audit are committed before POST.
  A crash consumes permission to POST even if the request never reached ERPNext.
  Only independent, complete destination comparison can confirm an outcome; HTTP
  200 and duplicate wording do not suffice. An absent destination after prior
  intent remains Needs Review. No automatic fresh send permission is restored.
- Selected cutover exceptions require an exact held-ID set and separate reference.
  The global boundary and unselected holds are unchanged. Handled uncertain sends
  restore the selected hold. A process crash can leave its authorized lease in
  delivering (later retry on expiry); the durable intent still blocks resubmission.
  Expired/cancelled requests can remain Needs Review without another comparison.
  Preserve these rows/intents and obtain independent destination review; never
  reset them to pending or remove their audit to retry.
- Manager service-request actions only publish private files and read the canonical
  ledger. They have no live sending transport. The older simulation action uses
  only the exact file-backed SimulationTarget. Real service_tick returns before
  schema/storage/transport work without that synthetic target, which the running
  service path never supplies. Neither configuration nor browser input opens this
  production gate. Restricted startup modes still deny recovery actions.
- This correction changes no Phase 1 module. Stage 2 previously added optional
  coordination/intent fencing to durable_sync, durable_delivery and sync_ledger;
  those files are not byte-identical to release/phase1. Their ordinary delivery,
  pacing, retry, cutover and default-disabled coordination regressions are retained.
  The archived Phase 1 release certification does not certify production recovery.

## Essential read-only production evidence (separate authorization required)

These instructions have **not** been executed. Collect evidence privately using
existing authorized access. A missing mechanism or unknown result is a blocker,
not permission to create a diagnostic service, Server Script or new DB account.

### Windows: inspect the running service, not the manager's account

Run these observation commands on the integration PC in an existing authorized
PowerShell session. Do not print local_config.py, API keys, process environments or
raw service logs. Keep account/path/ACL evidence private.

```powershell
$svc = Get-CimInstance Win32_Service -Filter "Name='ERPNextBiometricPushService'"
if (-not $svc -or $svc.State -ne 'Running' -or $svc.ProcessId -eq 0) { throw 'STOP: service not running' }
$originalServicePid = $svc.ProcessId
$svc | Select-Object Name,State,StartName,ProcessId
sc.exe qsidtype ERPNextBiometricPushService
sc.exe qprivs ERPNextBiometricPushService
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$originalServicePid"
Invoke-CimMethod -InputObject $proc -MethodName GetOwnerSid
Get-Acl -LiteralPath 'C:\ProgramData\ERPNextBiometricPushService' | Format-List Owner,AreAccessRulesProtected,Access
Get-Acl -LiteralPath 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3' | Format-List Owner,AreAccessRulesProtected,Access
Get-Acl -LiteralPath 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3.activation.json' | Format-List Owner,AreAccessRulesProtected,Access
```

Administrator: bind the process executable/loaded source to the approved service
deployment using existing deployment records and process inspection. Hash only
non-secret code artifacts; a checkout HEAD does not prove the process loaded it.
Inspect the existing process token (including restricted/service SIDs) and mutex
handle/security in an already approved read-only process inspector. Do not acquire,
create, release or close the mutex. A handle's existence alone does not prove
writer ownership. Do not run the staging-only mutex diagnostic against production.
Current ownership behavior must agree with the reviewed lifetime worker_session;
actual restart/restricted-token behavior needs prior matching deployment evidence
or a separately authorized isolated Windows validation, not a production restart.

For the **actual approved** snapshot, profile, evidence, reports, diagnostics and
proposed handoff paths, run Get-Acl on each file and parent. Confirm fixed local
NTFS storage, no reparse/cloud/network paths, protected DACLs and approved owners/
principals using durable_startup.private_storage policy. Confirm effective access
for both actual tokens: service reads all evidence, reads/writes its existing ledger
and inbox; operator reads the ledger and writes only approved handoff/report paths.
Do not test access by writing production files. An administrator's successful read
is not proof of service-token access; use existing token/ACL effective-access tools.
No such approved tool/evidence means this check remains unresolved. No new recovery
directory or ACL change is authorized here.

Use the existing manager Dashboard's read-only diagnostics for ledger integrity,
counts and continuing delivery. Verify the approved backup's SHA-256 against the
independently approved value with Get-FileHash -Algorithm SHA256; use that separate
snapshot for preview, never raw-copy an open SQLite file. Independently compare its
activation cookie/selected canonical fingerprints with the live ledger using the
existing read-only preparation validation when that workflow is separately approved.
Do not dispatch or create approval files during this check. Do not hash the changing
live ledger as evidence of non-mutation. A new source snapshot/evidence binding is
required if the approved inputs change.

At the end, repeat service State/ProcessId observation and Dashboard refresh:

```powershell
$after = Get-CimInstance Win32_Service -Filter "Name='ERPNextBiometricPushService'"
if ($after.State -ne 'Running' -or $after.ProcessId -ne $originalServicePid) { throw 'STOP: service continuity not established' }
```

This observes continuity; it does not by itself prove unchanged loaded settings.

### ERPNext: administrator source/configuration review before any API probe

Use the existing deployment administrator/provider's read-only artifact and
metadata access. Do not initialize Frappe/Bench console, import apps, install
scripts or call insertion endpoints to collect evidence. The detailed collection
method is in [deployed-source verification](frappe-cloud-checkin-source-verification-plan.md).
Require only this site-bound subset, with deployment/image ID, collection time with
offset, app order/commits, file hashes, local patches and running-worker consistency:

1. Resolve GET resource Employee, GET resource Employee Checkin and GET
   frappe.client.get_count through deployed dispatch/query code. Inspect effective
   override_whitelisted_methods, override/extend_doctype_class for both DocTypes,
   permission_query_conditions, has_permission, request/auth/session hooks and
   relevant enabled Server Scripts/API-name collisions. Follow custom callees and
   wildcard hooks. Inventory completeness must be administrator-attested. Confirm
   no business writes or unsafe side effects in the GET paths; standard upstream
   code or previous successful GETs is insufficient.
2. Trace the service's version-selected add_log_based_on_employee_field POST path
   **from source only**. Inspect controllers, doc_events including '*', Server
   Scripts, defaults, constraints/triggers and helpers. Establish exact employee
   lookup semantics (original attendance_device_id, database equality, active and
   inactive duplicates), employee/time/log_type duplicate predicates (device excluded),
   SQL NULL versus blank, timestamp conversion/precision and historical acceptance.
   Verify whether duplicate exclusion is atomic against other writers. Without
   suitable uniqueness, require independently established absence of other writers
   for the selected historical identities throughout comparison/submission; do not
   stop normal synchronization or assume two repeat scans prove quiescence.
3. Via the existing authorized read-only DB/metadata facility inspect column types,
   precision, defaults/nullability and collation for Employee.attendance_device_id
   and Employee Checkin.employee/time/log_type; relevant indexes/triggers; site and
   DB timezone/SQL mode. Compare these with the exact contract's seconds precision,
   timezone and NULL/blank policy. Unsupported behavior is a STOP, not normalization
   permission. Record metadata privately, not employee rows in logs.
4. Bind the configured integration API user independently to its credentials
   without displaying them. Inspect its effective roles, User Permissions, field
   access and all custom filtering for both resource lists **and get_count**. Use
   independently complete existing control sets/counts across devices, active and
   inactive employees, collation-equivalent IDs and NULL/blank log types. If a
   case has no existing controls, retain that uncertainty; do not create records.
   Any later approved GET probe must match authoritative sets, counts, terminal
   pages and repeat scans using the existing Phase 2A client. The older Phase 1
   helper does not verify get_count or full Phase 2A pagination.

For item 3, the administrator can use these SELECTs in an existing read-only
database facility for the site's selected database (not through a new API):

```sql
SELECT VERSION(), DATABASE(), @@session.time_zone, @@session.sql_mode;
SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,COLLATION_NAME
FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
AND ((TABLE_NAME='tabEmployee' AND COLUMN_NAME='attendance_device_id')
 OR (TABLE_NAME='tabEmployee Checkin' AND COLUMN_NAME IN ('employee','time','log_type')));
SELECT INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,SUB_PART
FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()
AND TABLE_NAME='tabEmployee Checkin' ORDER BY INDEX_NAME,SEQ_IN_INDEX;
SELECT TRIGGER_NAME,EVENT_OBJECT_TABLE,ACTION_TIMING,EVENT_MANIPULATION
FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()
AND EVENT_OBJECT_TABLE IN ('tabEmployee','tabEmployee Checkin');
```

Trace any returned trigger's definition privately, including indirect writes. An
empty inventory is proof only with independently confirmed metadata visibility.
Obtain site timezone and effective scripts/hooks from the administrator's existing
non-mutating inventory/source facility; do not invent a replacement endpoint.

Only after endpoint safety and independent controls are reviewed may an operator
request approval for the existing production compatibility procedure (32 GETs,
120-second cooperative deadline, ten-second socket timeout, production
acknowledgement, protected snapshot/output). See
[existing command and contract requirements](phase2a-production-read-only-verification.md).
Do not execute it as part of this review. reads_complete is not missing-punch or
recovery evidence. Unknown contract claims stay false; no flags are changed here.

## GO / NO-GO checklist

- [ ] Finite-request policy accepted: at most ten records, 128 persisted HTTP
  attempts across restarts, three/ten-second connect/read inactivity limits,
  cooperative 120-second cutoff, no uncertain POST replay. Potential in-flight
  delay to normal synchronization explicitly accepted; no hard deadline claimed.
- [ ] Service API identity independently bound to its actual configuration, with
  effective Employee/Checkin read visibility including the time field's permission
  level, get_count and complete pages checked against independent control sets.
- [ ] Both hostnames bound to the same site/database; effective deployed GET/POST
  rules, employee DB equality, duplicate atomicity/quiescence, timezone/precision
  and historical payload verified. Unknown claims remain false.
- [ ] Actual service/operator tokens, protected inputs/handoff ACLs, canonical
  binding, single-writer ownership and isolated Windows restart proof verified.
- [ ] Complete independent Phase 2A evidence; exact selected records, any separate
  hold exceptions, finite limits and uncertain-outcome review owner approved.
- [ ] A separately reviewed production gate implementation and explicit operational
  approval exist. Current synthetic-only gates must remain closed until then.

Any unchecked item means **NO-GO for recovery**. Read-only evidence collection may
be separately authorized while synchronization continues. No code/configuration
flag in this review enables production sending.

For the first separately approved production test, select **one** verified,
untouched, non-held record from the protected snapshot. Keep synchronization
running; use the existing service writer only. Approval and dispatch stay separate.
Require independent pre-send and post-send destination proof and retained audit.
If the outcome is uncertain, stop further dispatch, preserve the intent and review
the destination independently. Do not replay or reset the source. Starting with a
held record is unnecessary; any later held test needs its own specific authorization.

## Local validation

The focused service/manager/deadline run passed **61 tests in 18.024 seconds**.
After extending the Windows abrupt-exit test, the final sequential relevant
regression run passed **550 tests in 105.022 seconds**, with no skips on win32:
manager service handoff, deadline, service recovery, preview, offline diagnostics/
comparison, restricted modes, manager/web, historical investigation/recovery,
production-verification guards, durable sync, ERPNext sync, service hardening,
Phase 1 release/remaining checks, uncertain confirmation, reconciliation and Frappe
exports. New coverage checks strict phase timeouts, redirect denial, request-cap
exhaustion, no automatic timeout retry, cancellation during HTTP, incomplete
destination counts and actual Windows cross-process global mutex exclusion at
both ledger paths, release and abrupt-owner-exit recovery. Existing tests retain
handoff/approval separation, manager-no-POST, crash/restart reconciliation, holds,
duplicate prevention and independent confirmation. The earlier quarantine-race
regression remains covered. Source-pin regression
and git diff confirm no Phase 1 source changes from the starting commit. Validation
uses synthetic SQLite and mocked/file-backed ERPNext; the deadline test uses only
a loopback receiver. No production access, sending, service operation or deployment
was performed. git diff --check passed.
