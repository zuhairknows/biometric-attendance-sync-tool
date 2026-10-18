# Phase 2C Flask conversion validation

## Monitoring refresh correction

Base: `69a43e567b45dfadf35d511344064498183e0492`. `/refresh` was already on the
monitoring allowlist. The confirmed source defect was `Referrer-Policy: no-referrer`:
native HTML form POSTs use `Origin: null` under that policy, so the strict Origin
guard rejected even valid CSRF. A synthetic reproduction returned 403 and started
no job. This follows the [Fetch Origin-header algorithm](https://fetch.spec.whatwg.org/#append-a-request-origin-header).

The correction sets `Referrer-Policy: same-origin`; the CSRF/Origin/Host checks and
loopback binding are unchanged. Null and foreign origins remain rejected. Cross-origin
referrers remain suppressed. `/favicon.ico` now has a read-only empty 204 response;
no investigation/recovery/export endpoint was added to the monitoring allowlist.

Three focused tests cover actual rendered refresh forms from Dashboard and Devices
in normal/monitoring modes with browser-style navigation headers, invalid CSRF and
null/foreign origins, and GET/HEAD-only favicon behavior. Existing blocked-route tests
also send explicit valid Origin/CSRF while asserting no business operations/file changes.
The 35 targeted Flask/gate tests passed in 5.456 seconds; the expanded Phase 1–2C
suite passed **422 tests in 68.145 seconds** under Python 3.11. Diff checks passed.
Tests simulate browser headers using Flask; they do not claim browser/live production verification on IT-SUPPORT.
No deployment or production access was performed.

## Final monitoring-only gate

Gate base: `12a32666f9be9c7f0faa08ba71eaea31e8a087eb`. The additional startup
flag `--monitor-only` is enforced by a default-deny endpoint allowlist before
form parsing or business handlers. Only Dashboard, Devices, diagnostics refresh,
job display/status and static assets are available. Disabled sections are hidden
and **Monitoring only** is visible. Normal/demo defaults and existing protection
rules are unchanged; no Phase 1, service or business-rule modules changed.

Ten new synthetic tests enumerate prohibited routes for GET/HEAD/valid-CSRF POST,
assert business/export/job operations are never invoked, and compare every fixture
file's bytes before/after blocked requests. They also cover URL variants, request
flag bypass attempts, default denial of future endpoints, working diagnostics/assets/
job status, navigation, Host/CSRF protections, missing/relative/corrupt ledger paths,
missing device metadata, CLI forwarding/default behavior for profile/demo modes,
and monitoring-demo startup without investigation/recovery/export preparation.
The 57 focused manager/gate tests passed in 8.305 seconds. The full expanded
suite passed **419 tests in 66.909 seconds** under Python 3.11, adding
`test_monitor_only` to the original expanded command below. Launcher `--help` and
diff checks passed. No production connection, deployment, service action or new
listening server was used for gate validation.

```powershell
$env:PYTHONPATH = 'tests'
.\.venv\Scripts\python.exe -B -m unittest test_monitor_only test_manager test_manager_web -q
```

See [monitoring-only deployment instructions](phase2c-manager.md#enforced-monitoring-only-startup).
Operational deployment still requires approval, a private minimal profile and an
isolated checkout/environment. Existing read-only ledger/SCM permissions must be
verified without changing the running service. This gate does not authorize
investigation or recovery and does not make localhost user authentication.

## Original conversion validation

Branch: `feature/phase2-historical-recovery`.
Base: `ac8cd7248a7159449d3b48a8c7d055d875f47d52` (Tkinter version retained in history).
Final revision is reported with delivery. No push, merge or deployment is included.

## Implementation boundary

- Removed the Phase 2C Tkinter view, moved its reusable adapters/demo to `manager_app`,
  and added Flask routes, server-rendered templates, responsive CSS and vanilla JS.
- Dashboard extends existing read-only ledger diagnostics with full state counts and
  per-device quarantined counts. Configured device IDs/IPs/ports are AST-extracted
  from one approved literal source file; configuration code/passwords are not used.
- Existing Phase 2A CLI still handles the verified contract, GET-only comparison,
  protected source/export, 32-request budget, 120-second worker deadline and 10-second
  HTTP timeout. Production compatibility remains unverified and non-recoverable.
- Existing Phase 2B audit, pinned approval and simulation functions remain authoritative.
  Web approval adds a synthetic-only gate. No live transport/action was added.
- A private startup profile fixes allowed paths and origin. Browser requests cannot
  browse files, change paths or download arbitrary evidence. Employee IDs are opt-in.
- Waitress binds only `127.0.0.1:8765`; debug/reloader are absent. Exact Host and local
  peer checks reject proxies/unexpected hosts; POSTs require session CSRF and same
  origin. Cookies and responses use HttpOnly/SameSite/no-store/CSP protections.
- One manually selected action runs at a time. Status polling checks only that action.
  Browser closure does not cancel it; server shutdown cancels app work without SCM
  control calls. No scheduler, Windows service or device connection was introduced.
- Phase 1 ledger, normalization, startup, synchronization, delivery and service modules
  are unchanged. Only one legacy test guard was corrected for Python 3.11: `pathlib`
  may import non-network `urllib.parse`; `urllib.request`, socket, HTTP, requests and
  engine imports remain blocked, including a new assertion of that network guard.

## Reproducible tests

Use the isolated Python 3.11 environment from the operator guide. Run suites
sequentially because the inherited concurrency tests use a machine-wide mutex.

```powershell
$env:PYTHONPATH = 'tests'
.\.venv\Scripts\python.exe -B -m unittest test_manager test_manager_web -q
.\.venv\Scripts\python.exe -B -m unittest test_manager test_manager_web test_historical_recovery test_historical_investigation test_phase2a_production_verification test_durable_sync test_erpnext_sync test_service_hardening test_phase1_release test_phase1_remaining test_two_uncertain_confirmation test_attendance_reconciliation test_frappe_attendance_export test_frappe_server_export -q
node --check manager_app/static/manager.js
git diff --check
```

Results: **47 focused tests passed (6.354 seconds); 409 expanded tests passed
(97.119 seconds)**. Runtime: Python 3.11, Flask 3.1.3, Waitress 3.0.2, requests 2.34.2.
No production service, ledger, ERPNext or biometric device was accessed.

Coverage includes startup without I/O; loopback binding; Host/peer/proxy rejection;
CSRF, Origin and cross-session checks; payload limits; security headers; dashboard
and missing/corrupt sources; 12 configured FP1/FP2 devices with unknown observations;
configuration parsing without code execution or password disclosure; date/device
filters; search/pagination; contract/hash/production gates; limits/deadlines inherited
from Phase 2A; source-byte immutability; synthetic selected approval/confirmation;
idempotent simulation, source/contract changes and writer contention; protected
CSV/JSON exports; path injection/traversal; missing download/live-execution routes;
redacted errors; single-action serialization; and browser/server shutdown independence.

## Local runtime inspection

Started the real synthetic Waitress demo and confirmed its listening address was
**127.0.0.1:8765**. Loopback HTTP GET/POST checks completed a manual diagnostics action
with synthetic service status; unexpected Host returned **400**, missing CSRF **403**.
The Windows batch launcher passed its dependency checks and printed `--help` correctly.
The server factory/browser-open/shutdown sequence is also covered by mocked tests.

Browser visual inspection/screenshots were unavailable: the browser-control inventory
contained no available browser, and the native computer-use pipe was unavailable.
Server-rendered HTML routes were checked with Flask and real loopback HTTP; responsive
CSS/media rules and JS syntax were inspected. These checks do not prove browser layout,
mobile rendering or deployed Windows compatibility. Perform that operator acceptance
with `--demo` before approving normal-profile use. The temporary demo listener is
stopped after verification; no manager deployment is included.

## Remaining decision and blockers

**GO for isolated synthetic/local review. NO-GO for production recovery.**
Localhost is not user authentication: other local users/processes can access this
approved workspace. Do not expose or reverse-proxy the listener. Live use still needs
deployed ACL/SCM permissions and device-configuration/ledger-size acceptance. Production
GETs require separate operational approval and reviewed endpoint/effective-hook/
Server Script evidence; passing mocked tests establishes none of those claims.

Production recovery additionally requires a fully verified non-compatibility Phase 2A
investigation, deployed employee equality/visibility, duplicate/timezone/precision/
historical payload verification, source/live-ledger binding, audit ownership, an
authorized concurrency/fencing design and uncertain-delivery policy. Historical holds
remain excluded without a separately designed and approved transition. The UI conversion
does not authorize stopping the service or bypassing its writer mutex.

See the [operator guide](phase2c-manager.md) and
[startup profile example](../examples/manager-profile.example.json).

Server/browser hardening follows the official
[Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/)
and [Windows-compatible Waitress deployment guidance](https://flask.palletsprojects.com/en/stable/deploying/waitress/).
