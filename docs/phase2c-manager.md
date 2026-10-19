# Phase 2C local web manager

Flask replaces Tkinter. The desktop implementation remains at commit
`ac8cd7248a7159449d3b48a8c7d055d875f47d52` for rollback/reference. This manager
has no synchronization service, device client or live recovery transport.

## Install and launch on Windows

Use Python 3.11 in an isolated development/review checkout. Do not alter the
deployed service environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-manager.txt
& '.\Start Biometric Manager.bat' --demo
```

The launcher validates Python/dependencies, starts Waitress on **127.0.0.1:8765**
and opens the default browser. It needs no administrator rights, firewall changes
or service installation. Keep its console open. Ctrl+C stops only the manager;
closing the browser leaves initiated manager actions running. Add `--no-browser`
to print the address without opening a browser. A busy port fails before launch.

After an approved manager update, reload Dashboard/Devices before using their forms:
an old document may retain the previous referrer policy or session CSRF token.
No synchronization-service restart is required.

`--demo` uses disposable synthetic punches, 12 synthetic device definitions and a
local synthetic ERPNext fixture. It never contacts ERPNext, SCM or devices. Demo
state is removed on graceful shutdown. Start here on a review computer.

## Stage 1: local Historical Recovery Preview

The later [Stage 3 operator handoff](phase2-stage3-integration.md) connects selected
preview records to separately verified evidence, approval, dispatch and service
status in normal/demo mode. Production execution remains disabled; restricted
startup modes retain their read-only behavior.

In `--investigation-only` mode, open **Investigation → Historical Recovery Preview**.
This reads local delivery evidence only. It makes no ERPNext or device requests and
requires no API contract or credentials. Existing API investigation prerequisites
remain unchanged. Recovery approval, simulation and execution stay blocked.

Use the existing isolated manager environment and protected startup profile. Add
`backup_hash` to the existing `backup`, `output` and `device_config` settings:

```powershell
# Run on the approved offline backup, never the live production SQLite file.
(Get-FileHash -LiteralPath 'C:\BiometricReview\inputs\approved.sqlite3' -Algorithm SHA256).Hash.ToLowerInvariant()
& '.\Start Biometric Manager.bat' --investigation-only --profile 'C:\BiometricReview\private\manager.json'
```

Independently review the snapshot, then place that SHA256 in `backup_hash` and its
absolute path in `backup`. The snapshot must be self-contained rollback-journal
SQLite with no WAL/SHM/journal sidecars, in a private input directory outside the
checkout and production service directories. Both input file and parent must pass
the existing private ACL checks. Keep it unchanged; do not calculate and silently
approve a replacement hash after a failed run. Use an existing private, separate
`output` directory meeting the manager's normal report safeguards. No ACL or service
configuration changes are performed by this workflow.

Choose dates (inclusive, original device wall time) and configured devices, confirm
the snapshot acknowledgement, then select **Preview & export**. The manually
initiated job has a 120-second bound. Completed CSV/JSON files are published directly
to protected output with unique names. **Verify protected CSV / JSON** checks those
files and the approved source again; it does not download employee data.

The six classifications describe **local evidence**: untouched pending candidates,
locally synchronized records, historical holds, attempted/uncertain records,
locally unmatched employees, and invalid/ambiguous evidence. Unknown timestamps
remain visible with `range_status=unknown` even when outside the requested range
cannot be determined. Classification/search filters affect display only; reports
include all selected records and per-device counts. Detailed reports contain private
original source/audit fields and must remain protected.

**No record is confirmed missing in ERPNext.** Candidate mapping, timezone,
destination duplicate identity and effective cutover policy require later verified
checks. Synced/duplicate states do not prove current ERPNext existence. Every row
has `eligible_for_recovery=false`; the separate `local-recovery-preview-v1` summary
is not accepted as a verified Phase 2A recovery input. Holds and all ledger states
remain unchanged. Monitoring-only mode denies this workflow, including direct URLs.

## Enforced monitoring-only startup (Dashboard and Devices)

Use `--monitor-only` to allow only Dashboard, Devices, static assets, diagnostics
refresh and its read-only job pages/status. Investigation, recovery, approval,
simulation and exports return **403**, including direct GET/POST URLs. Their
navigation is hidden; every page displays **Monitoring only**. The restriction is
startup-only and cannot be disabled by a browser request. Without this flag, normal
and demo workflows retain their existing behavior. For a synthetic preview:

```powershell
& '.\Start Biometric Manager.bat' --demo --monitor-only
```

This preview seeds only synthetic monitoring data, not investigation reports or
recovery batches. Ordinary `--demo` still prepares its full demonstration workflow.

For approved production monitoring, use a separate manager checkout at the reviewed
delivery commit and its own `.venv`; leave the running service checkout/environment
unchanged. In an existing private profile directory meeting the ACL requirements
below, save this minimal UTF-8 JSON **without a BOM**:

```json
{
  "monitor_ledger": "C:/ProgramData/ERPNextBiometricPushService/sync.sqlite3",
  "device_config": "C:/GitHub/biometric-attendance-sync-tool/local_config.py"
}
```

Confirm `device_config` is the actual approved active device configuration. Do not
add credentials, investigation inputs or recovery workspace paths. Launch from the
isolated manager checkout under an account with existing approved ledger-read and
SCM-query permissions:

```powershell
& '.\Start Biometric Manager.bat' --monitor-only --profile 'C:\Private\BiometricReview\monitor.json'
```

Check `http://127.0.0.1:8765`: Monitoring only is visible, only Dashboard/Devices
navigation remains, and one manual refresh reports real diagnostics. Direct
`/investigation` and `/recovery` must return 403. Missing/invalid ledger access stays
Unavailable without creation/repair; missing device metadata stays unknown. Stop if
permissions or profile validation fail rather than changing service/ledger ACLs.
Verify the service remains running with the same PID/configuration. Ctrl+C rolls
back by stopping only the manager; leave its isolated files inactive. No service
restart or ledger restore is part of deployment/rollback. Separate deployment
approval is still required; localhost is not user authentication.

## Investigation-only mode: one controlled read-only production run

`--investigation-only` allows Dashboard, Devices and Phase 2A investigation/protected
CSV/JSON export. All recovery pages/actions, approvals, simulations and recovery
exports are denied server-side, even with valid CSRF and a populated audit workspace.
It is mutually exclusive with `--monitor-only`. Preview without live connections:

```powershell
& '.\Start Biometric Manager.bat' --demo --investigation-only
```

After separate deployment/execution approval, reuse
`C:\GitHub\biometric-sync-manager-phase2c` and its existing private profile. Update
only this isolated manager to the reviewed delivery commit, never the active
service checkout; keep `ERPNextBiometricPushService` running. Extend the profile
with these existing fields (example paths must point to already approved storage):

```json
{
  "backup": "C:/Private/BiometricReview/inputs/ledger-copy.sqlite3",
  "contract": "C:/Private/BiometricReview/inputs/production-review.json",
  "contract_hash": "REPLACE_WITH_INDEPENDENTLY_APPROVED_SHA256",
  "output": "C:/Private/BiometricReview/reports/run-01",
  "site": "https://erp.myfpf.com"
}
```

Keep the existing `monitor_ledger` and `device_config`. No credentials, recovery
workspace, new database or new ACL grants are needed. `start`/`end` defaults are
optional; select the actual range in the browser. Preserve private UTF-8 JSON
without a BOM and existing input/output separation/ACL checks.

1. Approve a five-minute interval with 1–20 available backup punches for
   `FP1_DEVICE_01`. Only local ledger history is investigated; no device is fetched.
   Use the existing consistent offline backup, not a file copy of the writable
   production ledger. Compare its SHA256 with independently recorded evidence:
   `Get-FileHash -LiteralPath 'C:\Private\BiometricReview\inputs\ledger-copy.sqlite3' -Algorithm SHA256`.
2. Review the existing [production contract template](phase2a-production-contract.template.json)
   and [verification requirements](phase2a-production-read-only-verification.md#contract-record-facts-rather-than-inventing-evidence).
   Apply those evidence requirements to this manager run in the isolated production
   manager installation specified above. Set `read_only_api_verified` true only after independent
   review of effective list/count GET handlers, overrides, middleware and relevant
   Server Scripts. **STOP before requests if GET safety cannot be established.**
   Leave every unknown comparison claim false, especially destination stability;
   never invent quiescence while the service runs. Independently approve/hash the
   contract, then place that exact hash in the profile. Use an existing approved token.
3. Use an existing empty protected report folder outside inputs, checkout and service
   directories. Stop if privacy checks fail; do not relax production ACLs to proceed.
   Record the service's running state/PID before and after using
   `Get-CimInstance Win32_Service -Filter "Name='ERPNextBiometricPushService'" | Select-Object State,ProcessId`.
4. Stop only the existing manager console with Ctrl+C, then launch with the **same
   private profile path** already used for monitoring:

   ```powershell
   Set-Location 'C:\GitHub\biometric-sync-manager-phase2c'
   $Profile = Read-Host 'Existing private manager profile absolute path'
   & '.\Start Biometric Manager.bat' --investigation-only --profile $Profile
   ```

5. Reload `http://127.0.0.1:8765`. Confirm **Investigation only**, no Recovery link,
   and direct `/recovery` returns 403. Enter the approved range/device and masked
   credentials; confirm the offline backup and explicitly acknowledge production.
   Initiate one investigation. Limits remain **32 GETs / 120-second worker deadline /
   10-second HTTP timeout**, without automatic retries or budget increases.
6. Review protected CSV/JSON, per-device counts, blockers and unverified claims.
   Recheck backup/contract hashes and service PID/state. **STOP** on source/hash drift,
   permission/API/pagination errors, exceeded limits or uncertain results. Do not
   rerun automatically. Ctrl+C stops only the manager; restart with `--monitor-only`
   and the same profile to return to monitoring. No service restart or ledger restore.

**Production remains compatibility-only in the unchanged Phase 2A CLI**, even if
all comparison claims are true. `reads_complete` only describes consistent GETs;
the report remains **Unverified**, with no confirmed missing/recoverable punches.
Fully verified comparison requires independent employee equality/visibility,
duplicate, timezone/precision, historical settings, effective-hook and stability
evidence and a mode permitted by Phase 2A. This change does not enable such a full
production comparison. Recovery eligibility/authorization remain false everywhere.

Live recovery is still blocked by the absence of a production transport/operational
approval and verified non-compatibility investigation, the service's global writer
mutex, unverified cross-host fencing/source-to-live-ledger/audit ownership, and
uncertain-delivery reconciliation requirements. Held/rejected records remain excluded;
any hold transition needs a separate design and authorization. No such change is made.

## Approved startup profile

Normal launch reads the existing private file
`%LOCALAPPDATA%\MYFPF\BiometricSyncManager\web-profile.json`, or an explicit file:

```powershell
& '.\Start Biometric Manager.bat' --profile 'C:\Private\BiometricReview\web-profile.json'
```

Copy the [example profile](../examples/manager-profile.example.json) to protected
local storage and replace its paths, range and reviewed contract hash. This example
is not deployment evidence. Keep the profile directory private, with protected
Windows ACL inheritance and no broad group grants. Startup checks permissions
without changing them. Never put credentials in the profile; unknown fields are
rejected. Browser forms cannot change paths or the destination origin.

- `monitor_ledger`: absolute local SQLite path for manual read-only diagnostics.
  Prefer a protected backup; an authorized local ledger can also be read.
- `backup`, `contract`, `contract_hash`: protected offline snapshot and independently
  reviewed Phase 2A contract/hash. Never investigate the live writable ledger.
- `output`: existing dedicated protected local report directory, separate from all
  inputs, checkout, audit workspace and service directories. Existing ACL, free-space,
  source-separation and non-overwrite checks apply. Reports contain employee data.
- `workspace`: existing private Phase 2B audit directory. Browsing does not initialize
  a store. Preparation remains in the [Phase 2B CLI](phase2b-controlled-recovery.md).
- `device_config`: approved configuration source containing a literal `devices` list.
  AST parsing extracts only ID, IP and port. The file is never imported/executed;
  passwords are discarded. Dynamic configuration fails closed.
- `site`, `start`, `end`: HTTPS origin and default inclusive site-local range.
  Credentials in origins are rejected. Unknown verification claims remain unknown.
- `simulation`, `normal_lock`: optional paths for a separately prepared synthetic
  fixture and synthetic lock target. Omit when simulation is not configured.

## Dashboard and devices

**Refresh diagnostics** queries local SCM status and opens SQLite with `mode=ro`.
Counters reflect stored ledger states: synced, pending, retry, rejected, unmatched
and historical holds. Delivery/refresh timestamps are UTC. Unavailable service,
corrupt/missing ledger and missing observations stay unavailable/unknown. Device
rows do not imply connectivity. Configuration groups FP1 and FP2; template addresses
are never substituted for missing actual configuration. Refresh opens no device
connection and uses no service-control operation or writer mutex. Integrity reads
can briefly contend with a writer; avoid excessive live-ledger refreshes.

## Investigation and protected exports

For file-only comparison, use [Offline Comparison](offline-comparison.md) under
Historical Investigation. It needs approved exports and independent evidence;
it neither uses nor weakens the production API endpoint-safety contract.

Start with FP1_DEVICE_01 and a five-minute range covered by the backup/contract.
Select floor/devices, enter masked credentials for this action, and acknowledge
the protected offline backup. Explicitly acknowledge production when applicable.
The existing Phase 2A CLI validates contract hashes, endpoint-safety evidence,
source identity, mappings, timestamps and destination comparison prerequisites.

Limits remain **32 ERPNext GET requests total, 120 seconds overall investigation
worker time, and 10-second HTTP connect/read timeouts**, with no automatic retries.
Local preflight/ACL checks are outside that worker deadline. UI completion checks
poll only the selected action's status, never the service, devices or ERPNext.

Production compatibility-only results display **Unverified**, cannot identify confirmed
missing recovery candidates and cannot authorize recovery. Follow the existing
[production read-only procedure](phase2a-production-read-only-verification.md) and
obtain separate approval before production GETs. Mocked tests establish no live claims.

Each completed investigation writes protected CSV and JSON. Search/filter results
with 25/50/100-row pages; identifiers are hidden until explicitly requested.
**Verify protected CSV / JSON** rechecks saved evidence. It does not download
employee data into an unprotected browser Downloads directory. No arbitrary file
browser/download endpoint exists. Interrupted partial files are not accepted evidence.

### Diagnosing a blocked investigation

A job marked **Action blocked** has no accepted result. Its fixed, private-data-free
reason is displayed on the job page, in `/jobs/<id>/status`, and after **Continue to
results**. Before this diagnostic improvement, the CLI/controller/job/JavaScript
discarded or replaced the reason; an old generic job cannot establish its cause.
Dashboard success only verifies `monitor_ledger`, not investigation prerequisites.

Check these gates in order, using local approved inputs only:

- **Profile/prerequisites:** `backup`, `contract`, `contract_hash`, `output`, `site`
  must be populated; start/end, devices, credentials and offline-backup acknowledgement
  are required. A valid monitoring-only profile may lack investigation settings.
- **Contract:** readable JSON object, matching independently approved SHA256. Production
  requires compatibility mode, exact origin acknowledgement and a contract declaring
  `destination_environment: production`. It separately requires
  `verification.read_only_api_verified: true` backed by independent effective-endpoint
  review. If that evidence is unknown, stop; never change it merely to pass the gate.
- **Backup:** approved absolute offline snapshot path must exist and be readable.
  SQLite schema/integrity checks run in the isolated worker; failures there produce
  a worker-failure notice, not a claim that the backup itself was definitely corrupt.
- **Output:** existing absolute local directory, outside checkout/service state and
  input directories/subdirectories; no database or production-log markers. ACLs must
  be protected with no broad group grants, read/write access available, and at least
  256 MiB free. Validation does not change ACLs or create the directory.
- **Execution/completion:** cancellation, deadline, worker failure, CLI failure or a
  missing/inconsistent completion receipt, summary, CSV hash or row count block the
  job. Worker exceptions remain grouped because raw errors may expose private data.

On the original version there is no retained detailed CLI failure log: Flask logging
is disabled and the controller captures/discards CLI stdout. Access logs show only
HTTP outcomes, not which prerequisite failed. A protected CSV/JSON pair accepted by
the manager is required for completed evidence; leftover files alone prove nothing.
API/pagination/contract-comparison uncertainty can instead complete a report marked
**Unverified**; inspect its displayed blockers and protected JSON `blockers` and
`unverified_claims`. This is different from a blocked job with no accepted report.
Do not run the CLI merely to diagnose preflight: passing preflight can issue live
GETs. Any further production attempt still needs separate read-only approval.

## Recovery management

Load an existing batch, select Pending Approval records, enter a non-sensitive review
reference and check explicit confirmation. Approval is restricted to synthetic
batches and updates only the separate audit. Phase 2B still verifies fingerprints
and source/contract/report pins.

**Simulation Only — Live Recovery Disabled** is always displayed. Simulation uses
only the approved `.example.test`, `synthetic_only` local fixture. It respects the
global Phase 1 writer mutex and fails closed if the running service owns it. Leave
the service running; simulate on another review computer. Repeated/uncertain outcomes
retain Phase 2B's durable audit/intent safeguards. Export writes the full protected
CSV/JSON audit; the page shows the latest 100 events. No historical hold release,
Phase 1 state transition, replay or production transport is exposed.

## Local security and remaining acceptance

**Localhost binding is not authentication. Other local users/processes able to access
this listener can use the approved workspace. Run only on a trusted Windows account/
computer; do not expose or proxy the port.** There is no account-management system.
Host/Origin checks, session CSRF, HttpOnly/SameSite cookies, no-store responses and
security headers protect browser interactions; they do not create user authorization.

Credentials stay in action memory/environment and are never persisted or echoed in
HTML/logs. Decline browser password saving. Identifiers are opt-in; keep reports
protected. One action runs at a time. Stop the manager console to cancel app work;
review durable outcomes after interruption. The service remains independent even
after an abrupt manager exit.

Browser visual/DPI acceptance, deployed ACLs/SCM permissions, ledger size, verified
GET endpoints/effective hooks/Server Scripts, employee equality/full API visibility,
duplicate rules, timezone/precision and historical payload settings still require
independent verification. Production recovery additionally needs approved concurrency/
cross-host fencing, source/live-ledger binding and audit ownership, uncertain-delivery
reconciliation and any separately approved hold transition. None is enabled here.
