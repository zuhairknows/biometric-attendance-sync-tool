# Stage 3: operator handoff integration (production execution disabled)

> Archived development evidence through `29a440c`. The later
> [historical recovery development report](historical-recovery-development.md)
> and [release procedure](historical-recovery-deployment.md) supersede the
> synthetic-only service gate and transport limitations described below.
> Production deployment and activation still require independent approval.

Use the existing manager's normal/demo mode. `--monitor-only` and
`--investigation-only` continue to reject all recovery actions on the server,
including direct URLs. No new application, sender, database or service is added.

## Operator workflow

1. Open **Investigation → Historical Recovery Preview**, choose the period/devices,
   acknowledge the protected snapshot and run the local preview.
2. Select at most ten displayed candidates/held records. Enter the independent
   investigation review reference and choose **Verify selected evidence & prepare
   request**. The startup profile must pin a separate, completed, fully verified
   Phase 2A report for those exact source records. Preview, offline and production
   compatibility-only results cannot authorize a request. No API call occurs here.
3. Review the request. Check each approved record explicitly. For every selected
   held record also check its individual hold-exception box and supply a separate
   authorization reference. **Approve selected records** publishes only approval.
4. Review the separate dispatch confirmation and enter its own reference.
   **Dispatch approved request** publishes to the private service inbox; it never
   sends attendance or starts a worker in the manager.
5. Use **Refresh status (read-only)**, or **Recovery → Refresh service requests**,
   to review durable service outcomes. A queued request is not a recovered record.
   Only the service's independent destination verification can report Recovered or
   Already Exists. Cancellation requests stop remaining work, not a remote commit
   already in flight. Needs Review never authorizes a blind retry.

Duplicate form submissions reuse the draft/approval/dispatch; an execution deadline
is never extended by repeating a request. After a diagnostic or connection failure,
refresh the request before repeating an action: publication may already have occurred.
Manager restart/closure does not stop the service or discard durable requests.
Status refresh is manual; no new background polling engine is present.

The application retains the older file-only simulation/batch interface. The new
service-request workflow does not invoke that simulation interface or any sending
transport. Production approval/dispatch controls are unavailable; direct action
requests also reject production destinations. No verification claim is filled in
automatically, except explicitly synthetic fixture claims in `--demo`.

## Private profile and demonstration

Existing `backup`, approved `backup_hash`, `contract`, approved `contract_hash`,
`output` and `device_config` remain unchanged. Normal-mode handoff additionally uses:

| Setting | Purpose |
| --- | --- |
| `recovery_ledger` | Approved canonical service ledger, opened read-only by the manager |
| `recovery_inbox` | Existing private local handoff directory, separate from all inputs |
| `recovery_summary` | Independently reviewed, fully verified Phase 2A summary JSON |
| `recovery_summary_hash` | Independently approved SHA-256 of that summary |
| `recovery_service_sid` | Approved service account SID when required by existing private-storage policy |
| `diagnostics_directory` | Existing private diagnostic location; defaults to the private profile directory |

Keep profiles, reports and input files private. Paths/SIDs cannot be supplied by a
browser form. The manager never receives new recovery API credentials and never
opens the service ledger writable. Windows ACLs authenticate the handoff using the
existing Stage 2 policy; no permissions are changed by the application. Diagnostic
JSONL contains only reference, stage, allowlisted code and event. The UI hides
employee identifiers, private paths, raw errors, review text and transport bodies.

For an isolated synthetic walkthrough only:

```powershell
& '.\Start Biometric Manager.bat' --demo
```

Demo inputs include a separate disposable canonical service-ledger fixture and
verified synthetic evidence. Preparing, approving and dispatching remains file-only.
No service emulator is started by the manager; processing/restart validation is in
the automated synthetic tests. The actual service hook's production transport gate
remains closed. Do not deploy or change production configuration for this stage.

## Deadline finding and production gates

The [final readiness review](phase2-final-readiness.md) records the current
NO-GO checklist, confirmed state-race correction and precise read-only deployment
evidence steps. It does not authorize production operations.

**The current transport cannot guarantee a hard 120-second execution bound.**
Local source inspection used Requests **2.34.2** (`adapters.py`, `sessions.py`) and
urllib3 **2.8.0** (`util/timeout.py`). Numeric Requests timeouts become connect/read
timeouts, and the default synchronous call consumes the response before returning.
Read and even urllib3 total timeouts do not impose an absolute wall-clock body/DNS
deadline. These are development-environment versions, not deployment evidence.

`test_recovery_deadline` uses one GET to a literal-loopback receiver which sends
bytes inside the socket timeout but beyond a shortened overall deadline. The
existing writer call returns late; its budget rejects the response and prevents the
next call. It proves fail-closed outcomes, **not** hard execution-time cancellation.
The receiver thread is only a test server; no additional sending worker is introduced.

The persisted 128-request cap, ten-item limit, three-second connect and ten-second
read-inactivity timeout caps (both limited further by remaining budget),
120-second dispatch deadline, source/destination intent fencing and cancellation
remain enforced. They cannot interrupt a blocking in-flight Requests call at an
absolute deadline. Abandoning that call in a sender thread/process would risk a
late remote commit and loss of ownership; killing/restarting the service is not a
solution. A hard bound requires a separately reviewed service-owned, cancellable
I/O transport covering DNS, connection, TLS, headers and body, while retaining one
writer and reconciliation of uncertain commits. That can be designed within the
existing service without another sender, but is outside this Phase 1-preserving
change and has not been implemented or certified.

For controlled testing the chosen policy is finite requests with these strict
timeouts and fail-closed late/uncertain outcomes, not a hard execution deadline.
It introduces no automatic transport retries or extra sending workers. Explicit
operational acceptance of potential delay to normal synchronization is required.

Production remains **NO-GO**. In addition to that policy acceptance, independently verify
the effective Frappe GET/POST handlers/hooks, full visibility, employee database
equality, duplicate uniqueness, timezone/precision and historical payload policy;
actual Windows service/operator token and ACL access; service ownership/restart
behavior; scoped destination quiescence and each held-record exception. Synthetic
tests exercise kernel mutex exclusion and persisted restart/crash behavior, not
actual SCM service deployment or production account access. A reviewed production
gate change and separate operational approval are still required.

Stage 3 changes no Phase 1 synchronization module, global cutover policy or deployed
Windows service. See [Stage 2 backend safeguards](phase2-stage2-service-recovery.md).

## Validation results

The sequential relevant regression run passed **542 tests in 156.208 seconds**.
After adding the final canonical-source status check, the focused run passed
**20 tests in 12.255 seconds**. Together they cover 543 distinct cases; no live
production acceptance is implied.

```powershell
$env:PYTHONPATH='tests'
.\.venv\Scripts\python.exe -B -m unittest test_manager_service_recovery test_recovery_deadline -q
```

New coverage includes the full operator/service path, immutable repeated handoffs,
separate dispatch, multiple selected holds and an untouched unselected hold,
read-only manager access, restart/closure independence, mutex contention, normal
delivery racing dispatch, crash/timeout reconciliation, rejected/changed evidence,
production and startup-mode gates, CSRF/Host/path rejection, safe diagnostic output,
post-publication logging failure and replaced-source status protection. The existing
Stage 2 tests retain intent-before-POST and crash-before/after-commit verification.
The initial timeout-status test required its fake clock to include preflight pacing
as well as jittered cooldown; production cooldown behavior was not changed.
