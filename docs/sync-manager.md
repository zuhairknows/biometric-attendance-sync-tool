# FPF Biometric Sync Manager

`FPF Biometric Sync Manager` is a small Windows desktop utility for operating the existing biometric attendance Windows service.

The manager is not the synchronization engine. The existing `erpnext_sync.py` and `erpnext_sync_win.py` files remain responsible for pulling ZKTeco punches and pushing Employee Checkin data to ERPNext.

## Features

- Show Windows service status and startup mode.
- Show Windows service recovery summary when available.
- Install, uninstall, start, stop, and restart `ERPNextBiometricPushService`.
- Validate the current runtime configuration using `erpnext_sync.validate_runtime_config()`.
- Test ERPNext authentication with a harmless authenticated endpoint.
- Test each configured ZKTeco device connection without changing device data.
- Run exactly one manual sync cycle with `erpnext_sync.main()`.
- Show the last successful sync from `status.json`.
- Show each device's last pull and last push timestamps.
- Open the logs folder and configuration folder.

## Runtime Modes

The manager supports three current operating modes:

- Development: use the Python interpreter that launched the manager with `erpnext_sync_win.py`.
- Packaged/installed: use `service\FPF-Biometric-Sync-Service.exe` beside the packaged manager.
- FP1 validation fallback: use `C:\FPF-Test\FPF-Biometric-Sync-Service\FPF-Biometric-Sync-Service.exe` when present.

The explicit `FPF_BIOMETRIC_SERVICE_EXE` environment variable takes priority over automatic detection.

The FP1 fallback is transitional and exists only to support the current packaged-service validation layout before the final installer places files under `C:\Program Files\FPF Biometric Sync\`.

The installer packages the manager as:

```text
C:\Program Files\FPF Biometric Sync\FPF-Biometric-Sync-Manager.exe
```

See [Production Windows Installer](installer.md) for setup, upgrade, and uninstall behavior.

## Administrator Permissions

Installing and uninstalling a Windows service requires Administrator permission.

If the manager is not running as Administrator, setup actions show a clear message and do not attempt the action.

Starting, stopping, and restarting a service can also require elevated Windows permissions depending on the local machine policy. Access-denied responses are shown as friendly admin-required messages.

## Service Actions

In production mode, install runs the packaged service executable:

```text
FPF-Biometric-Sync-Service.exe install
```

Development fallback runs the equivalent of:

```text
python erpnext_sync_win.py install
```

Developer note: in development, service installation can still use the Python interpreter that launched the manager. In packaged deployments, the manager should use the frozen `FPF-Biometric-Sync-Service.exe` runtime when it is available. See [Packaged Windows Service](packaged-service.md).

Uninstall first stops the service if it is running, waits for `Stopped`, then removes the service registration. If the service is already missing, the manager shows `Service is not installed.` instead of treating that as a scary failure.

Start, stop, and restart actions now wait up to 30 seconds for the actual Windows service state to change. A successful command is not considered enough by itself.

The manager can also set delayed auto start and configure service recovery from explicit operator actions. It does not silently overwrite startup or recovery settings.

Uninstall does not delete:

- `local_config.py`
- `logs.log`
- `error.log`
- `status.json`
- attendance success or failure logs
- retry dump JSON files

## Diagnostics

Configuration validation reuses the sync engine's runtime validation. Secrets are not shown in the manager UI.

ERPNext testing calls:

```text
/api/method/frappe.auth.get_logged_user
```

This verifies that the site is reachable and the API token is accepted. It does not create Employee Checkin records.

Device testing connects to each configured ZKTeco device and reads a harmless device property when supported. It disconnects immediately and does not clear attendance, modify users, or write punches.

## Health Data

The manager reads health from the existing runtime files. `status.json` remains the source of truth for:

- `mission_accomplished_timestamp`
- `<device_id>_pull_timestamp`
- `<device_id>_push_timestamp`

No separate `health.json` file is created.

If `status.json` is missing or corrupt, the manager stays open and shows unknown/empty health data until the service writes a valid status file.

## Production Paths

Packaged and FP1 packaged-service modes use:

```text
C:\ProgramData\FPF\BiometricSync\config\local_config.py
C:\ProgramData\FPF\BiometricSync\logs
```

Development mode continues to use the repo-local `local_config.py` and logs unless environment overrides are set.

The manager respects:

```text
FPF_BIOMETRIC_PROGRAMDATA
FPF_BIOMETRIC_CONFIG_DIR
FPF_BIOMETRIC_SERVICE_EXE
```

## Manual Sync Safety

Do not run a manual sync while the Windows service is actively running. The manager disables **Run Sync Now** when the service state is `Running` and shows:

```text
Stop the Windows service before running a manual sync.
```

This prevents two independent sync loops from accessing the same ZKTeco device at the same time.

## UTF-8 / Arabic Logs

The sync logs are written and read as UTF-8. If Arabic shift names look garbled in raw `cmd.exe` output, that is a console rendering/code-page issue, not necessarily a bad log file. The manager reads log and status files with UTF-8 where it opens them directly.

## Troubleshooting

If the service is not installed, use **Install Service** from an Administrator-launched manager window.

If ERPNext shows authentication failure, check the API key and API secret in `local_config.py`.

If ERPNext is unreachable, check the ERPNext URL, internet connectivity, DNS, and firewall rules.

If a device connection fails, check the device IP address, port, network cable or Wi-Fi, device power, and ZKTeco communication password.

If the manager opens but configuration cannot be loaded, fix `local_config.py` and use **Refresh**.

## Launch Locally

From the repository folder:

```text
python -m manager.app
```
