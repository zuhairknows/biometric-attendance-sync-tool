# Production Verification Checklist

Use this checklist before and after a controlled Windows service or installer installation.

## Repository

- [ ] Running from clean `main`
- [ ] Tests passing
- [ ] No secrets tracked
- [ ] `local_config.py` is ignored by Git
- [ ] `local_config.py` is not tracked by Git
- [ ] No logs, `status.json`, retry dumps, `.venv`, or cache files tracked

## Installer

- [ ] Built with `powershell -ExecutionPolicy Bypass -File .\build\build_release.ps1`
- [ ] Final artifact exists at `release\installer\Biometric-Attendance-Sync-Setup-0.1.0.exe`
- [ ] Setup run as Administrator
- [ ] Program files installed under `C:\Program Files\Biometric Attendance Sync`
- [ ] Manager installed at `C:\Program Files\Biometric Attendance Sync\Biometric-Attendance-Sync-Manager.exe`
- [ ] Service installed at `C:\Program Files\Biometric Attendance Sync\service\Biometric-Attendance-Sync-Service.exe`
- [ ] ProgramData config/log/state/retry folders created under `C:\ProgramData\BiometricAttendanceSync`
- [ ] ProgramData secrets/backups/diagnostics folders created under `C:\ProgramData\BiometricAttendanceSync`
- [ ] Real `local_config.py` preserved if already present
- [ ] Only `local_config.py.template` installed when config is missing
- [ ] Apps & Features entry appears
- [ ] Uninstall removes service and binaries
- [ ] Uninstall preserves `C:\ProgramData\BiometricAttendanceSync`
- [ ] Legacy `C:\ProgramData\FPF\BiometricSync` remains untouched if it exists

## ERPNext

- [ ] API user is active
- [ ] API user can create Employee Checkin
- [ ] API user can write Shift Type if last-sync updates are used
- [ ] Employee `attendance_device_id` matches biometric device PIN/User ID
- [ ] Shift Assignments or default shifts are correct
- [ ] Auto Attendance configuration is verified
- [ ] Go-live `IMPORT_START_DATE` is correct

## Device

- [ ] Device is reachable from the Windows integration PC
- [ ] Correct IP address or hostname
- [ ] Correct port
- [ ] Correct connection password
- [ ] Permanent logical `device_id` is correct
- [ ] Device clock and timezone are correct
- [ ] `clear_from_device_on_fetch = False`

## Functional Tests

- [ ] Normal punch creates Employee Checkin
- [ ] Duplicate punch is idempotent
- [ ] Unknown employee does not stop the entire service
- [ ] Inactive employee behavior is verified
- [ ] Device offline does not stop another device
- [ ] Temporary ERPNext outage is logged and recovers on a later cycle
- [ ] Retry dump survives restart
- [ ] Arabic logs render correctly
- [ ] `status.json` survives restart

## Windows Service

- [ ] Service installs as `ERPNextBiometricPushService`
- [ ] Service starts
- [ ] Service stops
- [ ] Service restarts
- [ ] PC reboot tested
- [ ] Service automatically starts after reboot
- [ ] Service runs while no interactive user is logged in
- [ ] Logs are still written
- [ ] ERPNext receives new checkins
- [ ] Service recovery is configured

## Manager Validation

- [ ] Launch Biometric Attendance Sync Manager as a normal user
- [ ] Confirm service shows `Running`
- [ ] Confirm Startup shows `Automatic (Delayed Start)` if configured
- [ ] Confirm Recovery shows restart behavior if configured
- [ ] Confirm configuration state, source, ERPNext URL, SSL, device count, interval, import start date, schema, update timestamp, and credential-present summary are visible without secret values
- [ ] Confirm Last Successful Sync is current
- [ ] Confirm device pull/push timestamps are current
- [ ] Click **Test ERPNext** and confirm `Connected`
- [ ] Click **Test Devices** and confirm configured devices show `Connected`
- [ ] Click **Refresh** and confirm device status stays `Connected`
- [ ] Confirm **Run Sync Now** is disabled while service is `Running`
- [ ] Stop service from Manager
- [ ] Confirm service status becomes `Stopped`
- [ ] Confirm **Run Sync Now** becomes enabled
- [ ] Run manual sync
- [ ] Confirm mission timestamp advances and the UI reports success
- [ ] Start service from Manager
- [ ] Confirm service returns to `Running`
- [ ] Restart service from Manager
- [ ] Confirm service returns to `Running`
- [ ] Close Manager
- [ ] Confirm service remains `Running`
- [ ] Re-open Manager
- [ ] Confirm actual service state is detected correctly
- [ ] Click **Back Up Configuration** and confirm a zip appears under `C:\ProgramData\BiometricAttendanceSync\backups`
- [ ] Inspect backup contents and confirm it excludes logs, retry dumps, and plaintext credential values
- [ ] Click **Export Diagnostics** and confirm a JSON report appears under `C:\ProgramData\BiometricAttendanceSync\diagnostics`
- [ ] Inspect diagnostics and confirm API keys, API secrets, device passwords, secret blobs, logs, and retry payloads are absent
- [ ] Edit configuration with secret fields left blank and confirm the existing protected credentials still work
- [ ] Edit one credential or device password and confirm the review page reports replacement without showing the value
- [ ] Save while service is running and confirm the Manager prompts for service restart
- [ ] Restore a known-good same-machine backup and confirm runtime validation still passes
- [ ] Attempt restore of an invalid backup in a test environment and confirm the previous configuration remains active
- [ ] Reset configuration in a test environment and confirm service stops, commercial config/secrets are removed, and logs/retry/state/backups/diagnostics are preserved

Do not uninstall the production service during first validation unless it is necessary for recovery.

## Runtime Paths

- [ ] Program files planned for `C:\Program Files\Biometric Attendance Sync\`
- [ ] Service executable planned for `C:\Program Files\Biometric Attendance Sync\service\Biometric-Attendance-Sync-Service.exe`
- [ ] Config exists at `C:\ProgramData\BiometricAttendanceSync\config\local_config.py`
- [ ] Logs are written under `C:\ProgramData\BiometricAttendanceSync\logs`
- [ ] State is written under `C:\ProgramData\BiometricAttendanceSync\state`
- [ ] Retry files are written under `C:\ProgramData\BiometricAttendanceSync\retry`
- [ ] Protected secret files are written under `C:\ProgramData\BiometricAttendanceSync\secrets`
- [ ] Configuration backups are written under `C:\ProgramData\BiometricAttendanceSync\backups`
- [ ] Diagnostics exports are written under `C:\ProgramData\BiometricAttendanceSync\diagnostics`
- [ ] `local_config.py` is external and not bundled into the executable
- [ ] Manager opens ProgramData logs/config folders in packaged mode

## Legacy Compatibility

- [ ] Existing legacy config at `C:\ProgramData\FPF\BiometricSync\config\local_config.py` is detected only when no new config exists
- [ ] Legacy `FPF_BIOMETRIC_*` environment variables are treated as compatibility aliases only
- [ ] Current `BIOMETRIC_SYNC_*` environment variables take priority over legacy aliases

## Known Console Display Note

- [ ] If Arabic text looks garbled in `cmd.exe`, open the UTF-8 log file in the Manager or a UTF-8 capable editor before treating it as a logging defect
