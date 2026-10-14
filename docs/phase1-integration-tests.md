# Phase 1 integration tests (nonproduction only)

The development PC has no production ZKTeco connection. Live ZKTeco retrieval,
staging Frappe and Windows SCM lifecycle validation are **NOT EXECUTED**. A passing
fake HTTP test does not change those labels or authorize production deployment.

## Repeatable fixture + SQLite + HTTP harness

From the repository, run:

```powershell
py scripts\phase1_http_harness.py
py -m unittest discover -s tests -q
```

The harness binds an ephemeral **127.0.0.1** port, uses disposable temporary SQLite
files and fixed fictitious employee IDs/tokens. It imports no real `local_config`.
There is no device connection, configurable destination URL, production credential
input, proxy routing or automatic HTTP redirect. Its checks remain enabled under
Python `-O`. The fake server is a test fixture, not a new application service.

Ten executed scenarios cover capture/create/repeated ingestion, HTTP 503 recovery,
401 cooldown, 403 cooldown, 429 Retry-After, real socket timeout, missing/inactive
employee quarantine, server creation followed by dropped response, server creation
followed by client interruption before local confirmation, and pending restart.
Nine fake Checkins are created. Crashed `delivering` recovers after lease expiry;
retries receive the modeled 417 duplicate and never create a second fake Checkin.
Ordinary repeated ingestion produces zero additional HTTP calls. These are real
loopback HTTP exchanges with modeled Frappe responses, not a real Frappe instance.

## Hardware and real staging Frappe procedure — NOT EXECUTED

Use a separate Windows staging host or VM with an isolated device LAN and a
nonproduction Frappe/HRMS database. The firewall/network topology must prevent
access to the production Frappe host and production device addresses. Do not copy
production API keys. Provision staging Employees and shifts for fictitious pins.
Give the staging integration account only the permissions used by this integration.
Use a **separate** external staging ledger and a copied staging configuration.
Never point this procedure at the production ledger, even in read-only mode.

1. Match the production Python/pyzk/pywin32 versions. Run
   `py scripts\apply_pyzk_speedface_patch.py --help` and the existing patch checker
   using its documented check mode on staging. Do not patch production packages.
   Record exact device model, firmware, record layout, timezone, UID behavior and
   port for every model. The patch-definition tests do not prove real decoding.
2. Configure only the isolated staging devices, stable test device IDs,
   `clear_from_device_on_fetch=False`, eight-digit import boundary, staging URL,
   staging tokens and unchanged payload/log-direction semantics. Configure the
   staging shift mapping with exactly those test devices.
3. Generate at least two punches per test employee, including two close timestamps,
   IN/OUT codes and a same-timestamp pair where the hardware permits it. Read using
   the existing `get_all_attendance_from_device` connector. Record the returned
   user IDs, timestamps, UID, punch/status codes; check against device display/export.
   Re-enable and disconnect must work after normal retrieval and injected failure.
   Save protected raw fixture evidence on staging, not in Git.
4. Enable durable mode in the **staging** config, run one engine cycle and inspect
   `sync_cli.py --db <absolute-staging-ledger> verify` and `status`. Accept only if
   every eligible source tuple exists in SQLite before POST and Frappe fields match
   employee pin, exact timestamp, device ID, direction and optional geolocation.
5. Repeat the same history for at least three cycles. Locally known completed rows
   must generate zero extra POSTs. Separately use a fresh test ledger with an already
   existing staging Checkin to verify the actual 417 message/exception identity is
   recognized. Distinct device/source tuples must remain distinct ledger rows.
6. Make **staging Frappe** unavailable for 2 poll cycles without affecting production.
   Generate new punches. Capture count must increase while no records disappear.
   Expect at most one failed destination request per cooldown, then recover. Backlog
   and new captures must both progress; synced/duplicate rows are not resent.
7. Rotate only the **staging** token or its permissions to produce 401/403, then
   restore it. Expect persistent destination cooldown (default >=900 seconds), no
   repeated per-record auth burst, no lost pending records and eventual recovery.
8. Use missing and inactive staging employees. Verify `unmatched_employee` and
   `rejected` are retained; later valid punches continue. Capture a malformed fixture
   record between valid records: invalid row retained and later valid row delivered.
   Do not manufacture corrupt production device history to exercise this test.
9. With an unresolved earlier punch on each of two devices, check the real mapped
   Shift Type watermark is no later than one second before the earliest unresolved
   timestamp, rounded down. Unknown timestamps or failed scans must block updates.
   No Checkin or Shift Type settings are changed by the engine beyond the explicitly
   intended staging watermark API call. Permanently quarantined rows remain blockers.
10. Stop the staging worker after local commit but before POST; restart and verify
    pending resume. Interrupt immediately after remote create (using a staging proxy
    or test transport), restart after the lease and verify confirmed duplicate, not
    a second Checkin. Crash during SQLite transaction; run `verify` after restart.
11. On a **staging-only** installation of the existing direct Python Windows service
    wrapper, verify existing name/executable hosting, start, stop during capture,
    stop during backoff and restart with pending/delivering rows. Stop is observed
    between records/devices/shift updates and in 0.5-second rate-wait slices, after
    any in-flight synchronous call/ingestion transaction finishes. Measure stop time
    against the approved SCM timeout; **do not infer success from mocked tests**.
12. Back up the staging ledger, restore it to a separate file, run `verify` and compare
    total/state counts and representative identities. Drill forward recovery with
    punches captured after the earlier backup. Never overwrite the latest ledger
    or resume the legacy engine until those interval punches are reconciled.

Record timestamp, host, software versions, device model/firmware, expected/actual
result and protected evidence location for each step. Label every unperformed step
NOT EXECUTED. Reject cutover if duplicate recognition, parsing, ACLs, restart,
backup restore, reconciliation or watermark checks do not meet the runbook gates.
