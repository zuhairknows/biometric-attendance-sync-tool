# Cleaning up the sync PC (Phase 2 install, Windows 10)

The sync PC runs the manually installed Phase 2 service from
`C:\Program Files\FPFBiometricSyncPhase2`, with its ledger in
`C:\ProgramData\ERPNextBiometricPushService\sync.sqlite3`. Earlier installs left
about 15 GB of checkouts, backups and evidence on drive C:. The service still
writes its logs and `status.json` into one of them,
`C:\GitHub\biometric-attendance-sync-tool\logs`.

The portable installer needs Windows 11, so on this Windows 10 PC the clean
setup keeps the Phase 2 install and removes everything around it. When the PC
gets Windows 11, `docs/new-sync-pc.md` replaces it with a portable install.

End state: one service in `Program Files`, its ledger and logs in `ProgramData`,
the dashboard, and nothing in `C:\GitHub`. The leftovers are kept in a protected
archive on drive D:.

Do this only after the attendance rebuild, while no Frappe maintenance is running.

## 0. See what is there

`deployment/Get-SyncPcInventory.ps1` (elevated, read-only) writes a report to the
Desktop: services and the folders they run from, folders, ledgers, tasks, Python and
disk space. Run it before and after.

## 1. Update the service and move its logs

Copy into one folder on the sync PC, from `main`:
`deployment/Update-ShiftSyncPolicy.ps1`, `durable_sync.py`, `sync_ledger.py` and
`durable_delivery.py`. In an elevated Windows PowerShell in that folder, first the
dry run, then the same command with `-Apply`:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Update-ShiftSyncPolicy.ps1 -ClearIgnoredDevices -RecheckDays 60 -AddShifts Test -MoveLogsTo C:\ProgramData\ERPNextBiometricPushService-Logs
powershell -NoProfile -ExecutionPolicy Bypass -File .\Update-ShiftSyncPolicy.ps1 -ClearIgnoredDevices -RecheckDays 60 -AddShifts Test -MoveLogsTo C:\ProgramData\ERPNextBiometricPushService-Logs -Apply -WaitMinutes 15
~~~

This stops the service for under a minute and:

* installs the unmatched-card re-check (punches of cards enrolled before their
  Employee existed are sent once the Employee exists; 60 days covers the October
  backlog, lower it to 30 later with `-RecheckDays 30`);
* adds the Test shift to the shift mapping, so its *Last Sync of Checkin* moves;
* stops ignoring device 06 (`-ClearIgnoredDevices`); leave this out if a device is
  still offline;
* copies the logs and `status.json` to `C:\ProgramData\ERPNextBiometricPushService-Logs`
  (SYSTEM and Administrators only) and points `LOGS_DIRECTORY` there. The old log
  folder is left as it is.

It backs up the files and `local_config.py` first and restores them if the service
does not come back. The ledger is never touched. Afterwards: the dashboard shows
deliveries, new lines appear in the new `logs.log`, and within an hour the Test
shift's *Last Sync of Checkin* in ERPNext moves to today.

## 2. Archive the leftovers (next day)

When the service has run normally for a day, copy
`deployment/Invoke-SyncPcCleanup.ps1` to the sync PC and, elevated, run the dry run,
then `-Apply`:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-SyncPcCleanup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\Invoke-SyncPcCleanup.ps1 -Apply
~~~

It reads what is live from the service command and the service's config (service
folder, Python, ledger, log folder, dashboard data, scheduled-task paths, base
Python) and never touches those. It archives these leftovers to
`D:\FPF-Biometric-Archive` and then deletes them from drive C::

* `C:\GitHub\biometric-attendance-sync-tool`, `biometric-sync-manager-29a440c` and
  `biometric-sync-service-phase2` (the first only once its logs have moved; until
  then it is skipped);
* `C:\Program Files\FPF Biometric Sync` and `backup-shift-sync-*` inside the service
  folder;
* in `ProgramData`: `BiometricAttendanceSync`, `ERPNextBiometricBackups`,
  `ERPNextBiometricConfirmation`, `ERPNextBiometricPushService-Backups`,
  `FPF-Biometric-Capture`, `FPF-Diagnostic-*` and the ledger folder's `backups`;
* per user: `AppData\Local\BiometricPhase1OfflineTest`, `FPF-Phase1-Staging-*`, and the
  `biometric-diag-*`, `monitor-dashboard-*` and `monitor-snapshot-*` items on the Desktop.

Each item is copied with robocopy and checked (file count and bytes) before the
original is deleted; a failed item is reported and kept. The archive is readable by
SYSTEM and Administrators only, because old configs contain the ERPNext API key. A
`manifest-*.csv` lists what was archived. `C:\GitHub` is removed when it is empty.

Keep the archive for at least two weeks after the attendance rebuild; it is the
record of what earlier installs sent. Then delete `D:\FPF-Biometric-Archive`.
