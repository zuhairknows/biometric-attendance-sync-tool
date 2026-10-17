# Phase 2A operator and review guide

Investigate available ledger punches on a development PC using a consistent offline
ledger copy and a reviewed ERPNext destination. Do not run this milestone on the
production Windows PC. It never submits Employee Checkins, fetches devices, changes
holds, imports configuration, or starts/stops the service.

## Run an investigation

1. Obtain a consistent ledger backup through your existing approved backup process.
   Transfer the copy to private development storage. This tool does not make the
   backup, open devices, or alter the production ledger. Do not copy an actively
   changing SQLite file without a consistent backup process.
2. Prepare a private contract from `phase2a-contract.template.json`. A reviewer must
   verify each claim against the deployed code, hooks, permissions and historical
   device settings for the selected dates/devices. Record the evidence reference,
   correct site origin, local timezone, precision and duplicate key. Confirm all
   destination writers are quiescent during the investigation. Leave uncertain
   claims false. Hash the reviewed file independently with `Get-FileHash -Algorithm
   SHA256`. A hash binds the file; it does not prove its assertions.
3. Use a token with read access to **all relevant Employee and Employee Checkin
   records**, including inactive employees and all destination devices. Verify
   user permissions, permission query conditions, field restrictions, deployed
   overrides and the read-only `frappe.client.get_count` implementation. HTTP 200
   and matching counts do not prove unrestricted visibility.
4. Set `INVESTIGATION_API_KEY` and `INVESTIGATION_API_SECRET` in the process
   environment using your approved secret mechanism. Do not put credentials in
   arguments, the contract, repository files, logs or shell history.
5. Create a private, local, dedicated output directory outside the checkout and
   input directories. Existing Windows output ACL checks apply. Then run:

```powershell
py -B scripts/investigate_attendance.py `
  --ledger-copy 'C:\PrivateInvestigation\inputs\ledger-copy.sqlite3' `
  --start '2026-10-01 00:00:00' --end '2026-10-17 23:59:59' `
  --device 'FP2_DEVICE_08' --device 'FP2_DEVICE_11' `
  --site-url 'https://reviewed-staging.example.test' `
  --contract 'C:\PrivateInvestigation\inputs\reviewed-contract.json' `
  --contract-sha256 '<independently reviewed SHA256>' `
  --output 'C:\PrivateInvestigation\reports'
```

Ranges include both endpoints. Use `.999999` for the end if the full final second
is required with microsecond precision. Supply naive local wall-clock datetimes
matching the reviewed site timezone; UTC conversion is deliberately unsupported.
No Phase 1 activation-date restriction is applied to investigation.

The CLI prints only status and record count. Exit codes: `0` completed, `2`
unverified comparison (reports still exported), `1` input/read/export failure,
`130` interrupted. Unverified contracts prevent API reads. Missing credentials
are a setup error. An empty range produces a header-only report without API reads.

## Interpret reports

| Classification | Meaning |
| --- | --- |
| `already_existing` | Exactly one destination identity matches under the reviewed rule |
| `potentially_missing` | No destination identity in the verified comparison; human review required |
| `unmatched_employee` | Complete employee scan returned no matching attendance device ID |
| `duplicate_source` | Multiple selected sources share one destination identity; all members flagged |
| `invalid` | Original source evidence cannot reproduce the Phase 1 identity/payload |
| `ambiguous` | Mapping, timestamp, direction, or destination identity conflicts |
| `unable_to_verify` | Contract/API/pagination/data uncertainty or conflicting delivery evidence |

`eligible_for_recovery` is always false. `potentially_missing` does not authorize
replay, even for held records. A `cutover_hold` remains the original ledger
classification; the CSV adds investigation columns without changing source state.
The detailed CSV retains the original identity, raw evidence, saved payload and
audit columns. Spreadsheet formula cells are escaped; use the original ledger
for byte-exact source values. Reports contain employee data and must remain private.

The JSON includes per-device counts (including zero-row selections), holds,
limitations, matching policy, input observation fingerprints and a CSV SHA256.
Publication never overwrites existing files. The JSON is published last and is
the completion marker; orphan CSV/`.part` files do not constitute a completed run.
After interruption rerun the full investigation; there is no automatic resume.

## Implementation and review boundaries

`historical_attendance/` separates the SQLite repository, source verification,
GET-only client, business rules and reporting. The future Windows application can
call `investigate(...)` with a cancellation callback and use the returned data.
Wire the same callback into `ReadOnlyFrappeClient(cancelled=...)` to check between
HTTP requests. A request already in progress ends on response/timeout; cancellation
does not interrupt SQLite's integrity check. No delivery component is imported.

The repository reuses `Ledger(readonly=True)` with `query_only`, a read transaction
and Phase 1 schema/integrity validation. Source validation reuses
`punch_normalization.normalize` without its cutover filter. Comparison reuses strict
time parsing, timezone transition checks and CSV escaping from
`scripts/attendance_reconciliation.py`. The preserved recovery-preview branch was
reviewed for NULL/blank separation and verification safeguards; it was not merged.
The small ledger adapter retains those comparison rules for arbitrary device IDs
and saved per-punch policies without changing the older fixed-device offline tool.

The API uses authenticated HTTPS GET requests only: paginated resource lists and
the allowlisted `frappe.client.get_count` method. Counts before/after each scan,
explicit terminal empty pages, unique ordered names, bounded pages and two equal
full scans are required. See [Frappe pagination documentation](https://docs.frappe.io/framework/user/en/api/rest)
and the [Frappe v15 client source](https://github.com/frappe/frappe/blob/version-15/frappe/client.py).
These checks detect truncation, observable drift and ignored offsets. They cannot
establish transactional snapshot isolation or discover permission-hidden records.
If visibility or quiescence is uncertain, leave the contract unverified.

Second precision is supported conservatively: fractional source or destination
values require review rather than rounding into an absence claim. Microsecond
comparison requires an explicitly verified microsecond contract. SQL NULL and
empty log type stay distinct. Both reviewed duplicate keys (`employee_time` and
`employee_time_log_type`) ignore destination device ID, preserving cross-device
duplicate behavior. No guessed mapping, inactive employee or arbitrary source
representative becomes a recovery candidate.

Only ledger-available punches are covered; unknown timestamps/evidence remain
visible regardless of range. Current employee mapping does not prove historical
mapping. The engine scans selected devices' ledger history to validate date
exclusion and holds the selected evidence and destination observations in memory.
Use smaller ranges for large ledgers; requests have a 30-second timeout and a
10,000-page safety bound. REST read consistency and historical source policy need
site-specific verification before operational use.

## Local validation

```powershell
py -B -m unittest discover -s tests -p test_historical_investigation.py -v
$env:PYTHONPATH = 'tests'
py -B -m unittest test_historical_investigation test_durable_sync test_erpnext_sync test_service_hardening test_phase1_release test_phase1_remaining test_two_uncertain_confirmation test_attendance_reconciliation -q
```

Tests use synthetic databases and mocked HTTP. They assert unchanged source bytes
and allow only the two resource GETs and count GET. The synthetic sample in
`examples/phase2a-investigation/` illustrates classifications, cross-device
duplicates and an unchanged historical hold; it contains no real employees.

Remaining verification: reviewed staging-site permissions, effective HRMS/Frappe
duplicate/timezone/precision behavior, historical payload policies, count/list
agreement and quiescence. No production, device, service or deployment validation
was performed or authorized. Phase 2B remains outside this milestone.
