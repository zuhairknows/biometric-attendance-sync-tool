# Windows Service Deployment

This guide is for running the FPF ZKTeco to ERPNext biometric attendance sync tool as a Windows service.

Do not install the service until `local_config.py` has been reviewed, tests pass, and a manual sync run has been checked.

## Prerequisites

Run these commands from PowerShell or Command Prompt:

```bat
cd C:\GitHub\biometric-attendance-sync-tool
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` includes `pywin32` only on Windows through an environment marker. Use the virtual environment Python for all service commands so Windows registers the intended environment.

## Manual Validation Before Installing Service

Create `local_config.py` from `local_config.py.template`, then fill in the real local credentials and device settings.

Run one manual sync from the activated virtual environment:

```bat
python erpnext_sync.py
```

Inspect:
- `logs\logs.log`
- `logs\error.log`
- `logs\status.json`
- `logs\attendance_success_log_<device_id>.log`
- `logs\attendance_failed_log_<device_id>.log`

Stop the manual process after confirming the expected behavior. Do not delete `status.json`; it is part of the local checkpoint state.

## Unit Tests

```bat
python -m unittest discover -s tests -v
```

These tests mock Windows service APIs, ERPNext calls, and ZKTeco devices. They do not install a Windows service and do not require a physical biometric device.

## Install Service

Use the virtual environment Python explicitly:

```bat
.venv\Scripts\python.exe erpnext_sync_win.py install
```

If updating an existing service registration:

```bat
.venv\Scripts\python.exe erpnext_sync_win.py update
```

## Start

From Command Prompt or PowerShell with administrator rights:

```cmd
net start ERPNextBiometricPushService
```

You can also start the service from `services.msc`.

## Stop

```cmd
net stop ERPNextBiometricPushService
```

The service waits on the Windows stop event and should stop promptly. The sync engine still controls actual polling through `PULL_FREQUENCY`.

## Update

Use this safe sequence:

1. Stop the service.
2. Pull or copy the updated code.
3. Activate the same virtual environment.
4. Install changed dependencies if needed.
5. Run unit tests.
6. Run a manual validation if the sync behavior changed.
7. Update the service registration if required.
8. Start the service.
9. Verify logs and new Employee Checkins in ERPNext.

Example:

```bat
net stop ERPNextBiometricPushService
cd C:\GitHub\biometric-attendance-sync-tool
.venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
.venv\Scripts\python.exe erpnext_sync_win.py update
net start ERPNextBiometricPushService
```

## Remove

```bat
.venv\Scripts\python.exe erpnext_sync_win.py remove
```

## Windows Service Settings

Recommended settings in `services.msc`:
- Startup Type: Automatic (Delayed Start)
- First failure: Restart the Service
- Second failure: Restart the Service
- Subsequent failures: Restart the Service
- Restart delay: 1 minute

Configure these manually unless there is a separate deployment policy for automating Windows service recovery.

## Service Account

For a simple deployment, Local System may be enough if it has the required network and file permissions.

For a managed production environment, prefer a dedicated Windows service account.

The service account needs:
- read access to the application files
- read access to `local_config.py`
- write access to the configured `LOGS_DIRECTORY`
- network access to ZKTeco devices
- HTTPS access to ERPNext

Do not hardcode Windows account credentials or ERPNext API credentials in tracked files.

