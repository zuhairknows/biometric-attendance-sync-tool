# Shift Type sync policy

`last_sync_of_checkin` tells ERPNext that every punch up to that time has arrived,
so Auto Attendance may process it. Durable mode computes it per mapping in
`Ledger.watermark_report()` and sends it from `durable_sync.update_shifts()`.

## What holds the date back

| Punch / device | Effect |
|---|---|
| `pending`, `delivering` | waits |
| `retry` caused by `authentication` or `rate_limited` | always waits (nothing is getting through) |
| other `retry` | waits for `SHIFT_SYNC_RETRY_GRACE_HOURS` (24) after capture |
| `unmatched_employee`, `rejected` (incl. `cutover_hold`), `invalid_record` | reported, not waited for |
| punch before `IMPORT_START_DATE` / cutover | ignored |
| punch dated more than `SHIFT_SYNC_FUTURE_TOLERANCE_MINUTES` (10) after the scan | ignored (device clock error) |
| device whose latest scan failed | waits for `SHIFT_SYNC_DEVICE_STALE_HOURS` (12), then excluded with a warning |
| device in `SHIFT_SYNC_IGNORED_DEVICES` | excluded |
| device never scanned successfully | waits |

The date never moves backwards, is rounded down to the second and is saved
locally only after ERPNext answers 200.

Before this policy, any unresolved punch blocked forever. On the production
ledger (2026-11-21) held history and junk records dated 2000-01-01 pinned every
shift at 2025-05-26 14:56:56, so Auto Attendance never ran.

## Observing it

* `logs.log`: one `{"event": "shift_watermark", ...}` line per mapping per cycle
  with each device's status, `blocking` and `not_waited` counts.
* `error.log`: `shift_watermark_devices_excluded` when a device is stale or ignored.
* Manager dashboard: **Shift sync** panel (estimate from the configured mapping;
  it does not apply the start boundary).

## Uncertain sends: verified-absence resend

A send whose result is unknown (timeout, 5xx, or a duplicate reply that cannot be
matched) is never POSTed blindly again. Without confirmation reads it stays in
`retry`/`confirmation_pending` forever; on the production ledger (2026-11-21)
that held 21,188 punches, of which 66 from real employees were missing in ERPNext
(8 of them a shift's first or last punch).

With `DURABLE_CONFIRM_READS_VERIFIED`, a non-empty `DURABLE_SERVER_REVIEW_REFERENCE`
and `DURABLE_RESEND_VERIFIED_MISSING`, each uncertain punch is looked up:

1. `Employee` by `attendance_device_id`: exactly one, Active.
2. `Employee Checkin` for that employee within +/-2 seconds.

| Lookup result | Action |
|---|---|
| a check-in is found | recorded as `duplicate` / `destination_confirmed`; no POST |
| both reads succeed and find none, last attempt older than `DURABLE_RESEND_SETTLE_SECONDS` (600) | POSTed once more (`durable_resend_verified_absent` in `logs.log`) |
| absent but the last attempt is more recent | waits (a timed-out POST may still commit) |
| any read fails, is forbidden, or is ambiguous | waits, as before |

A resend that is uncertain again goes through the same check on a later cycle.
The API user needs read access to Employee and Employee Checkin. Enabling it on
a ledger with a large `confirmation_pending` backlog spends two paced reads per
punch until the backlog is resolved.

## Updating an existing installation

`deployment/Update-ShiftSyncPolicy.ps1` replaces `durable_sync.py`,
`sync_ledger.py` and `durable_delivery.py` in place, each only when the installed
copy is a reviewed version (files already updated are left alone). It runs as a
dry run unless `-Apply` is given, backs up the files and `local_config.py`, sets
settings in place (or appends them), and restores everything if the service does
not return to Running:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\Update-ShiftSyncPolicy.ps1 -IgnoreDevices FP1_DEVICE_06 -RetryGraceHours 2
powershell -ExecutionPolicy Bypass -File .\Update-ShiftSyncPolicy.ps1 -IgnoreDevices FP1_DEVICE_06 -RetryGraceHours 2 -Apply -WaitMinutes 15
powershell -ExecutionPolicy Bypass -File .\Update-ShiftSyncPolicy.ps1 -ResendMissing -Apply -WaitMinutes 15
~~~

Once the date moves, ERPNext's hourly Auto Attendance creates attendance from
each shift's *Process Attendance After* onwards.
