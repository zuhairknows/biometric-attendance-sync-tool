# Offline historical comparison

Use **Historical Investigation → Offline Comparison** in the existing manager.
This workflow uses no ERPNext API, credentials, biometric connection or delivery
transport. It reads an approved offline ledger snapshot. The service stays running.
All results are **Offline comparison — Not authorized for recovery**. Offline
reports have a distinct schema/purpose rejected by the existing recovery engine.

## Prepare the three inputs and their evidence

Use an existing protected, coherent, self-contained SQLite backup (including
committed WAL content). Never copy the running database naively or select the live
ledger. This tool cannot independently prove backup provenance.
The snapshot must use self-contained rollback-journal format with no WAL, SHM or
hot-journal sidecars; incompatible snapshots fail before SQLite opens them.

Prefer standard Frappe Data Export, CSV, with only these fields:

- Employee: `ID`, `Attendance Device ID`, `Status`. Export every employee, including
  Active, Inactive and Left; no status/device/department filters.
- Employee Checkin: `ID`, `Employee`, `Time`, `Log Type`, `Device ID`. Export every
  Checkin in an explicitly inclusive site-local interval, across all employees and
  devices. The only filters are time >= start and time <= end.

Internal headers (`name`, `attendance_device_id`, `status`, `employee`, `time`,
`log_type`, `device_id`) are also accepted; optional `modified`/`Modified` is allowed.
UTF-8, optional BOM, one header row. Metadata preambles, extra columns, repeated
headers, duplicate record IDs, malformed records and unsupported encodings are
rejected as unverified. Re-export matching fields rather than editing values.
Do not open/resave identifiers or timestamps through Excel: leading zeros and
precision must survive unchanged. Formula-like values are inert input strings;
the protected output CSV escapes them for spreadsheet safety.

Copy the [evidence template](../examples/offline-comparison/evidence.template.json)
to the private import directory beside the three files. Fill it from independently
reviewed evidence, never from assumptions:

1. Exact file names and SHA256, origin and timezone-aware creation time for each
   export; independent full database counts and references. Counting the CSV itself
   is not independent evidence. Record actual filters; a filtered Employee export
   or additional Checkin filters cannot establish absence.
   If the original retrieval time was not recorded, omit `created_at` or use JSON
   `null`. This adds a specific missing-creation-time blocker and always forces
   Unverified, even if all other evidence is verified. Do not invent a time or use
   the file modification time; placeholders, malformed, timezone-free and future
   timestamps remain invalid. A valid timestamp alone never verifies completeness.
2. Inclusive export coverage containing the selected range, verified timezone,
   timestamp precision, deployed duplicate rules, historical payload settings and
   the unchanged seven Phase 2A claims. Unknown claims stay false. These are reviewed
   administrator assertions, not facts proven by importing CSV. Destination stability
   means consistency of the original export set, not that a running service is quiet.
3. `employee_lookup_results` for every distinct original source attendance ID:
   independently verified complete database-equality matches, including inactive and
   equivalent case/trailing-space IDs. Keys preserve the original ID exactly; values
   list stored Employee IDs present in the full export. A verified empty list means
   unmatched; multiple matches or inactive matches remain ambiguous. CSV equality,
   trimming or case-folding cannot establish these results. Without this evidence,
   comparison remains Unverified even when IDs look identical.
4. Explicit NULL/blank encoding evidence. Empty Log Type means an actual empty
   string; `__SQL_NULL__` means SQL NULL. Ordinary Data Export may collapse these
   states into blank cells. If so, leave `log_type_encoding_verified=false`; do not
   replace blanks by guessing. An independently verified absence of NULLs or an
   administrator-reviewed lossless export is needed before approving the encoding.
5. A review reference and `export_set_consistent=true` only when the exports share
   sufficient stable, complete visibility. No new endpoint or verification flag is
   enabled by this process. The example timezone/rules/coverage are placeholders.

CSV header templates are in [examples/offline-comparison](../examples/offline-comparison/).
No external ETL libraries or additional database are needed. Limits: 16 MiB per
CSV/evidence file, 100,000 rows per CSV, 200 selectable files and 120 seconds per
comparison worker. Split large exports into separately reviewed packages/ranges.

## Configure and run locally after operator approval

Keep the isolated manager installation and its existing private profile. Add:

```json
{
  "offline_directory": "C:/BiometricReview/PrivateImports",
  "offline_evidence": "evidence.json",
  "offline_evidence_hash": "INDEPENDENTLY_APPROVED_EVIDENCE_SHA256",
  "output": "C:/BiometricReview/PrivateReports"
}
```

These are example private paths, not instructions to alter production ACLs. Both
directories must already exist. The import directory and each input file must pass
the existing Windows private ACL guard (protected ACL inheritance, no broad grants).
Output must meet the existing private-directory checks and be separate from imports,
service state and checkouts. Use approved private copies; never change production
permissions merely to pass. Hashes in evidence use lowercase SHA256. For local
inspection without API requests:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\BiometricReview\PrivateImports\evidence.json'
$Profile = Read-Host 'Existing approved private manager profile absolute path'
& '.\Start Biometric Manager.bat' --investigation-only --profile $Profile
```

`$Profile` is the existing approved absolute private profile path. No API site,
API contract or credentials are required for offline mode; existing monitoring and
device settings remain. `--monitor-only` denies every offline route. Normal and
investigation-only modes allow offline comparison; investigation-only denies all
recovery routes. The production API workflow and `read_only_api_verified` gate are
unchanged. An offline evidence file is not an API verification contract.

Select the approved snapshot and two CSV files from the directory's dropdowns,
then select range, floor and devices. File names must also match the pinned evidence.
There are no uploads, arbitrary paths or browser downloads. Start with a short FP1
range. Review status, blockers, classifications and counts; filter/search details.
Completed runs automatically publish protected CSV/JSON; **Verify protected CSV /
JSON** rechecks them and their inputs. No new files overwrite earlier reports.

Missing completeness/mapping/precision evidence yields **Unverified**, never a
confident missing classification. Unreadable/unapproved source or evidence, changed
inputs, unsafe paths/permissions, timeout and publication failures can block the job
without an accepted report. Partials are not evidence. Invalid source rows remain
visible. Eligible-for-recovery and execution flags stay false, even for complete
offline comparisons. Historical cutover holds are reported and never changed.

Before live recovery: independently verify deployed mapping/visibility/rules,
source-to-live-ledger binding, concurrency/fencing, audit ownership and uncertain
delivery reconciliation; obtain separate execution approval. Any hold release
requires its own authorized design. This task implements none of those transitions.

## Private offline diagnostics

Each offline page/action has a random diagnostic reference. The job page, polling
response and return/error page show only that reference, a stable stage/code and
a fixed next step. Find `offline-<reference>.jsonl` in the approved diagnostic
directory. There is no browser log-download endpoint.

The default is the existing private startup-profile directory. If it does not meet
the existing output/privacy rules, add `"diagnostics_directory": "C:/BiometricReview/PrivateDiagnostics"`
to the startup profile, using an already approved existing private local directory.
It must be outside imports, checkouts and service state, with protected private ACLs,
write access and sufficient free space. Direct controller callers without a loaded
profile default to the approved report-output directory. No directory/ACL is created
or changed automatically. If logging cannot be established, the action stops with
a safe diagnostic-location error; it never falls back to console or public temp logs.

Each JSONL entry contains exactly `reference`, `stage`, `code`, `event`. No paths,
file names, hashes, API credentials, employee IDs, punch times, raw records, exception
text or traces are logged. Stages are validation checkpoints, not database state
changes. `unverified` events can end in `complete / result_unverified`: this is an
accepted Unverified report, not a failed worker. Missing creation times follow that
path and never permit a missing-punch finding.

Common failures:

| Stage / code | Next check |
| --- | --- |
| `profile / missing_setting` | Required offline profile fields and selected range |
| `source_path` or `evidence_path / file_not_found` | Approved file selection/existence |
| `source_path`, `evidence_path` or CSV stage / `output_acl_not_private` | Individual input file ACLs, not only parent directory ACLs |
| `evidence_hash / sha256_mismatch` | Independently approved evidence SHA256 |
| `source_hash / offline_source_changed_or_unapproved` | Snapshot binding and unchanged bytes |
| `source_format / self_contained_rollback_journal_backup_required` | Snapshot format and sidecars |
| `source_read / source_schema_unsupported` | Backup schema versus reviewed manager version |
| `output / output_must_be_existing_absolute_directory` | Existing approved report directory |
| `employees_csv` or `checkins_csv / offline_csv_headers_invalid` | Required export headers |
| `publication / permission_denied` | Private report write permissions |
| `worker / investigation_deadline_exceeded` | Incomplete action; no automatic retry or limit increase |

The existing ACL checker requires protected inheritance on individual input files;
inherited-only file ACLs may fail even when Dashboard reads succeed. Do not change
production permissions or relax validation to make a comparison pass. Logs are
diagnostic metadata, not evidence authorizing recovery. Retain/archive them privately.

Previously, controller preflight errors were replaced by one ManagerError, the bounded
worker discarded exceptions, and CSV import errors were reduced to generic blockers.
The app's Flask logger is intentionally disabled. This change adds a separate fixed
diagnostic channel without enabling raw Flask/debug logging. Old generic failures
cannot be reconstructed from HTTP access logs; no IT-SUPPORT root cause is asserted
without a new approved diagnostic reference.

Local validation: 476 relevant tests passed, including 14 new diagnostic tests
(`test_offline_diagnostics`). All inputs/services were synthetic or mocked; this
does not establish production permissions or identify a past deployed failure.
