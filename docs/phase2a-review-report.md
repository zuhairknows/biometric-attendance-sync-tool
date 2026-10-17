# Phase 2A review report

Base: `release/phase1` at `8eaa5729697c812f61ee06d3659a58cec2c0ee25`.
Development branch: `feature/phase2-historical-recovery`.

Implemented the reusable read-only ledger repository, source identity verification,
authenticated paginated ERPNext client, reconciliation rules, private CSV/JSON
reporting and CLI. Existing synchronization/service modules and historical hold
rules were not changed. The preserved historical-recovery branch was inspected,
not merged. No live API, ZKTeco connection, deployment or production PC was used.

The synthetic seven-row sample reports one existing punch, one potentially missing
punch, one unmatched mapping, two duplicate sources, one invalid source and one
ambiguous mapping. One source retains its original rejected/cutover-hold state.
All report rows explicitly have recovery eligibility disabled.

Validation commands and results:

```text
py -B -m unittest discover -s tests -p test_historical_investigation.py -v
33 tests passed

PYTHONPATH=tests
py -B -m unittest test_historical_investigation test_durable_sync test_erpnext_sync test_service_hardening test_phase1_release test_phase1_remaining test_two_uncertain_confirmation test_attendance_reconciliation -q
220 tests passed
```

The focused suite covers existing/missing records, cross-device duplicates,
missing/duplicate/inactive employee mappings, held history, timeouts, bad HTTP
responses, missing fields, truncated pages, ignored offsets, pagination limits,
changed destination scans, timezone/precision ambiguity, NULL/blank differences,
uncertain destination evidence, empty ranges, inclusive date boundaries,
interruption, unchanged SQLite bytes, database read-only enforcement and private
CLI diagnostics. All mocked requests must match the explicit GET endpoint allowlist.
The Phase 1 regression selection includes durable delivery, source normalization,
service hardening, release fixes, uncertain confirmation and offline reconciliation.
The full repository suite and SCM/device integration acceptance were not run.

Initial local tests encountered sandbox temporary-directory permissions; the same
synthetic tests ran successfully with approved temporary-file access. An initial
timezone fixture depended on an unavailable Windows IANA database; the test now
uses a deterministic transition fixture. Runtime behavior for unavailable zones
is explicitly tested as unable to verify.

Review limitations before operational use:

- Only ledger-resident punches are covered. No history absent from the ledger can
  be discovered in Phase 2A.
- Destination completeness depends on independently reviewed visibility and
  server semantics. Counts and equal repeated scans detect some failures, but
  cannot prove snapshot isolation or discover permission-hidden records.
- Require quiescent destination writers; otherwise keep the contract unverified.
  Current employee mapping/status does not establish historical mapping/status.
- Seconds with fractional evidence, clock transitions, unknown storage and
  conflicting identities require review; no rounding or guessed recovery is used.
- The engine holds observations in memory and scans selected device history for
  safe date exclusion. Split large investigations into smaller ranges.
- Remaining checks are staging-only deployed semantics, permissions, timestamp
  precision/timezone, historical policy and count/list agreement. Production
  execution, deployment, merge and Phase 2B are outside this review.

See `phase2a-historical-investigation.md` for operator steps, report meanings,
exit codes and the future application interface. The final implementation commit
is reported with the delivered Git revision; neither main nor release/phase1 is merged.
