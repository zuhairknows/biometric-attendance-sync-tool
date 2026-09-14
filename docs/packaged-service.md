# Packaged Windows Service

The packaged service is a self-contained PyInstaller build. It avoids depending on Git, a virtual environment, user-profile Python, or Python being installed on the target PC.

## Architecture

```text
C:\Program Files\Biometric Attendance Sync\
    Biometric-Attendance-Sync-Manager.exe
    service\
        Biometric-Attendance-Sync-Service.exe
        _internal\...

C:\ProgramData\BiometricAttendanceSync\
    config.json
    config\
        local_config.py
    logs\
    state\
    retry\
    secrets\
```

The manager controls `ERPNextBiometricPushService`. The packaged service executable runs the existing `erpnext_sync.py` logic. Sync engine behavior remains unchanged.

## Build Command

From the repository root:

```powershell
python -m PyInstaller -y --clean build\service.spec
```

The release build normally runs this through:

```powershell
powershell -ExecutionPolicy Bypass -File .\build\build_release.ps1
```

Expected service output:

```text
dist\Biometric-Attendance-Sync-Service\Biometric-Attendance-Sync-Service.exe
```

The service spec includes pywin32 service modules, including `win32timezone`, plus the sync engine dependencies `requests`, `zk`, and `pickledb`.

Commercial secret protection uses Windows DPAPI through the standard Windows API, so no extra Python package is required for the secret store.

## Runtime Layout

The installer places program files under:

```text
C:\Program Files\Biometric Attendance Sync\
```

Mutable runtime files live under:

```text
C:\ProgramData\BiometricAttendanceSync\
```

Subfolders:

```text
logs\
state\
retry\
secrets\
```

Commercial JSON configuration is loaded first from:

```text
C:\ProgramData\BiometricAttendanceSync\config.json
```

Existing production installations can still use legacy Python configuration at:

```text
C:\ProgramData\BiometricAttendanceSync\config\local_config.py
```

Do not bundle `config.json` with real credentials or `local_config.py` into the executable or distribution folder. They contain customer-specific configuration.

Commercial `config.json` stores secret refs such as `erpnext/api_key`, `erpnext/api_secret`, and `devices/DEVICE_01/password`. The encrypted secret files live below:

```text
C:\ProgramData\BiometricAttendanceSync\secrets\
```

Secrets use Windows DPAPI machine scope so the Manager running as the logged-in administrator can write them and the service running as LocalSystem can read them on the same PC. This is machine-local protection, not protection from a fully privileged same-machine administrator.

If packaged `local_config.py` uses a relative `LOGS_DIRECTORY`, the service runtime resolves it to:

```text
C:\ProgramData\BiometricAttendanceSync\logs
```

Using an absolute `LOGS_DIRECTORY` in production config is also acceptable.

## Service Commands

The packaged executable supports pywin32 service commands:

```powershell
Biometric-Attendance-Sync-Service.exe install
Biometric-Attendance-Sync-Service.exe remove
Biometric-Attendance-Sync-Service.exe start
Biometric-Attendance-Sync-Service.exe stop
Biometric-Attendance-Sync-Service.exe debug
```

When launched with no arguments by Windows Service Control Manager, the executable attaches to the pywin32 service dispatcher. Command-line service management verbs still use pywin32 `HandleCommandLine`.

The service name remains:

```text
ERPNextBiometricPushService
```

The display name remains:

```text
ERPNext Biometric Push Service
```

## Validation Procedure

1. Build the one-folder service with `python -m PyInstaller -y --clean build\service.spec`.
2. Confirm this file exists:

   ```text
   dist\Biometric-Attendance-Sync-Service\Biometric-Attendance-Sync-Service.exe
   ```

3. Create `C:\ProgramData\BiometricAttendanceSync\config.json`, or keep an existing reviewed `C:\ProgramData\BiometricAttendanceSync\config\local_config.py`.
4. Confirm logs resolve to a LocalSystem-writable location, preferably `C:\ProgramData\BiometricAttendanceSync\logs`.
5. Run debug mode only where it is safe and will not duplicate production sync work:

   ```powershell
   .\dist\Biometric-Attendance-Sync-Service\Biometric-Attendance-Sync-Service.exe debug
   ```

6. Confirm service startup reaches the Python service code and writes logs under ProgramData.
7. Do not install the packaged service on the development PC during build verification.

## Manager Integration

The manager resolves the service runtime in this order:

1. `BIOMETRIC_SYNC_SERVICE_EXE`
2. Legacy compatibility: `FPF_BIOMETRIC_SERVICE_EXE`
3. `<manager app root>\service\Biometric-Attendance-Sync-Service.exe`
4. `<manager app root>\Biometric-Attendance-Sync-Service.exe`
5. `<manager app root>\Biometric-Attendance-Sync-Service\Biometric-Attendance-Sync-Service.exe`
6. Legacy compatibility: equivalent `FPF-Biometric-Sync-Service.exe` paths
7. Development fallback: `python erpnext_sync_win.py`

Packaged service modes use ProgramData for config and logs. Development mode keeps the repo-local paths unless environment overrides are set.

If no valid commercial or legacy configuration exists, the service remains safely idle and logs `Product is not configured. Complete first-run setup.` It does not attempt ERPNext or device connections until setup succeeds.

If commercial configuration exists but protected secrets are missing, corrupt, or cannot be decrypted, the service also remains idle and logs `Configuration is invalid. Complete setup or repair protected secrets.`

Current environment overrides:

```text
BIOMETRIC_SYNC_PROGRAMDATA
BIOMETRIC_SYNC_CONFIG_DIR
BIOMETRIC_SYNC_SERVICE_EXE
```

Legacy compatibility aliases:

```text
FPF_BIOMETRIC_PROGRAMDATA
FPF_BIOMETRIC_CONFIG_DIR
FPF_BIOMETRIC_SERVICE_EXE
C:\ProgramData\FPF\BiometricSync
FPF-Biometric-Sync-Service.exe
```

These legacy values are supported temporarily for existing installations and should not be used for new setup.

The manager disables manual sync while the service is running. Stop the service first, then use **Run Sync Now** if an operator needs a one-cycle manual sync.

## Later Work

- Validate the first Inno Setup installer on a clean test machine.
- Decide whether to add an explicit ProgramData cleanup option in a later installer version.
- Add code signing before wider rollout.

See [Production Windows Installer](installer.md) for the current installer behavior and build procedure.
