# Production Verification Checklist

Use this checklist before and after the first controlled Windows service installation.

## Repository

- [ ] Running from clean `master` before creating a deployment branch
- [ ] Tests passing
- [ ] No secrets tracked
- [ ] `local_config.py` is ignored by Git
- [ ] `local_config.py` is not tracked by Git
- [ ] No logs, `status.json`, retry dumps, `.venv`, or cache files tracked

## Installer

- [ ] Built with `powershell -ExecutionPolicy Bypass -File build\build_release.ps1`
- [ ] Final artifact exists at `release\installer\FPF-Biometric-Sync-Setup-0.1.0.exe`
- [ ] Setup run as Administrator
- [ ] Program files installed under `C:\Program Files\FPF Biometric Sync`
- [ ] Manager installed at `C:\Program Files\FPF Biometric Sync\FPF-Biometric-Sync-Manager.exe`
- [ ] Service installed at `C:\Program Files\FPF Biometric Sync\service\FPF-Biometric-Sync-Service.exe`
- [ ] ProgramData config/log/state/retry folders created
- [ ] Real `local_config.py` preserved if already present
- [ ] Only `local_config.py.template` installed when config is missing
- [ ] Apps & Features entry appears
- [ ] Uninstall removes service and binaries
- [ ] Uninstall preserves `C:\ProgramData\FPF\BiometricSync`

## ERPNext

- [ ] API user is active
- [ ] API user can create Employee Checkin
- [ ] API user can write Shift Type if last-sync updates are used
- [ ] Employee `attendance_device_id` matches ZKTeco PIN/User ID
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

- [ ] Service installs
- [ ] Service starts
- [ ] Service stops
- [ ] Service restarts
- [ ] PC reboot tested
- [ ] Service automatically starts after reboot
- [ ] Service runs while no interactive user is logged in
- [ ] Logs are still written
- [ ] ERPNext receives new checkins
- [ ] Service recovery is configured

## Manager Validation On FP1

Use this after the packaged service has been installed and validated.

- [ ] Launch Manager as a normal user
- [ ] Confirm service shows `Running`
- [ ] Confirm Startup shows `Automatic (Delayed Start)` if configured
- [ ] Confirm Recovery shows restart behavior if configured
- [ ] Confirm Last Successful Sync is current
- [ ] Confirm device pull/push timestamps are current
- [ ] Click **Test ERPNext** and confirm `Connected`
- [ ] Click **Test Devices** and confirm `FP1_DEVICE_01` shows `Connected`
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

Do not uninstall the production service during the first Manager validation unless it is necessary for recovery.

## Runtime Paths

- [ ] Program files planned for `C:\Program Files\FPF Biometric Sync\`
- [ ] Service executable planned for `C:\Program Files\FPF Biometric Sync\service\FPF-Biometric-Sync-Service.exe`
- [ ] Config exists at `C:\ProgramData\FPF\BiometricSync\config\local_config.py`
- [ ] Logs are written under `C:\ProgramData\FPF\BiometricSync\logs`
- [ ] `local_config.py` is external and not bundled into the executable
- [ ] Manager opens ProgramData logs/config folders in packaged mode

## Known Console Display Note

- [ ] If Arabic text looks garbled in `cmd.exe`, open the UTF-8 log file in the Manager or a UTF-8 capable editor before treating it as a logging defect
