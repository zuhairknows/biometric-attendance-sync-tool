# Phase 1: durable capture and delivery recovery

Development only. No deployment or Windows service operation was performed.
`DURABLE_SYNC_ENABLED = False` is intentional until the controlled cutover below.

## Verified starting point and Git history

The requested `stable-biometric-core-2026-09-12-service` branch did not exist locally
or in the live remote listing. The expected commit **a23b8112fd1b87f3d2bb10f798b45e150aadcd7a**
was verified locally, at the `biometric-service-stable-2026-10-01` tag and at remote
`main` before publishing the template. After the user authorized creating the new
branch and continuing, `feature/phase1-durable-sync` was created directly from that
exact commit. No stable branch was modified or substituted with archived local main.

The existing template edit was committed to local `main` as `974a565`, then applied
on the verified remote main ancestry and pushed as **4858432bf424a08bd437cf7d55027ea249712bc1**.
This preserved its exact content without publishing 41 unrelated local main commits.
Local main and remote main remain divergent; do not force-push local main.
Phase 1 commits remain local and are not pushed, merged, or tagged.

## Assessment of the actual baseline

| Area | Baseline behavior | Phase 1 after cutover |
| --- | --- | --- |
| Fetch | Existing `ZK` connector disables terminal, calls `get_attendance`, enables and disconnects | Same sequential connector, with re-enable also attempted in `finally` on failure |
| History | Sort timestamp, UID, user ID; resume after last user/timestamp in success log, or inclusive `IMPORT_START_DATE` | Validate independently, preserve inclusive boundary; ledger identity replaces success-log checkpoint |
| Dump | Write/overwrite `<device_id>_last_fetch_dump.json` before API work; automatically replay and delete on successful processing | No writing, replay, truncation, or deletion of dumps; explicit offline migration only |
| Delivery | Loop through records following log checkpoint; abort device on unfamiliar error | Claim committed pending/due retry rows in bounded batches; permanent errors do not stop later records |
| Checkin | POST `hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field` for version >13, otherwise `erpnext` | Same endpoint selection, six payload fields, request timeout and local timestamp semantics |
| Duplicate | HTTP 417 plus exact known Checkin duplicate message; write successful checkpoint | Same specific recognition; completed `duplicate` state, never every 417 |
| Unmatched/inactive | Default allowlist `[1,2,3]` continues after these errors without successful delivery | Durable `unmatched_employee` / `rejected`; not successful and not automatically retried |
| Status | PickleDB `status.json`: pull/push, lift-off, mission-accomplished, Shift Type timestamps | PickleDB remains only for poll scheduling and legacy compatibility; authoritative capture/delivery/watermarks are SQLite |
| Shift Type | Earliest mapped-device pull timestamp if each push timestamp is nonempty | Latest successful scan must exist for every mapped device, capped before every unresolved punch |
| Windows | `ERPNextBiometricPushService`, direct Python interpreter hosting, wrapper calls `main()` then sleeps 15 seconds | Service name, hosting, entry point and installation remain unchanged |

The baseline's allowlisted permanent failures are especially dangerous: a later
successful punch moves the log checkpoint beyond an earlier unresolved punch.
The device can also get a successful push timestamp, permitting Auto Attendance
to advance past missing check-ins. Durable mode retains each record and blocks
watermark advancement past it, irrespective of `allowed_exceptions`.

README, `install.py`, `requirements.txt`, `SMWinservice.py`, `erpnext_sync_win.py`,
the template, both existing test files and SpeedFace patch script were inspected.
There is no new dependency, framework, installer, Windows service, or device decoder.
The existing raw pyzk decoder can fail a whole fetch before returning Python
records; per-record quarantine starts with the records that the connector returns.

## Architecture and files

```
existing sequential ZK connector
    -> normalize each source record
    -> commit records + device fetch metadata to SQLite
    -> claim one committed row
    -> POST unchanged Frappe payload
    -> commit outcome + bounded attempt history
    -> compute conservative Shift Type watermark
```

| File | Responsibility |
| --- | --- |
| `sync_ledger.py` (new) | SQL, transactions, schema validation, claims/leases, diagnostics and watermarks |
| `punch_normalization.py` (new) | Independent source validation, payload mapping and identity |
| `durable_delivery.py` (new) | HTTP client, safe classification, backoff, rate limiting and bounded queue processing |
| `durable_sync.py` (new) | Existing connector orchestration, explicit external database configuration and Shift Type safety |
| `legacy_import.py` (new) | Explicit transactional nondestructive dump import and preview |
| `sync_cli.py` (new) | Aggregate read-only status, online backup, legacy preview/apply |
| `erpnext_sync.py` (changed) | Opt-in routing from existing `main`, durable connector safeguards and safe logs |
| `local_config.py.template` (changed) | Approved FP1/FP2 inventory plus disabled-by-default durable settings |
| `.gitignore` (changed) | Exclude databases, journals, backups and legacy dumps |
| `tests/test_durable_sync.py` (new) | Offline acceptance, integration, process-death, migration and Windows wrapper tests |
| `README.md` (changed) | Link to cutover and operational guidance |
| `docs/phase1-durable-sync.md` (new) | Assessment, schema, operation, limits and rollback |
| `docs/phase1-verification.md` (new) | Acceptance coverage and development verification results |

## SQLite schema and durability

Schema version **1** is stored in `schema_version`. Initialization is transactional.
An unknown database or unsupported version is refused without rebuilding,
overwriting or upgrading it. Future changes require an explicit reviewed migration.

* `punches`: immutable integer ID and unique SHA-256 source identity; device ID,
  original user ID, UID, source timestamp, punch/status, whitelisted source evidence,
  normalized six-field payload, state, attempts, retry/lease times, HTTP status,
  sanitized error category/message, confirmed Checkin name and capture/update times.
* `delivery_attempts`: outcome, timestamp, HTTP status, safe category/details;
  newest **20 attempts per punch** retained. Total attempt count is never reset.
* `device_state`: latest successful persisted fetch, latest observed timestamp,
  last delivery, last scan attempt and whether the latest scan committed successfully.
* `destination_state`: persistent destination cooldown and queue fairness toggles.
* `shift_state`: locally confirmed Shift Type watermarks, separate from log files.
* `legacy_imports`: source absolute path, SHA-256 digest, import time and count report.

Indexes cover due state/retry time/ID, device/state/time, and per-punch attempts.
Due work is queried one row at a time, not loaded as a full backlog. New and retry
work alternate when both are due. Oldest/newest pending rows also alternate because
an outage leaves most untouched records pending, not retrying. This lets fresh
captures progress while still draining older pending records. The due index can
be scanned forward/backward; each tick delivers at most the configured batch.
The connector still loads device history as before. Ingestion uses one transaction
per fetch and a generator of normalized rows, avoiding a second normalized copy.

SQLite uses parameterized values, 10-second busy timeout, foreign keys,
`synchronous=FULL` and the rollback journal (`journal_mode=DELETE`). WAL is not
needed for this single-worker design. A failed transaction rolls back punches and
success metadata together; a failed scan invalidates the device's previous fetch
eligibility. Disk-full/corruption raises a critical cycle error, never a healthy state.
Use a local NTFS volume, not a network share, removable disk or synced cloud folder.

The database contains employee device identifiers and attendance: provision NTFS
permissions for the service identity and authorized administrators only. Provision
the parent directory before cutover; Python does not silently adjust account ACLs.
No API keys/secrets, device passwords, headers, HTML bodies or raw traceback strings
are written to ledger rows. Unknown errors store fixed messages and HTTP categories;
use protected Frappe server logs for their detailed investigation. Database files,
journals, dumps, backups and logs are ignored by Git.

## Identity and payload

Identity is a SHA-256 digest of a canonical tuple:

```
(permanent device_id, original user_id, normalized exact timestamp,
 punch code, device status, device UID when available)
```

IP, port, credentials, geolocation and Frappe mapping settings are not identity.
Changing IP/port does not enqueue another punch. Distinct UIDs, users, timestamps,
codes or statuses remain distinct; timestamps are not rounded or fuzzily compared.
UID is not trusted alone: reuse with different timestamp/user/code is a new punch.
If a model changes UID on repeated reads, the ledger conservatively retains another
source identity and relies on Frappe's duplicate response for safe delivery.
If a device provides no distinguishing attributes for two literally identical
records, their physical distinctness cannot be inferred; identical tuples collapse.
This source limitation requires model-specific proof before production cutover.
Frappe itself may reject distinct source events with the same employee/timestamp;
the source events remain separate ledger rows marked as confirmed duplicates.

Invalid records have a separate hash of device ID and whitelisted source evidence.
Malformed timestamps, empty/oversized IDs, invalid codes/direction/geolocation and
timezone-aware timestamps become `invalid_record`. Other records continue.
ZKTeco/local legacy epoch timestamps retain the baseline local wall-clock behavior;
run migration under the original Windows timezone. A malformed `IMPORT_START_DATE`
fails closed rather than importing all history.

Payload preserves `employee_field_value`, `timestamp`, `device_id`, `log_type`,
`latitude`, `longitude`. `AUTO` uses the existing IN/OUT code configuration.
Payload is frozen at capture: subsequent config edits do not silently rewrite a
queued historical punch. Example request (offline illustration):

```json
{"employee_field_value":"100","timestamp":"2026-10-13 08:00:00","device_id":"FP1_DEVICE_01","log_type":null,"latitude":null,"longitude":null}
```

Confirmed HTTP 200 response: `{"message":{"name":"CHECKIN-0001"}}`.

## State transitions and retry policy

| From | To | Trigger |
| --- | --- | --- |
| capture | `pending` | Valid eligible source committed locally |
| capture | `invalid_record` | Invalid source committed to quarantine |
| `pending` / due `retry` | `delivering` | Transactional claim, attempt increment and lease |
| `delivering` | `synced` | HTTP 200 with a nonempty Checkin document name |
| `delivering` | `duplicate` | HTTP 417 containing the known Checkin duplicate message |
| `delivering` | `unmatched_employee` | Known employee-mapping validation response |
| `delivering` | `rejected` | Inactive employee or persistent business validation |
| `delivering` | `retry` | Transient, authentication, unknown or unconfirmed response |
| expired `delivering` | `retry` | Worker restart/tick recovers expired lease |

Completed and quarantined rows are never automatically resent or deleted.
Default retry is 30 seconds times `2^(attempt-1)`, with positive jitter up to 20%,
capped at 3600 seconds. Valid numeric/date `Retry-After` can extend that delay.
There is no retry-count discard limit. Due retries resume on each existing service
tick, even between device polling cycles. A server creation followed by a lost
response/crash is retried after backoff/lease; a known duplicate finishes it safely.

* Connection/DNS/timeout: `retry`, fixed safe transport message.
* HTTP 408, 429 and 5xx: `retry`; stop destination requests for the calculated cooldown.
* HTTP 401/403: `retry`, at least 900-second destination cooldown by default.
* HTTP 400/417 with known missing mapping: `unmatched_employee`.
* HTTP 400/417 with known inactive employee: `rejected`.
* Other HTTP 400/417/422: `rejected`, never successful duplicate by status alone.
* Unfamiliar routing/response statuses or HTTP 200 without a document name:
  conservative `retry`; unknown statuses get the extended cooldown.

Any destination-wide failure stops the batch after one request and persists its
cooldown; captured punches are retained. Shift Type requests respect that cooldown,
and failed Shift Type requests also pause the destination. Defaults: 100 punches
per tick and 2 requests/second inside a batch; the 15-second service sleep adds
spacing between batches. Keep lease >= `2 * request timeout + 30` seconds.

## Shift Type policy

For every unchanged mapping, the candidate is the earliest mapped device's latest
successful **persisted** scan start. Using scan start avoids treating punches that
occur during the fetch as known complete. All mapped devices must have a successful
latest scan; missing/failed devices block the update. The candidate is capped to
one second before the earliest unresolved timestamp on any mapped device and
rounded down to whole seconds. A malformed/unknown timestamp blocks the mapping
entirely. Pending, delivering, retry, unmatched, inactive/rejected and invalid rows
all block progress past themselves. Fetching alone never proves delivery.

Successful deliveries and confirmed duplicates are the only completed states.
The legacy local shift timestamp is only a monotonic floor, not proof of delivery.
Previously confirmed SQLite watermarks prevent repeated writes. Failed remote
updates do not save a watermark. This does not retroactively undo a production
watermark already advanced by the old engine. Audit existing remote watermarks and
unresolved historical punches before cutover; any remote correction is separately
authorized operator work. No Shift Type mapping or configuration was changed by
runtime code.

Permanent mapping/business errors intentionally freeze advancement past their
timestamp. Fix the underlying master data, then arrange a separately reviewed
targeted release/requeue of those ledger rows; Phase 1 adds no manual historical
recovery interface or automatic mass requeue. Never mark them successful to unblock
Auto Attendance. SQLite records stay inspectable by authorized operators.

## Explicit legacy migration and controlled cutover

These are future operator instructions, **not actions executed during development**.
No first-start dump replay exists in durable mode. The CLI does not contact Frappe,
devices, or import the synchronization engine. Its trusted Python config is loaded
only by the explicitly requested migration command. Preview is the default.

1. Obtain separate deployment approval. Retain the baseline checkout/config and
   protected copies of status, rotating logs and every existing dump.
2. Arrange an approved maintenance window and stop the existing worker before
   migration/cutover; do not install another service or run two engines against the
   same device/ledger. Preserve the existing service identity and interpreter.
3. Provision an external database directory and service-account NTFS permissions.
   Confirm disk headroom, timezone, all 12 stable device IDs, reachable devices,
   `clear_from_device_on_fetch=False`, the original date boundary and shift mapping.
4. Audit existing remote Shift Type watermarks and dump coverage. Resolve partial
   or unknown-device dumps before declaring migration complete. Logs remain audit
   evidence; do not translate their last line into a successful SQLite checkpoint.
5. Preview every configured dump from a protected source directory:

   ```powershell
   py sync_cli.py --db 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3' import-legacy --config 'C:\approved\local_config.py' --directory 'C:\protected\legacy-dumps'
   ```

6. Review counts; `imported` is would-import in preview. `invalid` is a subset of
   imported/already-known rows; `excluded` is valid pre-boundary history; `failed`
   is a file-level failure. A nonzero exit means at least one source failed.
7. Once explicitly approved, repeat with `--apply`. Each file's rows, digest and
   import report commit together. Repeat is idempotent and never changes/deletes
   sources. Legacy numeric epochs and timestamp strings normalize through the same
   identity as live device records. Import does not mark a device freshly scanned.
   Malformed/partial JSON is retained and reported, never salvaged by guessing.
   Dumps larger than 64 MiB are refused intact for operator-assisted handling.
8. Run read-only status and verify imported/quarantined counts, then backup:

   ```powershell
   py sync_cli.py --db 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3' status
   py sync_cli.py --db 'C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3' backup --output 'C:\protected\phase1-before-cutover.sqlite3'
   ```

9. In the approved installation's existing `local_config.py`, set only reviewed
   durable options, including absolute `DURABLE_DB_PATH` and
   `DURABLE_SYNC_ENABLED=True`. This development task never edits that real file.
   Deploy reviewed source modules together; no service configuration change is
   required by Phase 1. Resume the existing service only with separate approval.
10. Observe captures, state counts, retry recovery and conservative watermarks.
    Keep dumps/logs and the pre-cutover backup. Do not delete evidence automatically.

## Diagnostics, backup and rollback

`status` opens an existing ledger with SQLite `mode=ro`: no schema initialization,
directory creation, credential loading or API requests. It reports schema version,
total/state counts, oldest pending time, per-device pending counts, last confirmed
delivery epoch and destination cooldown epoch. It prints no employee IDs or payloads.
Detailed source quarantine is available only through protected database inspection.
The engine logs aggregate health and safe punch ID/outcome diagnostics to existing
rotating logs. Corruption/storage failures produce a critical log category.

Use `backup` above for an online consistent SQLite backup plus integrity check;
the output must be a new absolute path and is never overwritten. Source connection
is read-only and both handles close. Protect backups like the original attendance
database. The rollback journal mode does not need WAL files; if making a manual
file copy, stop all writers gracefully first and retain any hot journal after an
unexpected shutdown. Never copy a changing database as a backup.

Rollback with separate operational approval: stop the existing worker, take a
fresh ledger backup, preserve all SQLite rows/dumps/logs, restore the verified
baseline source and previous config, then disable durable mode. **Do not simply
switch to legacy processing while unresolved SQLite punches exist.** The baseline
does not understand the ledger; export/recover that queue through separately
reviewed work before allowing legacy checkpoints to advance. Preserve the external
database for future recovery. Do not remove/reinstall the service or downgrade a
newer SQLite schema in place. No rollback command is run by this task.

## Production approval checklist and limits

* Review local Phase 1 commits and the offline verification report.
* Verify actual service account ACLs, Python/sqlite versions and installed pyzk
  SpeedFace patch on a staging host. This task does not patch dependencies.
* Exercise the unchanged Windows wrapper under SCM in staging: restart during
  delivery, storage failure, backup, graceful stop and recovery.
* With a nonproduction Frappe instance, verify actual duplicate/missing/inactive
  response formats, payload mapping, geolocation and Shift Type watermark behavior.
* Prove each device model's UID and wall-clock timestamp behavior. Keep stable IDs
  when changing IP/port; use the original timezone for legacy epoch import.
* Complete all dump migration reports; inspect unresolved/quarantined records and
  existing remote Auto Attendance watermarks before enabling.
* Run exactly one worker against this local database. Leases are crash recovery,
  not a supported multiworker/distributed locking protocol.
* Schedule protected backups, monitor disk size, pending age and quarantine growth.

Known limits: polling remains sequential; device connector returns full history;
ledger rows are retained indefinitely (only attempt history is bounded); imported
JSON is bounded at 64 MiB; no manual historical recovery/requeue UI, reconciliation,
or raw-device decoder overhaul. An invalid timezone or indistinguishable physical
source records need operator/model-specific investigation. Permanent errors block
Auto Attendance progress until separately reviewed correction. The legacy engine
remains available for controlled rollback and retains its old checkpoint behavior
when durable mode is disabled. Unit tests alone do not prove production readiness.

Phase 2 can consider concurrent polling, model-specific decoder/identity proof,
operational recovery tooling, retention and complete reconciliation after separate
scope approval. None is needed for Phase 1's sequential durable queue.
