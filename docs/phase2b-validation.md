# Phase 2B implementation validation

Branch: `feature/phase2-historical-recovery`.
Base: `d2b7f4c52ff2a438b6634c79dc030769421152b2`.

Added reusable recovery batch/approval/execution functions, a private SQLite audit
store, a persistent synthetic destination, explicit CLI commands, protected CSV/JSON
export and an offline fixture generator. No Phase 1 synchronization/service module
or Phase 2A comparison rule was changed. The sender is the existing Phase 1
`FrappeClient.send`; pre/post comparison is the existing Phase 2A investigation.

Execution is intentionally synthetic-only. There is no live transport, production
enable flag, source hold release, global reset, queue replay, service operation or
background worker. The seven requested statuses are separate recovery audit states;
they never replace source ledger states.

Validation commands:

```powershell
$env:PYTHONPATH = 'tests'
py -B -m unittest test_historical_recovery -v
py -B -m unittest test_historical_recovery test_historical_investigation test_phase2a_production_verification test_durable_sync test_erpnext_sync test_service_hardening test_phase1_release test_phase1_remaining test_two_uncertain_confirmation test_attendance_reconciliation test_frappe_attendance_export test_frappe_server_export -q
```

Results: **30 focused recovery tests passed; 362 expanded regression tests passed
in 42.638 seconds**. Tests used temporary synthetic databases, mocked HTTP and one
local process holding the Phase 1 writer mutex. No ERPNext/device connection or
production service, database, configuration or attendance mutation occurred.

Coverage includes explicit selected approval; normal recovery and independently
verified success; existing Checkins; repeated execution; cross-batch prior intents;
crashes before sending and after remote commit; timeouts before/after commit;
ambiguous/inactive mappings; fractional timestamps and duplicate sources; held
records; changed report/contract/source/batch contents; source WAL sidecars;
uncertain Phase 1 peers; partial failures; incomplete lookup pagination; normal
worker conflicts; competing recovery writers; disabled production/arbitrary live
transports; private workspace guards; writable-fixture input aliases; audit schema
corruption; result hashes; generated inputs; and CLI preparation/approval/simulation/
show/export. Execution tests assert unchanged source database bytes.

The documented synthetic input generator produced a preparable complete Phase 2A
report, and the resulting approved source recovered through the Phase 1 sender.
The output report contains status events, approval references, original source
snapshots and durable submission outcomes. CLI diagnostics contain no employee IDs
or credential values. Diff checks passed; all production execution remains disabled.

## Remaining operational blockers

- Current production compatibility-only results cannot authorize recovery. A full,
  independently verified Phase 2A investigation and deployed endpoint/permission/
  hook/duplicate/timezone/precision evidence are still required.
- Historical held/rejected records remain excluded. No hold transition is designed
  or authorized by this implementation.
- Phase 1's lifetime global mutex prevents same-host recovery while the normal
  service owns it. Do not stop or bypass the service to work around this block.
- Cross-host fencing, canonical audit-store ownership, current source-to-live-ledger
  binding and stability between GET revalidation and POST need operational design
  and independent verification before any separately approved live enablement.
- An uncertain intent is never automatically retried, even after empty destination
  reads. Manual review is required; do not clear intents or recreate the store.

See `phase2b-controlled-recovery.md` for the operator interface and limitations.
This milestone is complete for synthetic review, **NO-GO for production execution**.
No push, merge, deployment or Phase 2C is included. The final Git revision is
reported with the implementation delivery.
