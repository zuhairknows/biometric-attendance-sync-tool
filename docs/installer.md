# Production Windows Installer

The production installer is built with Inno Setup. It packages the PyInstaller Manager and Service builds into one Administrator-run `Setup.exe`.

## Installed Layout

Program files:

```text
C:\Program Files\FPF Biometric Sync\
    FPF-Biometric-Sync-Manager.exe
    _internal\...
    service\
        FPF-Biometric-Sync-Service.exe
        _internal\...
```

Mutable runtime data:

```text
C:\ProgramData\FPF\BiometricSync\
    config\
        local_config.py
        local_config.py.template
    logs\
    state\
    retry\
```

Never place credentials, logs, `status.json`, or retry dumps under Program Files.

## First-Time Configuration

The installer creates the ProgramData folder structure and installs a safe template:

```text
C:\ProgramData\FPF\BiometricSync\config\local_config.py.template
```

It does not create a real `local_config.py` automatically. Copy/review the template and create:

```text
C:\ProgramData\FPF\BiometricSync\config\local_config.py
```

If no real config exists, the service is installed and configured but left stopped.

## Service Setup

The installer runs:

```text
"C:\Program Files\FPF Biometric Sync\service\FPF-Biometric-Sync-Service.exe" install
sc config ERPNextBiometricPushService start= delayed-auto
sc failure ERPNextBiometricPushService reset= 86400 actions= restart/60000/restart/60000/restart/60000
```

If a real `local_config.py` exists, the installer starts the service. Template-only config is not enough to start the service.

## Upgrade Behavior

Before replacing files, the installer stops the service if it is present. It then updates Program Files binaries, preserves ProgramData, re-runs service registration/configuration, and starts the service only when real config exists.

The installer does not delete:

- `local_config.py`
- logs
- `status.json`
- retry dumps

## Uninstall Behavior

Uninstall stops and removes `ERPNextBiometricPushService`, removes Program Files binaries, removes shortcuts, and removes the installed application entry.

By default, it preserves:

```text
C:\ProgramData\FPF\BiometricSync\
```

This keeps production config, logs, state, and retry data available for reinstall or audit.

## Shortcuts

The installer creates a Start Menu shortcut:

```text
FPF Biometric Sync Manager
```

The desktop shortcut is optional and selected during installation.

## Build Procedure

Install PyInstaller and Inno Setup 6 on the build machine, then run from the repository root:

```text
powershell -ExecutionPolicy Bypass -File build\build_release.ps1
```

The script:

1. Cleans old Manager/Service build folders.
2. Builds the packaged Windows service.
3. Builds the packaged PyQt Manager.
4. Assembles `release\FPF Biometric Sync\`.
5. Invokes Inno Setup when `ISCC.exe` is available.

Expected final installer:

```text
release\installer\FPF-Biometric-Sync-Setup-0.1.0.exe
```

If Inno Setup is not installed, the staging folder is still created and the script exits with a warning.

## Installer Logging

Run Setup with logging:

```text
FPF-Biometric-Sync-Setup-0.1.0.exe /LOG="C:\Temp\FPF-Install.log"
```

No custom telemetry is included.

## FP1 Clean-Machine Validation

1. Back up `C:\ProgramData\FPF\BiometricSync`.
2. Uninstall/remove the current temporary test service.
3. Remove or rename `C:\FPF-Test\FPF-Biometric-Sync-Service`.
4. Do not delete ProgramData config/log/state.
5. Install using `Setup.exe`.
6. Verify files exist under `C:\Program Files\FPF Biometric Sync`.
7. Verify `sc qc ERPNextBiometricPushService`.
8. Verify startup is delayed automatic.
9. Verify recovery actions are configured.
10. Verify Manager opens from Start Menu.
11. Verify Manager detects the service.
12. Verify Manager reads existing ProgramData configuration/state.
13. Verify ERPNext test.
14. Verify device test.
15. Verify service starts.
16. Verify full sync reaches `Mission Accomplished!`.
17. Reboot Windows.
18. Verify service automatically returns to `RUNNING`.
19. Verify another `Mission Accomplished!` after reboot.
20. Test Apps & Features uninstall.
21. Verify service is removed.
22. Verify ProgramData remains preserved.

Do not perform destructive cleanup of existing ProgramData during this validation.
