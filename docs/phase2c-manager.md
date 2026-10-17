# Phase 2C Windows manager

Python 3.11 with the standard Windows Tcl/Tk component is required. This is a
separate desktop application; do not use the legacy `gui.py` for this workflow.
Run from an isolated development/review checkout, not the service installation.

```powershell
# Use an isolated environment; no changes to the synchronization service's Python.
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-manager.txt
.\.venv\Scripts\python.exe -B scripts\biometric_manager.py --demo
```

`--demo` creates disposable synthetic punches, a verified synthetic investigation
and a recovery batch. It never queries SCM or connects to ERPNext or devices.
Select **Refresh dashboard**, then **List existing batches** on Recovery management.
Select a batch, select individual records, enter a review reference and confirm
approval. **Run simulation only** uses the local synthetic destination fixture.
Nothing executes automatically. Demo data disappears when the window closes.

Launch the normal manager after reviewing these prerequisites:

```powershell
.\.venv\Scripts\python.exe -B scripts\biometric_manager.py
```

## Dashboard

Enter an absolute monitoring-ledger path and select **Refresh dashboard**. Prefer
a protected SQLite backup on the review computer. Monitoring also supports a local
ledger readable by the operator, using SQLite `mode=ro` and a short read transaction.
It acquires no Phase 1 writer mutex. An inaccessible, busy or invalid database is
reported as unavailable, without repair or state changes. Integrity checks can
briefly contend with a SQLite writer; avoid repeated refreshes of a busy ledger.

SCM is queried on this computer for `ERPNextBiometricPushService` with query-status
access only. A review computer without that service shows unavailable; it does not
infer the remote production service's state. Device rows are recorded observations,
not live connectivity. Failed/quarantined includes rejected and held records;
historical holds also have their own count. Delivery times are labelled UTC.

## Historical investigation and protected export

Use an existing protected offline SQLite backup, a small site-local start/end range,
selected device IDs, and the reviewed contract plus its independently approved
SHA256. FP1_DEVICE_01, FP2_DEVICE_08 and FP2_DEVICE_11 are provided; dashboard
observations add other recorded IDs. The end time is inclusive. No device is queried.

Use an existing, dedicated local report directory outside the checkout, service
directories, and all input directories. Existing Phase 2A Windows ACL, source
separation, free-space and non-overwrite protections apply to desktop calls too.
The directory must have protected ACL inheritance with no broad user/group grants;
the application checks but never changes permissions. Reports contain employee
identifiers and must stay in this protected storage, outside Git and shared folders.

Enter API key and secret in the masked fields for each investigation. They are
cleared from the widgets on start, held only in process memory/environment for the
action, and never logged or saved. Existing credential environment values are
restored afterward. Do not use credentials in URLs or review references.

Confirm the protected-backup acknowledgement. For production, explicitly acknowledge
the entered destination using the production checkbox. The unchanged Phase 2A CLI
also verifies the destination environment and reviewed GET-endpoint safety claim.
The application never fills unknown contract claims. Every investigation is bounded
to **32 total GET requests, a 120-second worker deadline, and 10-second HTTP
connect/read timeouts**; there is no automatic retry. Preflight/local ACL inspection
is outside the HTTP worker deadline. Closing cancels the app's investigation worker.

Select **Run investigation & export**. CSV and JSON are exported automatically;
the displayed table is accepted only after a successful supervisor receipt and CSV
hash/record-count validation. Filter by classification or search text. Tables display
at most 1,000 matching rows; protected reports contain all rows. A failure clears
previous investigation results; partial files after interruption are not evidence.

Production compatibility results always display **UNVERIFIED**, remain outside
recovery authorization and never become recovery candidates. Passing mocked tests
does not establish deployment compatibility. Follow the existing
[production read-only procedure](phase2a-production-read-only-verification.md)
and obtain separate approval before making any production GET requests.

## Recovery review and simulation

Choose an existing protected Phase 2B audit workspace and **List existing batches**.
Preparation remains in the existing [Phase 2B CLI](phase2b-controlled-recovery.md); this UI does
not turn arbitrary reports into batches. Listing, record review, audit display and
result export open the audit database read-only and create no writer lock files.

Select a batch to see original source identity, status and audit events. Select only
Pending Approval records, enter an explicit review reference, then confirm
**Approve selected records**. Approval updates only the private recovery audit;
the existing backend rechecks the pinned fingerprint and input hashes. It does not
change source attendance, holds, or Phase 1 ledger states.

Simulation is enabled only for approved/reviewable synthetic batches. Supply a
Phase 2B `synthetic_only` destination fixture with a `.example.test` HTTPS origin,
plus a synthetic normal-worker lock path outside all pinned inputs. The backend
uses only `SimulationTarget`, never a production transport. Simulation may acquire
the existing global Phase 1 writer mutex; if normal synchronization owns it,
simulation fails closed. Leave the service running and use another development
computer for simulations. Do not stop the service to obtain this lock.

**Export batch audit** writes the existing protected CSV/JSON results without
overwriting reports. Recovery statuses and immutable audit events come from Phase 2B.
There is no live execution button, transport selection, auto-approval or replay.

## Application settings and closing

**Remember paths** saves only whitelisted path/range preferences to
`%LOCALAPPDATA%\MYFPF\BiometricSyncManager\settings.json`, outside the production
ledger. Credentials, origin, acknowledgements, approval reference and employee data
are excluded. Startup does no refresh or API work. One selected action runs at a
time; UI completion notifications are not a background monitoring engine.

Closing cancels only app work, clears credential fields and closes the desktop
window. A local approval already committed remains in the audit; an interrupted
simulation must be reviewed using the existing durable Phase 2B statuses. No service
start/stop/configuration action exists. The production Windows service is independent.

## Remaining production recovery requirements

Production recovery is unavailable. Before any future implementation/authorization,
independently verify deployed GET endpoints and effective hooks/Server Scripts,
employee equality and full API visibility, duplicate rules, timezone/precision and
historical payload settings. Production compatibility reads cannot supply a fully
verified recovery investigation. Verify source/contract/report provenance, an approved
concurrency arrangement with normal synchronization, uncertain-delivery reconciliation,
and a separately designed/authorized transition for held historical records. No such
transition is included here. Windows operator acceptance with deployed ACLs, DPI,
service permissions and the real ledger size also remains outstanding.
