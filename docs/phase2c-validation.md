# Phase 2C local validation

Base: `434a40cb12ee8d614ac5f9220686a3a0e7cc0a55` on
`feature/phase2-historical-recovery`. Final revision is in the delivery report.

Implemented a separate Tkinter application with reusable desktop adapters, a
disposable synthetic demo, a whitelist-only preferences file, and an independent
launcher. The three tabs reuse Phase 1 diagnostics, the exact bounded Phase 2A CLI,
and Phase 2B approval/simulation/report functions. Dependencies are standard Windows
Python 3.11/Tk plus `requests` for explicitly initiated investigation GETs.

Backend changes are limited to an optional cancellation callback on the existing
bounded worker, read-only snapshot access to an existing recovery audit database,
and injectable existing ACL validation for the contained synthetic demo. Production
defaults retain the real privacy checks. Phase 1 synchronization, normalization,
ledger, startup, delivery and service modules are unchanged.

## Automated checks

Run suites sequentially: the existing synthetic concurrency tests intentionally use
the same machine-wide Phase 1 worker mutex. Concurrent runtime suites invalidate
one another's results; no mutex or synchronization behavior was changed to avoid it.

```powershell
$env:PYTHONPATH = 'tests'
py -3.11 -B -m unittest test_desktop_manager -q
py -3.14 -B -m unittest test_desktop_manager test_historical_recovery test_historical_investigation test_phase2a_production_verification test_durable_sync test_erpnext_sync test_service_hardening test_phase1_release test_phase1_remaining test_two_uncertain_confirmation test_attendance_reconciliation test_frappe_attendance_export test_frappe_server_export -q
```

- Python 3.11: **33/33 manager tests passed** (4.300 seconds).
- Python 3.14: **395/395 expanded tests passed** (48.709 seconds).
- Python 3.11, same expanded command with `-3.11`: **394 passed, 1 failed**
  (47.646 seconds). The unchanged legacy test
  `test_attendance_reconciliation.ReconciliationTests.test_fresh_process_has_no_engine_sdk_http_or_sqlite_imports`
  blocks every `urllib` import before importing `pathlib`; Python 3.11's standard
  `pathlib` itself imports `urllib.parse`. This is a pre-existing test-guard
  incompatibility, not an HTTP request or a manager failure. Both the legacy test
  and `scripts/reconcile_attendance.py` are unchanged from the starting commit.
  The full Python 3.11 suite is therefore not reported as green.
- Python 3.11 syntax parsing and launcher `--help` passed.
- Rendered synthetic investigation and recovery windows inspected on Windows;
  minimum 960x800 layout and asynchronous UI actions tested.
- No production API, biometric connection, service operation or deployment was used.

Manager tests cover read-only dashboard loading, stopped/pending/unavailable service
states, corrupt/missing databases, retry/quarantine/hold/delivery counts, immutable
source bytes, audit read-only SQL enforcement and no audit/lock-file creation while
browsing. They also cover selected approval with confirmation, changed source and
contract rejection, malformed audit metadata, idempotent simulation, unapproved
records, production-target rejection, normal-writer contention, protected exports,
CSV/JSON consistency and non-overwrite, CLI worker/receipt integration, production
results staying unverified, missing prerequisites, environment restoration, no
credential/preferences leakage, error redaction, filters, responsive worker failure,
startup without polling, active-worker cancellation and closing without service calls.

Existing Phase 2A/2B tests retain mapping/pagination/timezone/hold/HTTP-verb guards,
uncertain outcomes, source hashes and source-state preservation. Synthetic simulation
submissions are in-process fixture operations through the existing Phase 1 sender;
there is no network recovery transport or live enable flag.

## Limits and decision

**GO for isolated synthetic desktop review. NO-GO for production recovery.**
Real operator acceptance still needs deployed Windows ACLs, service-query permissions,
display scaling and ledger-size checks. Production GETs need separately approved
endpoint-safety evidence and the existing Phase 2A contract. Compatibility-only reads
cannot authorize recovery. Live recovery still needs independent deployed identity,
mapping, timezone, precision, hook and permission verification; source/audit provenance;
cross-host fencing and source/live-ledger binding; and an authorized concurrency and
uncertain-delivery policy. Held records remain excluded without a separately designed
and approved transition. No production service stop or mutex bypass is authorized.

See the [operator guide](phase2c-manager.md). No push, merge or deployment is included.
