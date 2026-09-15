# M5.6 Manual Windows Acceptance Checklist

Use this checklist on clean Windows test machines and controlled upgrade
machines. Record `Pass`, `Fail`, or `N/A` for every scenario.

## Scenario A - Clean Install, No Configuration

Result: Pass / Fail / N/A

1. Back up any existing `C:\ProgramData\BiometricAttendanceSync` test data.
2. Install `Biometric-Attendance-Sync-Setup-<version>.exe` as Administrator.
3. Verify the installer creates `config`, `logs`, `state`, `retry`, `secrets`,
   `backups`, and `diagnostics` under `C:\ProgramData\BiometricAttendanceSync`.
4. Verify the Windows service is installed but not started before setup.
5. Launch Biometric Attendance Sync from the Start Menu.
6. Verify first-run setup opens automatically.

Expected result: installation succeeds, no customer config is overwritten, and
the administrator is guided into first-run setup.

## Scenario B - First-Run Configuration And Service Start

Result: Pass / Fail / N/A

1. Complete ERPNext, device, sync interval, and import start date setup.
2. Save configuration through the Manager.
3. Verify configuration is saved securely under ProgramData.
4. Verify the synchronization service starts or restarts through the Manager.
5. Reopen Manager and verify the normal dashboard opens.

Expected result: setup only reports complete after valid configuration is saved
and the synchronization service is running.

## Scenario C - Existing Configured Install Opens Dashboard

Result: Pass / Fail / N/A

1. Install over a machine with a valid existing commercial `config.json`.
2. Launch Biometric Attendance Sync.
3. Verify the first-run wizard is not forced.
4. Verify the dashboard shows configuration, service, device, and last-sync
   status.

Expected result: configured installations continue directly to the Manager
dashboard.

## Scenario D - Upgrade Preserves ProgramData

Result: Pass / Fail / N/A

1. Capture checksums or timestamps for `config.json`, protected secrets, logs,
   state, retry files, backups, and diagnostics before upgrade.
2. Run the new installer over the existing install.
3. Verify Program Files binaries are replaced.
4. Verify ProgramData customer files remain present.
5. Verify the service is re-registered and configured for delayed automatic
   startup and recovery.

Expected result: upgrade updates binaries and service registration without
deleting customer data.

## Scenario E - Upgrade With Running Service

Result: Pass / Fail / N/A

1. Start the synchronization service.
2. Run the installer upgrade.
3. Verify the installer stops the existing service before replacing binaries.
4. Verify the service starts after install when real configuration exists.

Expected result: upgrade handles a running service without locked files or lost
configuration.

## Scenario F - Service Start Failure

Result: Pass / Fail / N/A

1. Create a controlled condition where the service cannot start.
2. Save setup or attempt to start the service from Manager.
3. Verify Manager does not claim setup is complete.
4. Verify the UI shows an actionable customer-friendly error and technical
   details remain in logs or diagnostics.

Expected result: failure is clear and setup completion is not falsely reported.

## Scenario G - Shortcuts And Product Naming

Result: Pass / Fail / N/A

1. Verify the Start Menu shortcut is named `Biometric Attendance Sync`.
2. If selected, verify the desktop shortcut is named `Biometric Attendance Sync`.
3. Verify installer completion text and post-install launch text use generic
   product naming.
4. Verify customer-facing screens do not show company-specific names.

Expected result: product naming is generic and commercial.

## Scenario H - Uninstall Preserves Customer Data

Result: Pass / Fail / N/A

1. Uninstall from Apps & Features.
2. Verify the Windows service is stopped and removed.
3. Verify Program Files application binaries and shortcuts are removed.
4. Verify `C:\ProgramData\BiometricAttendanceSync` remains present.

Expected result: uninstall removes the application and service while preserving
customer configuration, logs, state, backups, and diagnostics.

## Scenario I - Reinstall After Uninstall

Result: Pass / Fail / N/A

1. Reinstall after Scenario H without deleting ProgramData.
2. Launch Biometric Attendance Sync.
3. Verify existing configuration is detected.
4. Verify the dashboard opens and the service can start with preserved settings.

Expected result: reinstall recovers the preserved configured installation.
