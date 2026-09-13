# Biometric Attendance Sync

Biometric Attendance Sync polls ZKTeco-compatible biometric attendance devices and submits Employee Checkin records to ERPNext/HRMS.

ERPNext remains the attendance source of truth. This tool reads device punches, sends them to ERPNext, writes local logs/checkpoints, and provides a Windows Manager app for day-to-day operation.

## Prerequisites

- Python 3.10+ for source/development usage.
- Windows for the packaged Manager, packaged Service, and Inno Setup installer flow.
- ERPNext/HRMS API credentials with permission to create Employee Checkin records.
- Network access from the integration PC to the biometric devices and ERPNext site.

## Configuration

Create a real `local_config.py` from `local_config.py.template`.

For source/development usage, keep `local_config.py` in the repository folder.

For packaged Windows usage, keep it outside Program Files:

```text
C:\ProgramData\BiometricAttendanceSync\config\local_config.py
```

Mutable packaged runtime data uses:

```text
C:\ProgramData\BiometricAttendanceSync\
    config\
    logs\
    state\
    retry\
```

Current environment overrides:

```text
BIOMETRIC_SYNC_PROGRAMDATA
BIOMETRIC_SYNC_CONFIG_DIR
BIOMETRIC_SYNC_SERVICE_EXE
```

Legacy compatibility aliases from older builds are still accepted temporarily:

```text
FPF_BIOMETRIC_PROGRAMDATA
FPF_BIOMETRIC_CONFIG_DIR
FPF_BIOMETRIC_SERVICE_EXE
C:\ProgramData\FPF\BiometricSync
```

Use the `BIOMETRIC_SYNC_*` names for new deployments.

## Device Configuration

Each configured biometric device needs a stable logical `device_id`. Do not change `device_id` when only the IP address, hostname, port, or password changes; it is used for ERPNext device identity, retry dumps, logs, and status keys.

Example:

```python
devices = [
    {
        "device_id": "DEVICE_01",
        "ip": "192.0.2.10",
        "port": 4370,
        "password": 0,
        "punch_direction": None,
        "clear_from_device_on_fetch": False,
    }
]
```

For ERPNext/HRMS v15, the biometric device User ID/PIN should match the Employee Attendance Device ID.

Keep `clear_from_device_on_fetch = False` in production. Setting it to `True` can delete attendance records from the biometric device after fetch.

## Source Usage

Install dependencies:

```powershell
cd C:\Projects\biometric-attendance-sync-tool
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Run one manual sync cycle:

```powershell
python -c "import erpnext_sync; erpnext_sync.main()"
```

Run the legacy GUI entry point only when needed:

```powershell
python gui.py
```

## Windows Service

The Windows service name remains stable for upgrade compatibility:

```text
ERPNextBiometricPushService
```

Development/source install commands:

```powershell
.\.venv\Scripts\python.exe erpnext_sync_win.py install
net start ERPNextBiometricPushService
net stop ERPNextBiometricPushService
.\.venv\Scripts\python.exe erpnext_sync_win.py remove
```

The packaged service executable is:

```text
Biometric-Attendance-Sync-Service.exe
```

See [Windows Service Deployment](docs/windows-service.md) and [Packaged Windows Service](docs/packaged-service.md) for the full service contract.

## Manager

The Windows desktop utility is named:

```text
Biometric Attendance Sync Manager
```

Local launch:

```powershell
python -m manager.app
```

Packaged executable:

```text
Biometric-Attendance-Sync-Manager.exe
```

The Manager can show service health, validate configuration, test ERPNext, test biometric devices, run one manual sync cycle, and open runtime folders. It does not change the Windows service name.

See [Sync Manager](docs/sync-manager.md).

## Build

Run the normal release build from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\build\build_release.ps1
```

Expected packaged outputs:

```text
dist\Biometric-Attendance-Sync-Manager\Biometric-Attendance-Sync-Manager.exe
dist\Biometric-Attendance-Sync-Service\Biometric-Attendance-Sync-Service.exe
release\Biometric Attendance Sync\
```

Expected installer when Inno Setup is available:

```text
release\installer\Biometric-Attendance-Sync-Setup-<version>.exe
```

## Logs And State

Development mode uses the configured `LOGS_DIRECTORY`, usually repo-local `logs`.

Packaged mode writes runtime files under `C:\ProgramData\BiometricAttendanceSync` unless an environment override or legacy compatibility config path is active.

Important files:

```text
logs\logs.log
logs\error.log
logs\status.json
logs\attendance_success_log_<device_id>.log
logs\attendance_failed_log_<device_id>.log
retry\...
```

Known duplicate Employee Checkin responses from ERPNext are treated as already synchronized only when ERPNext returns HTTP 417 with the expected duplicate timestamp message. Other HTTP 417 responses remain failures.

Logs are UTF-8 so Arabic and mixed-language messages can be written on Windows.

## Validation

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

## Resources

- [ERPNext biometric attendance device integration](https://docs.erpnext.com/docs/user/manual/en/setting-up/articles/integrating-erpnext-with-biometric-attendance-devices)
- [Original Frappe project wiki](https://github.com/frappe/biometric-attendance-sync-tool/wiki)

## License

This project is licensed under [GNU General Public License v3.0](LICENSE).
