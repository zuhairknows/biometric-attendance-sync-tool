# Historical attendance recovery: offline preview only

This implementation cannot recover attendance. It has no network transport, insertion
payload, credentials, approval switch, or execution mode. It neither imports the running
integration nor reads its configuration, ledger, service state, device state, or retry dumps.
The existing October 14–15 reconciliation baseline remains unchanged.

## Architecture and boundaries

`scripts/attendance_recovery_preview.py` is the reusable local application layer.
`scripts/preview_attendance_recovery.py` is its command-line adapter. A future small
Windows application can call the same planner and display its reports. A future executor
must be separately implemented, reviewed, and authorized; adding a button must not bypass
the planner's checks.

The planner reuses `attendance_reconciliation` capture validation, export validation,
employee resolution and cross-device comparison without changing those components.
`capture_history_safety` supplies private-directory checks only; its service-query helper
is never called. `reconcile_attendance.publish` provides flush/fsync and publication without
overwriting a report. No capture SDK or production integration module is imported.

All inputs are separately prepared **offline files**. This program does not fetch current
Checkins. A fresh, independently counted, unfiltered database snapshot is required; connector
pagination/counts alone cannot attest completeness or atomicity. The existing exporter and
its verification requirements remain unchanged. Do not set their verification flags merely
to make this planner accept an export.

## Supported matching policy

Only this reviewed policy is supported: `attendance_device_id` lookup, all statuses,
duplicate identity `employee + time + log_type`, second precision, both device directions
literal `None`, client null log type becoming the stored empty string. No AUTO substitution
is made. Multiple mappings and non-Active employees remain ambiguous; device UID is never
used as global identity. All destination devices, including unrelated and blank device IDs,
participate. Existing matching records also represent previously recovered punches; their
origin cannot be inferred solely from their identity.

The CSV contract loses SQL NULL information. A same-snapshot hashed sidecar is mandatory.
SQL NULL and empty string are kept distinct. An unknown/null/other-type record at the same
employee/time is conservatively ambiguous, even where the committed duplicate rules might
permit a distinct blank record. A known blank match can still prove that identity exists.
Existing fractional destination timestamps make that employee uncertain. Fractional source
timestamps never become eligible through silent truncation; they require separate review.
Source timestamps remain in detail reports alongside comparison timestamps. This program
does not generate endpoint parameters or choose a new submitted log type.

## Required input files and trust

Use copies in a protected review-input directory, leaving original evidence and reports
unchanged. Supply independently trusted SHA-256 values for the capture summary, existing
export manifest and new review manifest. Calculating a hash of an untrusted file does not
approve its contents. Protect the input directory against concurrent changes throughout
the run. Capture file hashes are transitively verified through the pinned capture summary.

The existing `frappe-offline-reconciliation-v1` manifest and capture schemas are documented
in [the Phase 1 runbook](phase1-attendance-reconciliation.md). Unsupported, incomplete or
unattested exports fail closed before any report is published. There is no provisional
flag that relaxes those validators. With a valid export but missing additional review
claims, the planner produces a **blocked** preview with zero eligible records.

New review manifest schema: `attendance-recovery-review-v1`. Required fields:

| Field | Meaning |
| --- | --- |
| `capture_summary_sha256`, `exports_manifest_sha256` | Exact bindings to separately pinned inputs |
| `range_start`, `range_end` | Naive local ISO datetimes, inclusive; ordinarily October 14–15, 2026 |
| `site_timezone` | Exact export timezone; normally `Asia/Riyadh` |
| `baseline_details` | `{file, sha256}` for preserved reconciliation detail CSV, exact existing `DETAIL_FIELDS` header |
| `approved_source_records` | Nonempty list of `{device_id, source_sha256, source_order_index}`; selection for **review**, never approval to write |
| `log_type_storage` | `{file, sha256}` for sidecar below |
| `verified_at` | Offset-aware time of administrator review |
| `snapshot` | `{id, started_at, completed_at, independent_counts}` |
| `verification` | Per-claim `{verified: boolean, evidence_reference: string}` below |

Referenced filenames must be direct children of the review manifest directory; traversal
and symlink escapes are rejected. The baseline is hash-checked, and selected anchors must
be unique, previously classified `missing`, present in verified captures within the selected
range, and agree with original source identity columns. It is acceptable to select a small
subset. Current mappings and Checkins determine today's classification; a historical
`missing` label cannot override a current match or ambiguity.

`independent_counts` contains integer `checkins`, `lookup_keys` and `mapping_matches`.
The last count includes every matched employee across the required distinct User IDs,
including duplicate and inactive mappings. These counts must come from independent queries
within the same consistent snapshot, not from counting emitted rows. The program compares
them with emitted rows; the administrator supplies evidence that the queries were actually
independent, unfiltered and in the same snapshot. Empty lookup results must be explicit.

Snapshot start, completion, export timestamp, review timestamp and current clock must be
ordered. The snapshot must start after capture completion and no more than **15 minutes**
before preview time. There is no age override. A preview expires as evidence ages and is
never a promise that the destination will stay unchanged. A future executor needs another
destination check immediately before submission.

Sidecar schema:

```json
{
  "schema": "employee-checkin-log-type-storage-v1",
  "snapshot_id": "administrator-recorded-snapshot-reference",
  "states": [
    {"name": "SYNTHETIC-CHECKIN-1", "state": "empty"},
    {"name": "SYNTHETIC-CHECKIN-2", "state": "null"}
  ]
}
```

States are `empty`, `null`, `nonblank`, or `unknown`. Require exactly one state for every
exported Checkin name and agreement with CSV values. Derive states with SQL `IS NULL` and
empty-string comparison in the same authorized read-only snapshot; never guess from CSV.
No new SQL/export transport is introduced by this implementation.

Every verification claim requires literal `true` **and** a nonempty administrator evidence
reference before any missing record can be eligible for review:

- `deployed_sources_reviewed`
- `effective_hooks_and_overrides_reviewed`
- `null_request_defaults_to_empty_string`
- `sql_null_distinct_from_empty_string`
- `employee_lookup_semantics_verified`
- `timestamp_and_timezone_verified`
- `historical_device_settings_verified`
- `shift_and_custom_validations_reviewed`
- `unfiltered_repeatable_read_snapshot`
- `independent_counts_verified`

Evidence references should identify retained administrator-approved deployed source hashes,
versions, effective hooks/overrides/Server Scripts, applicable shifts/custom validations,
site timezone, historical client settings and snapshot/count query records. They must not
contain credentials. The pinned review artifact is a human trust boundary: the offline
program checks bindings and consistency, but cannot independently prove that an administrator's
claims are truthful. It never invents evidence or promotes pinned upstream source into
runtime verification. Re-review if code, hooks, settings or schema changes.

## Local execution procedure

Run only on the development/review PC with existing Python 3.9+ and the reviewed checkout.
No installation, production branch switch or service action is required. Use a new empty
local folder outside the repository and all evidence/report directories. Example preparation
below creates only a dedicated report folder, never changes an existing folder's ACL:

```powershell
$reportDir = Join-Path $env:USERPROFILE ('Documents\Attendance-Recovery-Preview-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $reportDir -ErrorAction Stop | Out-Null
$operatorSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $reportDir /inheritance:r /grant:r "*${operatorSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
if ($LASTEXITCODE -ne 0) { throw 'Private directory preparation failed' }

# Replace paths and three hashes with separately reviewed values. No credentials belong here.
py -B .\scripts\preview_attendance_recovery.py `
  --capture-summary 'C:\PrivateReviewInputs\capture-summary.json' `
  --capture-summary-sha256 '<independently-recorded-original-sha256>' `
  --exports-manifest 'C:\PrivateReviewInputs\export-manifest.json' `
  --exports-manifest-sha256 '<reviewed-fresh-export-manifest-sha256>' `
  --review-manifest 'C:\PrivateReviewInputs\recovery-review.json' `
  --review-manifest-sha256 '<independently-approved-review-manifest-sha256>' `
  --output $reportDir
```

The report directory must have inheritance disabled and no broad user/group access; the
program checks ACLs without changing them. Run under a trusted operator on a trusted PC.
Detailed CSV contains employee identifiers and requires private storage. Console output has
only fixed diagnostic codes, counts, hashes and generated report filenames. Generic exception
handling prevents private input values from appearing in errors. No credentials are needed.

Exit codes: **0** preview complete (still no recovery authorization); **2** blocked preview
published; **1** input/output validation failure, no successful summary; **130** interrupted.
On write failure a private partial/detail file can remain. A complete JSON summary with a
valid detail hash is the success marker. Repeated runs publish new files without replacement.
No report is written into the repository, original captures, inputs or production state.

## Reports and synthetic example

CSV adds `review_status`, `review_reason`, `in_review_scope` to existing reconciliation
details. Statuses: `eligible_for_review`, `already_matched`, `excluded`, `ambiguous`, `blocked`.
Existing reconciliation categories remain available, including unmatched employees and
duplicate-source records. All duplicate group members are excluded, including cross-device
duplicates; the planner never arbitrarily keeps the first source punch.

JSON includes aggregate/per-device/per-day counts, selected-scope counts, original comparison
statistics, input hashes, blockers and a deterministic review fingerprint binding all inputs
and computed detail rows. It always says `execution_supported: false`,
`recovery_authorized: false`, `approval_required: true`, `provisional: true`.
The fingerprint is a review identifier, not an executable batch or approval token.

See [synthetic examples](../examples/historical-recovery-preview/README.md). Those files are
deliberately synthetic and public; their fabricated test attestations must never be reused
for production. No production evidence or employee information is included.

## Future executor design — deliberately not implemented

Use a separate, explicitly authorized executor with its own write credential boundary.
Require approval of a fixed hash binding exact records, destination identity, reviewed
semantics, source evidence, expiry and maximum batch size. A changed batch requires new
approval. The present preview contains no request payload and cannot activate an executor.

1. Default future batches to small sizes (for example 10), with a configured upper bound.
   Recheck mappings, hooks/policy revision and destination completeness immediately before
   each batch. A snapshot alone cannot eliminate concurrent insert races; require proven
   server-side duplicate/idempotency enforcement or an approved operational concurrency guard.
2. Record each source anchor and intended identity in a private transactional checkpoint.
   States: pending, submitting, confirmed, rejected, uncertain. Flush intent before sending;
   preserve immutable source identifiers, attempts, sanitized result codes and destination
   record references. Never log credentials or full HTTP error bodies.
3. Confirm success through a fresh read of the returned record and expected identity.
   Treat timeout, disconnect and interruption during submission as **uncertain**, not failed.
   Recheck destination identity before retrying an uncertain outcome; if not conclusively
   resolved, stop for operator review. A local checkpoint alone cannot guarantee exactly-once
   delivery to Frappe.
4. Retry only verified retryable failures with bounded exponential backoff and attempt caps.
   Do not retry validation rejection or ambiguous identity. Existing identities become
   already matched. Unexpected server semantics stop the whole batch.
5. Stop safely between records. On resume, verify batch approval/hash/expiry and reconcile
   all submitting/uncertain records before sending anything. Post-recovery verification must
   independently check counts, identities, timestamps and stored log-type nullity and retain
   a per-record audit without changing the original evidence or baseline reports.

## Minimum authorization checklist and present blockers

- Obtain actual effective runtime hooks, method/controller overrides and Server Script
  evidence; pinned application commits alone do not verify runtime behavior.
- Resolve historical device configuration and applicable shift/custom validation uncertainty.
- Produce an authorized fresh, unfiltered consistent snapshot, independent counts, complete
  mappings and SQL NULL sidecar; connector-visible records are insufficient.
- Review all exclusions/ambiguities, source hashes and exact selected anchors. Keep unmatched
  employees and duplicate-source punches excluded pending separately authorized resolution.
- Separately implement, test and review the executor, concurrency safeguards, interruption,
  uncertain-outcome handling, audit storage and post-recovery verification.
- Obtain explicit operator approval of the final fixed, hashed batch and expiry immediately
  before any future write. This implementation cannot accept that approval or perform writes.

The current provisional 2,161 apparently missing punches are **not an authorized recovery
batch**. This development task does not rerun that comparison, certify runtime assumptions,
or change recovery readiness merely because the planner's synthetic tests pass.

## Validation and security assessment

Run `py -B -m unittest tests.test_attendance_recovery_preview tests.test_attendance_reconciliation -q`.
Final validation: **209 tests passed** on Python 3.14 across the preview, reconciliation,
capture safety, database export and server-side export suites. The focused preview and
reconciliation subset contains 86 tests, including saved-example replay and a check of the
entire local import chain for indirect clients or dynamic imports. Full relevant command:

```powershell
py -B -m unittest tests.test_attendance_recovery_preview tests.test_attendance_reconciliation tests.test_capture_device_history_readonly tests.test_frappe_attendance_export tests.test_frappe_server_export -q
```

CLI `--help` also passed. Final publication review added Git attributes preserving exact
synthetic JSON/CSV bytes so newline conversion cannot break pinned hashes on checkout.
The initial sandbox run
could not create temporary test directories; the same tests passed with approved temporary
directory access. Test output-directory ACL checks are injected; this does not constitute
production ACL, runtime-hook, server, device, or end-to-end recovery validation.
Tests use fake capture terminals and synthetic employees only. They cover hashes, source
selection, incomplete exports, missing runtime attestations, independent counts, freshness,
cross-device and repeated identities, SQL NULL versus blank, mapping ambiguity, inactivity,
timestamp precision, private output, sanitized failures and unavailable execution switches.

The CLI imports no HTTP/Frappe/device client and contains no database or submission path.
It writes only new local preview reports after private-directory validation. Shared storage
helpers can query directory ACLs with PowerShell, but service control is never used. Trust
depends on operator-approved hashes, accurate administrator attestations, private stable
inputs, local ACL integrity and clock accuracy. These are explicit limits of offline review.
