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

`--demo` uses disposable synthetic punches, 12 synthetic device definitions and a
local synthetic ERPNext fixture. It never contacts ERPNext, SCM or devices. Demo
state is removed on graceful shutdown. Start here on a review computer.

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
