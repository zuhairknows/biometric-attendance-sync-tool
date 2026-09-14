# Biometric Attendance Sync Manager

`Biometric Attendance Sync Manager` is a small Windows desktop utility for operating the existing biometric attendance Windows service.

The manager is not the synchronization engine. The existing `erpnext_sync.py` and `erpnext_sync_win.py` files remain responsible for pulling biometric punches and pushing Employee Checkin data to ERPNext.

## Features

- Show Windows service status and startup mode.
- Show Windows service recovery summary when available.
- Install, uninstall, start, stop, and restart `ERPNextBiometricPushService`.
- Validate the current runtime configuration using the same normalized loader and validation path as the sync service.
- Test ERPNext authentication with a harmless authenticated endpoint.
- Test each configured biometric device connection without changing device data.
- Run exactly one manual sync cycle with `erpnext_sync.main()`.
- Show the last successful sync from `status.json`.
- Show each device's last pull and last push timestamps.
- Open the logs folder and configuration folder.
- Launch first-run setup when no valid commercial or legacy configuration exists.
- Edit or repair commercial configuration without exposing stored secrets.
- Back up, restore, and reset commercial configuration.
- Export a sanitized diagnostics report for support review.

## Runtime Modes

The manager supports these operating modes:

- Development: use the Python interpreter that launched the manager with `erpnext_sync_win.py`.
- Packaged/installed: use `service\Biometric-Attendance-Sync-Service.exe` beside the packaged manager.
- Compatibility: use the legacy packaged service executable name only when the new executable is absent.

The explicit `BIOMETRIC_SYNC_SERVICE_EXE` environment variable takes priority over automatic detection.

Legacy `FPF_BIOMETRIC_SERVICE_EXE` is still accepted temporarily for existing installations, but should not be used for new setup.

The installer packages the manager as:

```text
C:\Program Files\Biometric Attendance Sync\Biometric-Attendance-Sync-Manager.exe
```

See [Production Windows Installer](installer.md) for setup, upgrade, and uninstall behavior.

## Administrator Permissions

Installing and uninstalling a Windows service requires Administrator permission.

If the manager is not running as Administrator, setup actions show a clear message and do not attempt the action.

Starting, stopping, and restarting a service can also require elevated Windows permissions depending on the local machine policy. Access-denied responses are shown as friendly admin-required messages.

## Service Actions

In production mode, install runs the packaged service executable:

```powershell
Biometric-Attendance-Sync-Service.exe install
```

Development fallback runs the equivalent of:

```powershell
python erpnext_sync_win.py install
```

Developer note: in development, service installation can still use the Python interpreter that launched the manager. In packaged deployments, the manager should use the frozen `Biometric-Attendance-Sync-Service.exe` runtime when it is available. See [Packaged Windows Service](packaged-service.md).

Uninstall first stops the service if it is running, waits for `Stopped`, then removes the service registration. If the service is already missing, the manager shows `Service is not installed.` instead of treating that as a failure.

Start, stop, and restart actions wait up to 30 seconds for the actual Windows service state to change. A successful command is not considered enough by itself.

The manager can also set delayed auto start and configure service recovery from explicit operator actions. It does not silently overwrite startup or recovery settings.

Uninstall does not delete:

- `local_config.py`
- `logs.log`
- `error.log`
- `status.json`
- attendance success or failure logs
- retry dump JSON files

## Diagnostics

Configuration validation reuses the sync engine's normalized runtime validation. Commercial JSON secrets are resolved from protected secret refs before validation. Secrets are not shown in the manager UI.

ERPNext testing calls:

```text
/api/method/frappe.auth.get_logged_user
```

This verifies that the site is reachable and the API token is accepted. It does not create Employee Checkin records.

Device testing connects to each configured biometric device and reads a harmless device property when supported. It disconnects immediately and does not clear attendance, modify users, or write punches.

## Health Data

The manager reads health from the existing runtime files. `status.json` remains the source of truth for:

- `mission_accomplished_timestamp`
- `<device_id>_pull_timestamp`
- `<device_id>_push_timestamp`

No separate `health.json` file is created.

If `status.json` is missing or corrupt, the manager stays open and shows unknown/empty health data until the service writes a valid status file.

## Production Paths

Packaged mode uses:

```text
C:\ProgramData\BiometricAttendanceSync\config.json
C:\ProgramData\BiometricAttendanceSync\config\local_config.py
C:\ProgramData\BiometricAttendanceSync\logs
```

The JSON file has precedence. The Python file is retained for legacy compatibility.

On a fresh installation, the Manager shows `Not Configured` and opens the first-run setup wizard. Existing valid legacy installations show `Legacy Configuration` and are not automatically migrated.

When saving commercial setup, the Manager writes `api_key_ref`, `api_secret_ref`, and device `password_ref` values to `config.json`; the actual credential values are stored below `C:\ProgramData\BiometricAttendanceSync\secrets`. Blank secret fields during an edit keep the existing protected secret. Entering a new value replaces that secret.

The setup button text reflects the current state:

- `Start Setup`: no valid configuration exists.
- `Edit Configuration`: valid commercial JSON exists.
- `Repair Configuration`: commercial configuration exists but cannot be loaded or validated.

Valid legacy installations show `Legacy Configuration` and are not reset or rewritten by the commercial setup controls.

The configuration panel shows a sanitized summary: ERPNext URL, SSL setting, enabled and total devices, sync interval, import start date, schema version, last update timestamp, and whether ERPNext credentials are present. It does not display API keys, API secrets, or device passwords.

## Configuration Management

**Back Up Configuration** writes a zip backup under `C:\ProgramData\BiometricAttendanceSync\backups`. It includes `config.json`, metadata, and protected secret files only. It excludes logs, status files, retry dumps, and plaintext credential values.

**Restore Configuration** prompts for a Manager-created backup zip, validates the archive paths, restores commercial JSON and protected secrets, then validates the restored runtime configuration. If validation fails, the previous commercial config and secrets are restored.

**Reset Configuration** stops the service if it is running, creates a backup when commercial JSON exists, removes `config.json`, and clears commercial protected secrets. Logs, retry files, state files, backups, diagnostics, and legacy `local_config.py` are preserved.

**Export Diagnostics** writes a sanitized JSON report under `C:\ProgramData\BiometricAttendanceSync\diagnostics`. It includes status, source, safe config summary, device IDs and addresses, and paths. It never includes secret values, encrypted secret blobs, logs, or retry payloads.

When setup or restore changes configuration while the Windows service is running, the Manager asks whether to restart the service. Restarting applies the new config immediately. Skipping restart leaves the saved config ready for the next service restart.

DPAPI-protected secret backups are intended for the same Windows machine. If a backup is restored on another PC and secrets cannot decrypt, use **Repair Configuration** to re-enter the credentials and device passwords.

Development mode continues to use the repo-local `local_config.py` and logs unless environment overrides are set.

The manager respects:

```text
BIOMETRIC_SYNC_PROGRAMDATA
BIOMETRIC_SYNC_CONFIG_DIR
BIOMETRIC_SYNC_SERVICE_EXE
```

Legacy compatibility aliases are still recognized temporarily:

```text
FPF_BIOMETRIC_PROGRAMDATA
FPF_BIOMETRIC_CONFIG_DIR
FPF_BIOMETRIC_SERVICE_EXE
C:\ProgramData\FPF\BiometricSync
FPF-Biometric-Sync-Service.exe
```

Current `BIOMETRIC_SYNC_*` values take priority when both current and legacy variables are set.

## Manual Sync Safety

Do not run a manual sync while the Windows service is actively running. The manager disables **Run Sync Now** when the service state is `Running` and shows:

```text
Stop the Windows service before running a manual sync.
```

This prevents two independent sync loops from accessing the same biometric device at the same time.

## UTF-8 / Arabic Logs

The sync logs are written and read as UTF-8. If Arabic shift names look garbled in raw `cmd.exe` output, that is a console rendering/code-page issue, not necessarily a bad log file. The manager reads log and status files with UTF-8 where it opens them directly.

## Troubleshooting

If the service is not installed, use **Install Service** from an Administrator-launched manager window.

If ERPNext shows authentication failure, use the Manager setup flow to update the API key and API secret. For legacy deployments, check `local_config.py`.

If ERPNext is unreachable, check the ERPNext URL, internet connectivity, DNS, and firewall rules.

If a device connection fails, check the device IP address, port, network cable or Wi-Fi, device power, and biometric-device communication password.

If the manager shows `Invalid`, repair the commercial `config.json` and protected secrets or rerun setup, then use **Refresh**. A broken commercial config does not fall back to legacy `local_config.py`.

## Launch Locally

From the repository folder:

```powershell
python -m manager.app
```
