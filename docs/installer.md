# Production Windows Installer

The production installer is built with Inno Setup. It packages the PyInstaller Manager and Service builds into one Administrator-run setup executable.

## Installed Layout

Program files:

```text
C:\Program Files\Biometric Attendance Sync\
    Biometric-Attendance-Sync-Manager.exe
    _internal\...
    service\
        Biometric-Attendance-Sync-Service.exe
        _internal\...
```

Mutable runtime data:

```text
C:\ProgramData\BiometricAttendanceSync\
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
C:\ProgramData\BiometricAttendanceSync\config\local_config.py.template
```

It does not create a real `local_config.py` automatically. Copy/review the template and create:

```text
C:\ProgramData\BiometricAttendanceSync\config\local_config.py
```

If no real config exists, the service is installed and configured but left stopped.

## Service Setup

The installer runs:

```text
"C:\Program Files\Biometric Attendance Sync\service\Biometric-Attendance-Sync-Service.exe" install
sc config ERPNextBiometricPushService start= delayed-auto
sc failure ERPNextBiometricPushService reset= 86400 actions= restart/60000/restart/60000/restart/60000
```

If a real `local_config.py` exists, the installer starts the service. Template-only config is not enough to start the service.

The Windows service name remains:

```text
ERPNextBiometricPushService
```

The Inno AppId remains stable for upgrade compatibility:

```text
{F3E3DD71-B7E0-4CF5-A1BF-25F100100100}
```

## Upgrade Behavior

Before replacing files, the installer stops the service if it is present. It then updates Program Files binaries, preserves ProgramData, re-runs service registration/configuration, and starts the service only when real config exists.

The installer does not delete:

- `local_config.py`
- logs
- `status.json`
- retry dumps

Changing `DefaultDirName` does not add custom destructive migration logic. Existing installs should remain upgradeable through the unchanged AppId; test the exact install-location behavior during a controlled upgrade rehearsal.

## Uninstall Behavior

Uninstall stops and removes `ERPNextBiometricPushService`, removes Program Files binaries, removes shortcuts, and removes the installed application entry.

By default, it preserves:

```text
C:\ProgramData\BiometricAttendanceSync\
```

This keeps production config, logs, state, and retry data available for reinstall or audit.

Legacy ProgramData from older builds is not deleted or migrated by the installer:

```text
C:\ProgramData\FPF\BiometricSync\
```

Runtime compatibility may still read that legacy location when no new config exists.

## Shortcuts

The installer creates a Start Menu shortcut:

```text
Biometric Attendance Sync Manager
```

The desktop shortcut is optional and selected during installation.

## Build Procedure

Install PyInstaller and Inno Setup 6 on the build machine, then run from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\build\build_release.ps1
```

The script:

1. Cleans current generated Manager/Service build folders.
2. Cleans known stale legacy artifact folders from older build names.
3. Builds the packaged Windows service.
4. Builds the packaged PyQt Manager.
5. Assembles `release\Biometric Attendance Sync\`.
6. Invokes Inno Setup when `ISCC.exe` is available.

Expected final installer:

```text
release\installer\Biometric-Attendance-Sync-Setup-0.1.0.exe
```

If Inno Setup is not installed, the staging folder is still created and the script exits with a warning.

## Installer Logging

Run Setup with logging:

```powershell
.\Biometric-Attendance-Sync-Setup-0.1.0.exe /LOG="C:\Temp\Biometric-Attendance-Sync-Install.log"
```

No custom telemetry is included.

## Clean-Machine Validation

1. Back up `C:\ProgramData\BiometricAttendanceSync`.
2. Uninstall/remove any previous test service if required.
3. Do not delete ProgramData config/log/state.
4. Install using `Biometric-Attendance-Sync-Setup-0.1.0.exe`.
5. Verify files exist under `C:\Program Files\Biometric Attendance Sync`.
6. Verify `sc qc ERPNextBiometricPushService`.
7. Verify startup is delayed automatic.
8. Verify recovery actions are configured.
9. Verify Manager opens from Start Menu.
10. Verify Manager detects the service.
11. Verify Manager reads existing ProgramData configuration/state.
12. Verify ERPNext test.
13. Verify device test.
14. Verify service starts.
15. Verify a full sync reaches `Mission Accomplished!`.
16. Reboot Windows.
17. Verify service automatically returns to `RUNNING`.
18. Verify another `Mission Accomplished!` after reboot.
19. Test Apps & Features uninstall.
20. Verify service is removed.
21. Verify ProgramData remains preserved.

Do not perform destructive cleanup of existing ProgramData during this validation.
