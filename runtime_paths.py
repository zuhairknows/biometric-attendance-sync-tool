"""Shared runtime location resolution for the manager and the packaged Windows service.

Resolution order for every override is:

1. the current BIOMETRIC_SYNC_* environment variable,
2. the legacy FPF_BIOMETRIC_* variable (compatibility only),
3. the generic product default.

An explicit ProgramData or config-directory environment override is
authoritative: no legacy location is probed while one is set. Automatic
legacy discovery runs only with no overrides at all, and only while the
generic root holds no real configuration. Empty or whitespace-only
environment values are treated as unset.
"""

import os
from pathlib import Path

# Primary product runtime locations and environment variables.
DEFAULT_PROGRAMDATA_ROOT = Path(r"C:\ProgramData\BiometricAttendanceSync")
PROGRAMDATA_ENV = "BIOMETRIC_SYNC_PROGRAMDATA"
CONFIG_DIR_ENV = "BIOMETRIC_SYNC_CONFIG_DIR"
SERVICE_EXE_ENV = "BIOMETRIC_SYNC_SERVICE_EXE"

# Compatibility values for installations created before the M2 de-personalization.
# They only keep existing deployments working during the transition release.
LEGACY_PROGRAMDATA_ROOT = Path(r"C:\ProgramData\FPF\BiometricSync")
LEGACY_PROGRAMDATA_ENV = "FPF_BIOMETRIC_PROGRAMDATA"
LEGACY_CONFIG_DIR_ENV = "FPF_BIOMETRIC_CONFIG_DIR"
LEGACY_SERVICE_EXE_ENV = "FPF_BIOMETRIC_SERVICE_EXE"

CONFIG_FILE_NAME = "local_config.py"


def _first_environment_value(names):
    for name in names:
        value = os.environ.get(name)
        if value:
            value = value.strip()
            if value:
                return value
    return None


def resolve_programdata_root():
    """Explicit environment override chain, else the generic default.

    Performs no filesystem discovery; see resolve_active_programdata_root().
    """
    override = _first_environment_value([PROGRAMDATA_ENV, LEGACY_PROGRAMDATA_ENV])
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_PROGRAMDATA_ROOT


def get_config_dir_override():
    override = _first_environment_value([CONFIG_DIR_ENV, LEGACY_CONFIG_DIR_ENV])
    if override:
        return Path(override).expanduser().resolve()
    return None


def get_service_exe_override():
    override = _first_environment_value([SERVICE_EXE_ENV, LEGACY_SERVICE_EXE_ENV])
    if override:
        return Path(override).expanduser()
    return None


def _has_real_config(config_dir):
    return (Path(config_dir) / CONFIG_FILE_NAME).is_file()


def resolve_active_programdata_root():
    """Return the ProgramData root the runtime should actually use.

    An explicit ProgramData environment override is authoritative: config,
    logs, state, and retry all stay under it and no legacy location is
    probed. Automatic legacy discovery runs only when no override is set:
    while the generic root holds no real configuration, an existing legacy
    configuration keeps the legacy root active for this compatibility
    release, so old installations without environment overrides keep
    working. No files are copied or migrated.
    """
    override = _first_environment_value([PROGRAMDATA_ENV, LEGACY_PROGRAMDATA_ENV])
    if override:
        return Path(override).expanduser().resolve()
    if _has_real_config(DEFAULT_PROGRAMDATA_ROOT / "config"):
        return DEFAULT_PROGRAMDATA_ROOT
    if _has_real_config(LEGACY_PROGRAMDATA_ROOT / "config"):
        return LEGACY_PROGRAMDATA_ROOT
    return DEFAULT_PROGRAMDATA_ROOT
