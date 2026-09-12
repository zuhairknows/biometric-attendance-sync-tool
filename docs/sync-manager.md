# FPF Biometric Sync Manager

`FPF Biometric Sync Manager` is a small Windows desktop utility for operating the existing biometric attendance Windows service.

The manager is not the synchronization engine. The existing `erpnext_sync.py` and `erpnext_sync_win.py` files remain responsible for pulling ZKTeco punches and pushing Employee Checkin data to ERPNext.

## Features

- Show Windows service status and startup mode.
- Install, uninstall, start, stop, and restart `ERPNextBiometricPushService`.
- Validate the current runtime configuration using `erpnext_sync.validate_runtime_config()`.
- Test ERPNext authentication with a harmless authenticated endpoint.
- Test each configured ZKTeco device connection without changing device data.
- Run exactly one manual sync cycle with `erpnext_sync.main()`.
- Show the last successful sync from `status.json`.
- Show each device's last pull and last push timestamps.
- Open the logs folder and configuration folder.

## Administrator Permissions

Installing and uninstalling a Windows service requires Administrator permission.

If the manager is not running as Administrator, setup actions show a clear message and do not attempt the action.

Starting, stopping, and restarting a service can also require elevated Windows permissions depending on the local machine policy.

## Service Actions

The manager uses the existing service script:

```text
erpnext_sync_win.py
```

Install runs the equivalent of:

```text
python erpnext_sync_win.py install
```

Developer note: service installation currently uses the Python interpreter that launched the manager. This is correct for development and virtual environment usage. Final PyInstaller packaging will need a dedicated installed service executable or runtime path; do not redesign that until the installer phase.

Uninstall first stops the service if it is running, then removes the service registration.

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
