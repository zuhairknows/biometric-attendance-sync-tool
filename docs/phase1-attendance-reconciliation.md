# Phase 1 offline attendance reconciliation

This utility compares protected local copies of published capture evidence with
separately prepared Frappe exports. It never contacts a terminal or Frappe, imports
configuration/credentials, opens the production ledger/logs, or operates a service.
It does not create an import file or authorize recovery. Every missing punch needs
review and a separate recovery scope. Do not deploy or run against production
evidence under the development task that introduced this utility.

The operator supplied these incident totals: Device 08 44,010; Device 11 37,128;
81,138 full-history punches; 2,280 valid punches on October 14–15, 2026. Captures
and hashes reportedly passed on October 15. Development did not open those files
or independently verify those numbers. The utility recomputes full-history counts
from the supplied manifests; it does not hard-code incident totals.

## Matching rules and their evidence

Reviewed repository implementations:

- `erpnext_sync.py::send_to_erpnext` posts `user_id` as `employee_field_value`
  to `add_log_based_on_employee_field`; no employee-field override is sent.
- `erpnext_sync.py::pull_process_and_push_data` and
  `punch_normalization.py::normalize` derive AUTO direction with OUT codes before
  IN codes. The default code lists are IN `[0,4]`, OUT `[1,5]`; verify the actual
  configured values separately and put only these non-secret settings in exports.
- `erpnext_sync.py::is_duplicate_employee_checkin_response` accepts HTTP 417
  with `This employee already has a log with the same timestamp`. That response
  alone does not identify the deployed server's duplicate fields.
- `punch_normalization.py` uses device, user ID, full timestamp, punch/status and
  UID for its local ledger identity. This is not the destination Checkin identity.

The [upstream Frappe HR implementation](https://github.com/frappe/hrms/blob/version-15/hrms/hr/doctype/employee_checkin/employee_checkin.py)
defaults the employee field to `attendance_device_id`. The version-15 source
reviewed on October 15, 2026 includes log type in duplicate validation and truncates
microseconds. Other revisions/customizations can differ. Upstream branch contents
are not proof of the deployed version. Do not select a duplicate policy by guessing.

An authorized administrator must inspect the deployed endpoint, Employee Checkin
validation/before-validation, overrides and hooks separately. Record an immutable
source commit or reviewed source-file hash in `server_source_reference`, then attest
the exact duplicate fields and timestamp precision in the export manifest. If that
cannot be verified, stop export preparation; the CLI rejects unverified semantics.
No production source lookup is performed by this reconciliation utility.

Employee resolution uses exported results of the same database lookup as the
endpoint, rather than approximating database collation in Python. Preserve the
original lookup string exactly, including leading zeros and whitespace. Include
every result and inactive employees. Although some server versions select the first
result, reconciliation treats multiple results as ambiguous and never chooses one.
Absent lookup rows are ambiguous; only an explicit empty result means unmatched.

Destination matching always includes Checkins from **all devices**, including blank
or unrelated device IDs. Device ID, UID, punch/status, shift, skip-auto-attendance
and attendance links do not restrict duplicate matching. No fuzzy time tolerance,
employee-name fallback, UID employee lookup, or automatic timezone conversion exists.

## Required input layout and trust

Use reviewed, authorized, protected local copies on an offline review workstation:

```text
inputs/captures/
  original capture_summary_....json
  both original device snapshot JSON files named by that summary
inputs/exports/
  export-manifest.json
  employee-mappings.json
  employee-checkins.csv
reports/                              # separate private directory, not below inputs
```

The original capture summary and snapshots must be unchanged byte-for-byte. The
CLI consumes `zkteco-offline-capture-v1` as published at commit `3c0bda5`, including
`records[].raw`, `normalized`, `valid`, `raw_complete`, `validation_categories` and
`source_order_index`. It requires both approved devices, completed capture status,
confirmed cleanup, matching metadata, valid record order, and matching full-snapshot
counts in each file and summary. Date filtering happens only after validation.

Supply the independently recorded SHA-256 of the **original persisted capture
summary**, not a reformatted console copy. The original capture console output
contains `summary_sha256`. The summary in turn pins both device snapshots. Pin the
export manifest using its separately approved SHA-256; it pins the two exports.
Preserve trusted hashes out of band, such as in the approved incident record. A hash
computed from an untrusted replacement immediately before processing is not an
independent trust anchor. Hashes detect changes, not who created the evidence.

Inputs are read into bounded local memory (maximum 512 MiB per file); byte hashes
are checked before JSON/CSV interpretation. JSON duplicate keys/nonfinite values,
path traversal, escaped file links, missing files and inconsistent counts fail.
For this 81,138-punch incident an ordinary workstation should be sufficient, but
allow memory for parsed JSON, indexes and CSV rows. Processing is indexed rather
than comparing every source punch to every Checkin. No state database is created.

## Separately prepared Frappe exports

Export preparation is a separate authorized, read-only administrator task. These
instructions specify required data; no exporter is built into the CLI. Do not run
them against production as part of development/testing. Use a consistent database
snapshot for employee resolution and Checkins, record export time/site timezone,
verify complete row counts independently, and include all pages without permission,
device, employee-status, company, shift or log-type omissions. Concurrent writes or
permission-limited exports cannot support missing classifications reliably.

The export timestamp must be at or after the latest device capture finish. This
comparison describes the exported snapshot, not later changes to Frappe. Current
employee mappings may differ from the historical incident; review that limitation
before any recovery decision. No `employee_name`, phone, email or API token is needed.

### Employee mapping JSON

UTF-8, exact schema:

```json
{
  "schema": "frappe-employee-lookups-v1",
  "lookups": [
    {"user_id": "001", "matches": [
      {"employee": "SYNTH-EMP-001", "attendance_device_id": "001", "status": "Active"}
    ]},
    {"user_id": "002", "matches": []}
  ]
}
```

Prepare a lookup row for every distinct `normalized.user_id` in the source captures
(at least every valid record in the requested range). The lookup key is always a
JSON string. Do not cast it to an integer or trim it. To reproduce endpoint behavior,
the separately authorized Frappe administrator uses this read-only query for each
exact input string, including all returned rows:

```python
# Frappe administrator console context ONLY; not a Server Script or part of this CLI.
matches = frappe.db.get_values(
    "Employee", {"attendance_device_id": user_id},
    ["name", "attendance_device_id", "status"], as_dict=True
)
# Export name as employee; retain attendance_device_id and status as strings.
# Keep [] when there are no matches; never select matches[0] or filter Active only.
```

This preserves database-specific case/accent/trailing-space matching. A plain
Employee CSV joined by Python equality is not an equivalent mapping export.
Duplicate lookup rows, duplicate employee names within a lookup, or malformed fields
fail validation. Different lookup strings may legitimately resolve to one employee;
source collisions are then checked on the destination identity.

### Employee Checkin CSV

UTF-8 (BOM allowed), comma separated, standard CSV quoting; exact header order:

```csv
name,employee,time,device_id,log_type
SYNTH-CHECKIN-001,SYNTH-EMP-001,2026-10-14 08:00:00,OTHER_DEVICE,IN
```

`name` and `employee` are nonempty strings. Names must be unique. `time` must retain
the database's naive site-local ISO wall-clock value, including microseconds when
stored. `device_id` may be empty or any device; `log_type` is `IN`, `OUT` or empty.
No attendance/skip/docstatus filter is permitted. Export every Checkin in the full
coverage range for every employee and device, even if it was created manually.
For example, an authorized administrator can use an unpaginated read-only query:

```python
checkins = frappe.get_all(
    "Employee Checkin",
    filters=[["time", ">=", coverage_start], ["time", "<=", coverage_end]],
    fields=["name", "employee", "time", "device_id", "log_type"],
    order_by="name", limit_page_length=0
)
# Serialize datetime with ISO local wall-clock precision, None device/log type as ''.
# Verify len(checkins) against an independent count in the SAME consistent snapshot.
```

If precision is seconds, coverage must start at the whole second at or before the
requested start. An invalid/aware/ambiguous Checkin timestamp blocks missing claims
for that employee, even if its intended date is unknown. Malformed CSV structure,
duplicate names or unsupported log types fail the entire export instead of silently
dropping rows. Do not open and resave exports in Excel before hashing.

### Export manifest JSON

All booleans below must be literal `true`; they attest checks performed externally.
The CLI can verify structure and hashes, but cannot prove export completeness or
the truth of an administrator attestation without live access.

```json
{
  "schema": "frappe-offline-reconciliation-v1",
  "exported_at": "2026-10-16T00:00:00+03:00",
  "site_timezone": "Asia/Riyadh",
  "coverage_start": "2026-10-11 00:00:00",
  "coverage_end": "2026-10-15 23:59:59.999999",
  "exports_complete": true,
  "consistent_snapshot": true,
  "checkins_all_devices": true,
  "checkins_all_employees": true,
  "mapping_all_statuses": true,
  "server_semantics_verified": true,
  "no_identity_mutating_hooks": true,
  "server_source_reference": "REPLACE with reviewed deployed source commit/hash",
  "employee_field": "attendance_device_id",
  "mapping_resolution": "frappe.db.get_values",
  "duplicate_key": "employee_time",
  "timestamp_precision": "microseconds",
  "device_policies": {
    "FP2_DEVICE_08": {"punch_direction": "AUTO", "in_codes": [0,4], "out_codes": [1,5]},
    "FP2_DEVICE_11": {"punch_direction": "AUTO", "in_codes": [0,4], "out_codes": [1,5]}
  },
  "employee_mappings": {"file": "employee-mappings.json", "sha256": "REPLACE", "rows": 2},
  "employee_checkins": {"file": "employee-checkins.csv", "sha256": "REPLACE", "rows": 1}
}
```

This is a schema example, not a production policy. Set `duplicate_key` to
`employee_time` or `employee_time_log_type` ONLY after source verification. Set
`timestamp_precision` to `seconds` (truncation, never rounding) or `microseconds`.
Set directions/codes from the actual non-secret production settings. The CLI does
not read `local_config.py`. Mapping `rows` counts lookup rows, not employee matches;
Checkin `rows` excludes the CSV header. Filenames must be basenames beside the
manifest. Use the checked-in synthetic export manifest as a complete format example.

## Operator command for a future separately authorized offline run

No production run is authorized by the implementation task. After separate approval,
work from protected, hash-verified copies; do not point at the original production
evidence directory. There is no requirement to stop the production service for an
offline run. This utility neither queries nor controls SCM or device sessions.

Use Python 3.10+ and a reviewed diagnostic-only `scripts/` bundle containing
`reconcile_attendance.py`, `attendance_reconciliation.py` and
`capture_history_safety.py`. No SDK, Frappe package, credentials or HTTP library is
required. On Windows use a dedicated existing local NTFS reports directory outside
the repository/production tree and input directories. Disable ACL inheritance and
grant only the authorized reconciliation account, administrators and SYSTEM;
review custom group membership yourself. The utility reads ACLs and requires at
least 256 MiB free, but never changes permissions. Protect inputs and working
copies to the same standard. Other platforms fail the live ACL check closed.

Replace all paths and both trusted hash placeholders before running:

```powershell
$PythonExe = 'C:\protected\attendance-reconciliation\python\python.exe'
$Tool = 'C:\protected\attendance-reconciliation\scripts\reconcile_attendance.py'
$CaptureSummary = 'C:\protected\attendance-reconciliation\inputs\captures\capture_summary_APPROVED.json'
$ExportManifest = 'C:\protected\attendance-reconciliation\inputs\exports\export-manifest.json'
$Reports = 'C:\protected\attendance-reconciliation\reports'
$CaptureHash = '<independently recorded original capture summary SHA-256>'
$ExportHash = '<approved export manifest SHA-256>'
& $PythonExe -B $Tool --capture-summary $CaptureSummary --capture-summary-sha256 $CaptureHash --exports-manifest $ExportManifest --exports-manifest-sha256 $ExportHash --output $Reports --start '2026-10-11T00:00:00' --end '2026-10-15T23:59:59.999999' --site-timezone 'Asia/Riyadh'
if ($LASTEXITCODE -ne 0) { throw 'Reconciliation failed; do not use partial reports' }
Get-ChildItem -LiteralPath $Reports -File | Get-FileHash -Algorithm SHA256
```

Defaults are the same inclusive October 11–15 range and Asia/Riyadh timezone. There
is no capture, delivery, force, service-control or production-config argument.
The CLI always performs offline comparison; it has no live mode.

## Reports, classification and review

Each run publishes a unique `reconciliation_details_<UTC timestamp>_<UUID>.csv` and
`reconciliation_summary_<UTC timestamp>_<UUID>.json`. Writes use exclusive temporary
files, flush/fsync, and no-overwrite finalization. The summary is published last and
contains the CSV hash plus input hashes; stdout supplies the final summary hash.
Exit 0 means comparison/report persistence completed, not that every row is resolved.

| Classification | Meaning |
| --- | --- |
| `matched` | Exactly one Checkin has the verified destination identity, regardless of device. |
| `missing` | One active mapped employee, valid complete source, no destination identity in the attested complete export. Review candidate only. |
| `unmatched_employee` | Authoritative lookup explicitly returned no employee. |
| `ambiguous` | Multiple/unexported/inactive mappings, invalid/incomplete evidence, uncertain timestamp, conflicting Checkins, or precision collision. |
| `duplicate_source` | Multiple usable source punches have the same destination identity. Every member is marked; no arbitrary winner is selected. |

Duplicate-source detection deliberately uses the verified destination key, so it
catches repeated reads, cross-device collisions, and changed/reused UIDs. It is
broader than identical raw-row detection. `destination_match` retains each member's
matched/missing observation. Distinct fractional source timestamps collapsing to
one destination second remain ambiguous. With a log-type-aware server, an existing
opposite-type Checkin at the same time is conservatively ambiguous, not missing.

The detail CSV includes device ID, original zero-based source index, source hash,
date/range status, user ID, diagnostic UID, source/comparison time, mapped employee,
expected log type, category/reason, matching Checkin names/device IDs and duplicate
group size. It contains sensitive identifiers; restrict access. Spreadsheet-formula
cells are prefixed with an apostrophe; import identifier columns as text to preserve
leading zeros. Original exact evidence remains unchanged in the source JSON.

Valid records outside the selected range are excluded from the detail CSV and
counted separately. Invalid/incomplete/unverifiable records are retained as ambiguous
even when their normalized timestamp appears outside the range; it cannot safely
be trusted for exclusion. Such rows use `range_status=uncertain`; missing timestamps
use day `unknown`. Therefore totals can exceed the valid in-range punch count.

JSON/stdout contain counts, dates, hashes, fixed device IDs/policies and generated
report basenames only, never employee identifiers, Checkin names, input paths or raw
error messages. Totals partition every detail row across the five categories, with
per-device and per-day breakdowns. `source_records` covers both complete snapshots;
`source_records = totals.records + excluded_valid_out_of_range`. Reports explicitly
set `recovery_authorized=false` and `all_missing_require_review=true`.

Stop on hash/schema failures, incomplete exports, unsupported server semantics or
unresolved ambiguity. Preserve failed `.part` files and any orphan CSV under the
same restricted permissions; absence of a completed summary means failure. Fix
inputs/permissions/disk issues and run again with a new unique report set. Never
edit original captures or promote reports into device retry dumps, ledger imports
or production success logs. There is no data rollback because no source/system data
is mutated. Retention and deletion require the incident owner's explicit policy.

## Synthetic example and development verification

`examples/reconciliation/` contains only synthetic captures written by the actual
published capture writer with fake terminals, authoritative lookup examples,
Checkins from another device, trusted test hashes, and expected CSV/JSON outputs.
Expected result: 9 source rows, 8 detail rows, 1 excluded valid row; matched 1,
missing 1, unmatched employee 1, ambiguous 3, duplicate source 2. These are unrelated
to production counts. The example contract is explicitly marked synthetic.

To regenerate into a NEW directory during development:

```powershell
py -B tests\generate_reconciliation_example.py C:\protected\NEW-synthetic-example
py -m unittest discover -s tests -p test_attendance_reconciliation.py -q
py -m unittest discover -s tests -q
```

Tests use fake devices, synthetic files, temporary directories and mocked ACLs only.
A fresh-process import guard runs a complete offline comparison while rejecting
engine, config, SDK, HTTP, socket and SQLite imports. Real Windows temporary-file
no-overwrite publication is tested; production ACLs and operator export attestations
are not live-proven. Do not confuse successful tests with reconciliation of the
81,138 production punches. Final verified test counts are in the implementation
report/commit notes.

Development verification on October 15, 2026: **185 tests passed** (129 existing and
56 reconciliation tests) in 24.272 seconds. All **27 Python files** syntax-compiled
without importing configuration; both PowerShell blocks above parsed without
execution. The committed synthetic example reproduced its expected CSV byte-for-byte
and summary counts/hashes. No production evidence, devices, Frappe or service was
accessed by these checks.
