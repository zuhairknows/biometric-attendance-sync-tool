# Phase 2A fixes and revalidation report

## Production compatibility safety extension

Extension base: `459bff1ee5fc0873aa9749699b9356488dd5cce8`.
The only known live site is `https://erp.myfpf.com`; no staging environment is
assumed. The production synchronization service remains running. No live
verification has been executed as part of this extension.

Minimal safety changes: configurable CLI GET budget (default 32), inactivity
timeout (10 seconds), isolated-process investigation/report deadline (120 seconds),
explicit production flag plus matching-origin acknowledgement, and a production
contract labelled with the destination environment. The known production hostname
cannot omit acknowledgement merely by omitting the environment label.

Production mode additionally requires independently evidenced
`read_only_api_verified`. It can read without falsely attesting the original seven
deployment claims, but forcibly blocks reconciliation and retains every false
claim. It never produces potentially-missing or already-existing decisions; source
rows remain unable-to-verify/invalid and all eligibility flags stay disabled.
Even consistent successful API reads return `status=unverified`,
`compatibility_status=reads_complete`, exit 2. Quiescence is not fabricated.

The supervisor bounds source SQLite validation/reading, GETs (including stalled
bodies), reconciliation/report processing and publication. It terminates a stalled
worker instead of leaving a background request thread alive. Local input/ACL
preflight precedes the budget; termination allows up to two seconds cleanup.
Artifacts from a failed/deadline/interrupted run are not accepted without a
completed terminal receipt. Process IPC files inherit private output-directory
protection and are removed by the supervisor.

Current validation: 59 focused Phase 2A tests (48 existing plus 11 new production
safety tests); expanded relevant Phase 1/Phase 2A suite: **332 passed in 42.437s**.
Only synthetic SQLite, mocked HTTP and isolated local worker processes were used.
New cases cover required acknowledgement, environment labelling, read-only endpoint
evidence, no false quiescence claim, no production reconciliation even with every
claim true, a hard one-GET cap, invalid limits, failed deadline/no receipt, sanitized
worker errors, actual termination of a stalled mocked GET and a complete spawned
CLI/report path with no network. Phase 1 synchronization modules remain unchanged.

GO only to prepare the limited production read-only probe. Execution is NO-GO
until independent endpoint-safety evidence, approved backup/range/account/contract
and explicit execution approval exist. Remaining independent claims are database
lookup equality, complete permission visibility, duplicate/NULL semantics,
timezone/precision, historical payload settings, relevant destination stability
and effective identity-mutating hooks. Consistent API observations cannot prove
those claims. The running service can legitimately change production; site-wide
unchanged-record counts are not promised.

Operator procedure: `phase2a-production-read-only-verification.md`.
Contract: `phase2a-production-contract.template.json`.
No push, merge, deployment, production/device access or Phase 2B work is authorized.

## Prior reconciliation implementation and fix evidence

Base: `release/phase1` at `8eaa5729697c812f61ee06d3659a58cec2c0ee25`.
Development branch: `feature/phase2-historical-recovery`.
Fix starting point: `1047a5fae78faba05be39969cfc4f7d04c32c9cf`.

Implemented the reusable read-only ledger repository, source identity verification,
authenticated paginated ERPNext client, reconciliation rules, private CSV/JSON
reporting and CLI. Existing synchronization/service modules and historical hold
rules were not changed. The preserved historical-recovery branch was inspected,
not merged. No live API, ZKTeco connection, deployment or production PC was used.

The synthetic seven-row sample reports one existing punch, one potentially missing
punch, one unmatched mapping, two duplicate sources, one invalid source and one
ambiguous mapping. One source retains its original rejected/cutover-hold state.
All report rows explicitly have recovery eligibility disabled.

## Findings resolved

| Severity | Root cause | Applied fix |
| --- | --- | --- |
| High | Exact Python string keys replaced database equality and could hide equivalent duplicate mappings | Each distinct unchanged source ID now gets a counted, paginated Employee equality lookup. All active/inactive matches are preserved under the queried source ID. Results are cached per ID, then verified by a second pass. Any incomplete lookup blocks comparison. |
| Medium | Python Unicode ordering was assumed to match database collation ordering | Server `name asc` remains; a set detects repeated names within/across pages without Python ordering comparisons. Counts, terminal pages, repeated reads, timeout and page bounds remain. |
| Low | Directory privacy and source separation were enforced only by the CLI | `reporting.validate_output` is shared by CLI preflight and `export` publication. Direct export requires the source ledger path, enforces the existing private-directory/protected-root guards and rejects input directories/children. Other input/config paths are supplied through `input_paths`. Atomic non-overwriting publication is retained. |

The contract now requires `employee_lookup_database_equality`. Older or unverified
contracts block comparison. No Python trimming/case-folding is performed in the
production lookup path. Synthetic mocks model a reviewed database collation;
that model is not evidence of staging's actual semantics.

Lookup requests run sequentially in pages of up to 200 records by default. Limits:
1,000 distinct original IDs, 10,000 pages per scan, 20,000 total GETs across both
passes and a 30-second request timeout. Exceeding a limit produces an unverified
result, never a truncated missing-record decision. No automatic retries are added.

Validation commands and results:

```text
py -B -m unittest discover -s tests -p test_historical_investigation.py -v
48 tests passed

PYTHONPATH=tests
py -B -m unittest test_historical_investigation test_durable_sync test_erpnext_sync test_service_hardening test_phase1_release test_phase1_remaining test_two_uncertain_confirmation test_attendance_reconciliation test_frappe_attendance_export test_frappe_server_export -q
321 tests passed in 37.827 seconds
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
It also includes authoritative database/export lookup behavior. Fifteen added
focused tests cover unchanged IDs on the wire, whitespace/case equivalence,
ambiguous active/inactive equivalents, per-ID caching, incomplete later lookup
pages, later-page errors/timeouts, unverified equality semantics, `a`/`B` database
ordering, duplicate names, mapping drift, global request/ID limits, direct export
privacy/default ACL checks, config/service/source-directory exclusions,
non-overwriting output, and every ledger state/hold remaining unchanged.
Existing CSV/JSON integrity and safe-diagnostics checks remain in place. The
synthetic sample was regenerated and its CSV hash verified. Diff checks passed.
The full repository suite and SCM/device integration acceptance were not run.

Tests used approved temporary synthetic fixtures and mocked HTTP only. No real
server, production PC, devices or running service were accessed. The new drift
fixture returns independent response dictionaries, matching real decoded JSON;
the ordering fixture links its Checkins to the synthetic renamed employee.

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

## Recommendation

The following recommendation applies only to a separately verified nonproduction
destination if one becomes available. It does not imply such a site exists or
authorize reconciliation against the current live site; use the production-only
compatibility procedure above for the current environment.

GO for controlled read-only staging verification, with a separately reviewed
contract and unrestricted read visibility, including database equality semantics.
This is not approval for recovery, production execution or Phase 2B. There are no
remaining confirmed blockers from the three findings under review. The staging
assumptions above remain unverified until tested against that staging deployment.

See `phase2a-historical-investigation.md` for operator steps, report meanings,
exit codes and the future application interface. The final implementation commit
is reported with the delivered Git revision; neither main nor release/phase1 is merged.
