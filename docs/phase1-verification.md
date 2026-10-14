# Phase 1 development verification — October 14, 2026

Branch: `feature/phase1-durable-sync`, based directly on verified service commit
`a23b8112fd1b87f3d2bb10f798b45e150aadcd7a` after the user's authorization to proceed.
See [the full assessment and operator guide](phase1-durable-sync.md) for the missing
baseline branch discrepancy, schema, all changed files, cutover and rollback.

## Initial development results (before release review)

* Baseline suite: **27/27 passed** before edits; retained unchanged.
* Final combined suite: **74/74 passed** (27 existing + 47 new), 25.585 seconds.
  Command: `py -m unittest discover -s tests -v`.
* Syntax compilation: **16 Python source files passed**, using `compile()` on
  tracked/new source bytes without importing config, contacting APIs, or creating
  additional `.pyc` files. The template also parsed successfully with `ast`.
* Development host: Windows, Python **3.14.3**, SQLite **3.50.4**.
* `git diff --check`: passed (only Git's routine LF/CRLF conversion notices).
* Approved template AST comparison: all previously approved assignments unchanged;
  12 devices, all deletion flags false; only durable options added and disabled.
* Original service wrapper, base service, requirements, installer and both existing
  test files have no diff from the baseline. Stable local branch remains at
  `93589c1362d596291fa7e30a003582dbd568d99d`.
* Live remote ref verification: remote `main` contains the authorized template
  commit `4858432bf424a08bd437cf7d55027ea249712bc1`; Phase 1 branch is not published.
* Credential review: new database/error code retains fixed safe messages and
  whitelisted punch evidence; headers exist only inside HTTP client memory.
  No real `local_config.py` content was read, edited, staged or copied into tests.

The first combined test run hit sandbox restrictions on Windows temporary folders.
The suite was rerun with approved filesystem access. It found a backup connection
that needed explicit close and a Windows short-path/long-path assertion mismatch;
both were corrected before the successful run. Tests remain completely offline.

## Required acceptance coverage

Tests are in `tests/test_durable_sync.py`; names below omit the `test_` prefix.
Some tests consolidate several related acceptance cases.

| # | Required case | Passing test / evidence |
| --- | --- | --- |
| 1 | Persistence before delivery | `persisted_before_api_delivery_and_success_name` observes committed claim from a second read-only connection |
| 2 | Repeated ingestion dedup | `repeated_ingestion_and_large_history_do_not_post_again` |
| 3 | HTTP 200 synchronized + name | `persisted_before_api_delivery_and_success_name` |
| 4 | Confirmed HTTP 417 duplicate | `recognized_duplicate_is_complete_but_unrelated_417_is_rejected` |
| 5 | Other HTTP 417 not success | Same test; also duplicate phrase in missing employee ID and incompatible exception identity |
| 6 | HTTP 500/502/503/504 retry | `server_errors_retry_and_stop_destination_batch`, one subcase per status |
| 7 | Timeout durable retry | `network_timeouts_are_durable_and_sanitized` |
| 8 | HTTP 429 backoff | `429_retry_after_seconds_and_date`, valid seconds/date and malformed guidance |
| 9 | HTTP 401/403 cooldown | `authentication_cooldown_survives_restart`, both statuses |
| 10 | Unknown employee quarantine | `unmatched_and_inactive_employee_quarantine_does_not_halt_later_record` |
| 11 | Inactive employee quarantine | Same test |
| 12 | Malformed timestamp isolation | `malformed_timestamp_and_empty_user_do_not_stop_later_records` |
| 13 | Empty user ID isolation | Same test |
| 14 | Crash after local commit | `committed_punch_survives_process_exit`, real child process `os._exit(23)` |
| 15 | Remote commit before client confirmation | `crash_after_remote_commit_resolves_by_duplicate_after_lease`, mocked committed remote + interrupted client |
| 16 | Interrupted delivering recovery | Same test, not reclaimable before lease expiry, recoverable afterward |
| 17 | Restart resumes pending | `restart_resumes_pending_automatically`; service main integration resumes between polls |
| 18 | Log rotation independence | `log_rotation_cannot_change_delivery_checkpoint` |
| 19 | Large repeated source history | 2,000 punches posted once, reingested, zero additional posts |
| 20 | Import boundary | `import_start_boundary_is_inclusive_and_bad_config_fails_closed`, plus original boundary regression |
| 21 | Stable identity after IP/port change | `device_identity_survives_ip_and_port_changes` |
| 22 | Idempotent nondestructive legacy import | `legacy_migration_is_idempotent_nondestructive_and_date_filtered`; CLI explicit preview/apply |
| 23 | Corrupted dump retained | `corrupted_partial_legacy_dump_is_retained_and_reported` |
| 24 | SQLite failure not false ingestion success | `persistence_failure_rolls_back_ingestion_and_migration_metadata`; critical engine error and no API request |
| 25 | Safe Shift Type watermark | `watermark_never_passes_any_unresolved_or_unknown_timestamp` tests all six unresolved states; all-device/latest-scan tests |
| 26 | Existing payload fields unchanged | `http_payload_semantics_and_erpnext_version_selection`, plus original timeout/payload regression |
| 27 | Existing service entry point | `windows_entry_point_keeps_service_identity_and_calls_main`, mocked Windows imports, same service name/interpreter/call |
| 28 | Unsupported newer schema | `unsupported_schema_is_not_modified`, byte-for-byte database comparison |
| 29 | Interrupted transaction consistency | `interrupted_sqlite_transaction_rolls_back_on_process_death`, real child `os._exit(24)` plus integrity check |
| 30 | Bounded thousands-record queue | `thousands_pending_are_processed_in_bounded_batches`, 4,000 pending, exactly 13 delivered and indexed query plan |

Additional checks cover exact distinct-source identity, malformed timestamp message
redaction, source dump retention during normal polling, wrong-device migration,
rollback of import digest metadata, backup consistency, read-only diagnostics that
never create missing databases, bounded 20-entry attempt history with retained total
attempt count, new/retry and older/newer pending fairness, rate limiting, conservative unknown response classification,
terminal re-enable after failure, disabled attendance deletion, external absolute
database path validation, Shift Type destination cooldown, and safe Shift Type logs.

## What this does and does not prove

These tests prove the mocked integration and SQLite transaction/state behavior on
this development Windows host, including real process-death recovery of local
SQLite files. They do not prove device-model UID stability, raw pyzk decoder
behavior, actual Frappe response translations, service-account NTFS permissions,
Windows SCM lifecycle under the production account, or actual Auto Attendance.
Disk failure is simulated with SQLite abort triggers and mocked operational errors;
no disk was filled or production database corrupted. No live devices or Frappe
instance were contacted. Production configuration, service and records were
untouched. Only the earlier explicitly authorized template change was pushed to
remote main; durable code remains a reviewable local development branch.

## Final release-preparation verification

The independent review found and corrected schema-version-only validation, integer
truncation/invalid-evidence identity collisions, permissive short import dates,
truthy nonboolean activation, unpropagated service stop requests and legacy status
dependency during pending recovery. See [the release review](phase1-release-review.md).

Current suite: **92/92 passed** in **16.607 seconds**, including 18 additional tests
beyond the 74-test initial suite. The standalone loopback harness also passed all
**10 real HTTP fixture scenarios** and created 9 fake Checkins. **19 Python files**
compile successfully; the PowerShell inventory helper parses without execution and
its embedded config inspector is tested only on credential/syntax-error fixtures.
The original 27 regression tests remain unchanged.

The wrapper now passes an optional stop callback and waits on the existing stop
event; service name/interpreter hosting/base class/install model remain preserved.
SQLite schema remains 1; valid existing ledgers are supported without an upgrade,
and altered/unsupported schemas fail closed. In durable mode legacy status is only
a protected read-only floor; polling and automatic delivery no longer write it.

Hardware, real staging Frappe and Windows SCM tests are **NOT EXECUTED**. Production
PC verification commands and the [cutover runbook](phase1-cutover-runbook.md) were
prepared, not executed. Outcome: **A. Development verified, ready for controlled
staging**. Production readiness is not established. Phase 2 remains separately scoped.
