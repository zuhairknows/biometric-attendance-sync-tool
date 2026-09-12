# Packaged Windows Service

The old development service installation used the Python interpreter and `pythonservice.exe` from the active virtual environment. On the FP1 Windows PC this installed successfully, but service startup failed with Windows Error 1053 because `pythonservice.exe` could not load `python311.dll` under the LocalSystem account.

Do not fix that production failure by manually copying Python DLLs. The production direction is a self-contained PyInstaller service build that does not depend on Git, a virtual environment, user-profile Python, or Python being installed on the target PC.

## Architecture

```text
FPF Biometric Sync Manager
    |
    +-- controls ERPNextBiometricPushService
            |
            +-- FPF-Biometric-Sync-Service.exe
                    |
                    +-- existing erpnext_sync logic
```

The sync engine behavior remains in `erpnext_sync.py`.

## Build Command

From the repository root:

```text
python -m PyInstaller build\service.spec
```

The initial build is one-folder for easier validation:

```text
dist\FPF-Biometric-Sync-Service\FPF-Biometric-Sync-Service.exe
```

The service spec includes pywin32 service modules, including `win32timezone`, plus the sync engine dependencies `requests`, `zk`, and `pickledb`.

## Runtime Layout

The installer phase should place program files under:

```text
C:\Program Files\FPF Biometric Sync\
```

Mutable runtime files should live under:

```text
C:\ProgramData\FPF\BiometricSync\
```

Planned subfolders:

```text
config\
logs\
state\
retry\
```

For packaged service testing, place external configuration at:

```text
C:\ProgramData\FPF\BiometricSync\config\local_config.py
```

Do not bundle `local_config.py` into the executable or distribution folder. It contains credentials.

If packaged `local_config.py` uses a relative `LOGS_DIRECTORY`, the service runtime resolves it to:

```text
C:\ProgramData\FPF\BiometricSync\logs
```

Using an absolute `LOGS_DIRECTORY` in production config is also acceptable.

## Service Commands

The packaged executable is designed to support pywin32 service commands:

```text
FPF-Biometric-Sync-Service.exe install
FPF-Biometric-Sync-Service.exe remove
FPF-Biometric-Sync-Service.exe start
FPF-Biometric-Sync-Service.exe stop
FPF-Biometric-Sync-Service.exe debug
```

The service name remains:

```text
ERPNextBiometricPushService
```

The display name remains:

```text
ERPNext Biometric Push Service
```

## FP1 Validation Procedure

1. Build the one-folder service with `python -m PyInstaller build\service.spec`.
2. Confirm this file exists:

   ```text
   dist\FPF-Biometric-Sync-Service\FPF-Biometric-Sync-Service.exe
   ```

3. Create `C:\ProgramData\FPF\BiometricSync\config\local_config.py` from the reviewed FP1 config.
4. Confirm the config points logs to a LocalSystem-writable location, preferably `C:\ProgramData\FPF\BiometricSync\logs`.
5. Run debug mode where practical:

   ```text
   dist\FPF-Biometric-Sync-Service\FPF-Biometric-Sync-Service.exe debug
   ```

6. Confirm service startup reaches the Python service code and writes logs under ProgramData.
7. Do not install the packaged service on the development PC during build verification.

## Remaining Installer Work

- Install program files under `C:\Program Files\FPF Biometric Sync\`.
- Create `C:\ProgramData\FPF\BiometricSync\config`, `logs`, `state`, and `retry`.
- Copy or prompt for external `local_config.py` without embedding credentials.
- Register `ERPNextBiometricPushService` using the packaged service executable.
- Ensure uninstall removes service registration but preserves config, logs, state, and retry files.
- Package the manager and service together in the final Setup.exe.
