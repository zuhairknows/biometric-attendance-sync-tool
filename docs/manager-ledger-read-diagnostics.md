# Manager ledger-read diagnosis and ordinary-user startup

## Finding and scope

The production root cause is **not established** by the reported symptoms. The old
message hid every exception from this path:

`POST /refresh -> State.start -> Controller.dashboard -> Ledger(readonly=True)`
`-> schema validation -> BEGIN -> full integrity/foreign-key checks -> counts -> display`

The caught exception was returned as a generic dashboard error; it never escaped
to the manager console. SQLite quick_check does not validate the expected ledger
schema, foreign-key relationships or diagnostic timestamp rendering. Administrator
membership also does not establish the actual process token's file/sidecar access.

The manager uses mode=ro, query_only and the existing ten-second busy timeout. It
does not switch journal mode, checkpoint WAL, use immutable mode, repair the source
or take the service writer mutex. No new retry/polling loop is added. Ordinary
rollback-journal and WAL writers are covered by synthetic Windows tests. An
exclusive writer can still exceed SQLite's wait and correctly report Unavailable.

Confirmed development defect: health/counts could be published before formatting
all device diagnostics succeeded. A later rendering error left partial results
marked OK. Publication now occurs only after the complete read/format operation.
A synthetic invalid device-delivery epoch reproduces the defect, but is **not**
claimed as production evidence or a reason to modify production values.

## Read the diagnostic, do not bypass the check

The refresh route supplies its approved startup profile to the existing private
diagnostic writer. Default location is the profile directory; an existing approved
diagnostics_directory can select another private location. The directory must be
outside service/input directories and pass existing output/ACL checks. No directory
or ACL is created or repaired by refresh. Direct Controller.dashboard callers can
pass values=profile to enable the same logging.

Each manual refresh writes monitor-<random-reference>.jsonl with only reference,
stage, code and event. No paths, credentials, employee/device identifiers, attendance
timestamps, records, exception text or traces are logged. Browser failures show the
fixed stage/code and reference. If logging fails, a separate safe warning appears;
the actual ledger failure is still shown. There is no public/console/temp fallback.

| Stage/code | What it establishes / next read-only check |
| --- | --- |
| monitor_path | Configured path was rejected; check the private profile's absolute path. |
| monitor_open_schema / sqlite_cantopen | SQLite could not open/read the database; check existence and effective database/sidecar access under the actual manager account. It does not distinguish missing file from ACL/other access failures. |
| any stage / sqlite_busy or sqlite_locked | SQLite reported lock contention; retry manually after startup/active work. Do not stop the service or change journal mode. |
| monitor_open_schema / source_schema_* | The source failed exact schema/version checks; independently compare deployed versions/schema. Do not migrate or weaken validation. |
| monitor_integrity / source_integrity_failed or source_foreign_keys_failed | Full integrity/foreign-key checks failed, even if quick_check passed. Preserve the source and approved backup; escalate for separate review. |
| any stage / sqlite_ioerr, sqlite_readonly, sqlite_corrupt or sqlite_notadb | SQLite returned that error category. Obtain read-only account/storage/journal evidence; the code alone does not establish its underlying cause. |
| monitor_counts | Failure while reading ledger/device diagnostic queries. |
| monitor_display / diagnostic_value_invalid | Stored diagnostic values could not be rendered. Health stays Unavailable; do not edit source values as a workaround. |

Collect only the reference/stage/code and running manager commit/version for review.
Record Python/SQLite versions using that manager's interpreter, without opening
the ledger or importing production configuration. First set $manager to the
approved checkout path of the running manager:

```powershell
& "$manager\.venv\Scripts\python.exe" -I -S -B -c "import sys,sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)"
```

Do not send raw exception traces, configuration files or attendance records. The
new diagnosis must be observed on IT-SUPPORT after separate deployment approval
before identifying the production root cause.

## Safe rollout and rollback (instructions only)

1. Review the local fix and tests, then transfer its exact approved source to a
   fresh isolated manager checkout/environment. Do not update the service checkout
   C:\GitHub\biometric-attendance-sync-tool or either existing manager installation.
2. Under the intended ordinary user, verify existing approved access: ledger and
   required existing SQLite sidecars readable, SCM service status queryable,
   private profile readable, private diagnostics directory writable. No ledger-write
   grant is required by the app. SQLite may require directory/sidecar access for
   WAL/recovery; if the existing permissions do not suffice, stop for a separate
   review. Do not grant broad permissions, run a repair or elevate to hide failure.
   Avoid giving a new account access to credential-bearing local_config.py just
   for Devices: use an approved credential-free literal devices metadata file if
   necessary. Preparing that profile/ACL is a separate operator action.
3. Record the running service State/ProcessId with Get-CimInstance Win32_Service
   -Filter "Name='ERPNextBiometricPushService'". Do not restart/control it.
4. If port 8765 is occupied, identify the existing manager and close only its own
   console with Ctrl+C. Do not kill an unidentified process or delete old folders.
5. Start the isolated corrected manager with its existing approved private profile:

   ```powershell
   $manager = 'C:\GitHub\biometric-sync-manager-monitoring-fix'
   $profile = Read-Host 'Existing approved private monitoring profile path'
   & "$manager\.venv\Scripts\python.exe" -B "$manager\scripts\biometric_manager.py" --monitor-only --profile $profile --no-browser
   ```

6. Refresh Dashboard and Devices once. Check the fixed diagnostic on failure and
   the private monitor log. Confirm monitoring-only restrictions and unchanged
   running service PID/state; healthy counts may change because synchronization
   continues. Never execute investigation/recovery as part of this acceptance.
7. Rollback: Ctrl+C stops only the corrected manager; relaunch the previous manager.
   No ledger restore, service restart, configuration change or hold reset is needed.

## Start after restart without elevation

After manual ordinary-user acceptance, create a shortcut in that user's
shell:startup folder. Target the isolated manager's Start Biometric Manager.bat,
set its working directory to that checkout, and use arguments:

`--monitor-only --profile "<existing approved private profile>" --no-browser`

Leave Run as administrator **unchecked**. This starts the manager at **user logon**,
not before logon; the Windows synchronization service remains independently active.
The manager starts idle: no background refresh, API/device connection or recovery
dispatch is added. Refresh manually once the PC/service is ready. Duplicate
instances fail on the existing occupied port. If ordinary-user access is denied,
report that operational prerequisite; do not create an elevated task/service or
change production permissions as part of this fix. No startup entry was installed
during development.

## Synthetic validation scope

tests/test_manager_monitoring.py exercises real Windows SQLite connections with
reserved/exclusive rollback-journal locks, a committing writer and an active WAL
writer. It covers missing-at-startup/manual recovery, quick_check passing with an
invalid schema or foreign-key orphan, diagnostic privacy/log-write failure and
atomic rejection of malformed display values. tests/test_monitor_only.py covers
safe messages on Dashboard and Devices, existing CSRF/Host/route restrictions and
monitor-only demo logging. These are synthetic tests, not production account,
SCM startup or actual IT-SUPPORT failure evidence.

Final relevant regression run: **564 tests passed in 95.327 seconds**, no skips,
on Windows with Python 3.11.9 / SQLite 3.45.1. It includes Phase 1 source pins and
all existing manager, investigation, offline, recovery and service safety tests.
The partial-health regression failed before the fix. git diff --check passed;
Phase 1 modules are unchanged from b729821. No production operation was performed.
