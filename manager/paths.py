import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import runtime_paths
from config import paths as config_paths

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = PROJECT_ROOT / "erpnext_sync_win.py"
# Primary packaged service executable name. Keep the legacy value only for one
# compatibility release so existing staged/package layouts can still be found.
SERVICE_EXE_NAME = "Biometric-Attendance-Sync-Service.exe"
LEGACY_SERVICE_EXE_NAME = "FPF-Biometric-Sync-Service.exe"


@dataclass(frozen=True)
class ServiceRuntime:
    runtime_type: str
    executable: Path
    command_prefix: tuple
    source: str

    @property
    def is_packaged(self):
        return self.runtime_type == "packaged"


def is_frozen_app():
    return bool(getattr(sys, "frozen", False))


def get_app_root():
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def get_programdata_root():
    return config_paths.get_active_app_data_dir()


def get_programdata_config_folder():
    return get_programdata_root() / "config"


def get_programdata_logs_folder():
    return config_paths.get_logs_dir()


def get_programdata_state_folder():
    return config_paths.get_state_dir()


def get_programdata_retry_folder():
    return config_paths.get_retry_dir()


def get_programdata_secrets_folder():
    return config_paths.get_secrets_dir()


def get_programdata_backups_folder():
    return config_paths.get_backups_dir()


def get_programdata_diagnostics_folder():
    return config_paths.get_diagnostics_dir()


def get_app_data_folder():
    return config_paths.get_active_app_data_dir()


def uses_packaged_service_runtime():
    return resolve_service_runtime().is_packaged


def get_config_folder():
    override = runtime_paths.get_config_dir_override()
    if override:
        return override
    if is_frozen_app() or uses_packaged_service_runtime():
        return get_programdata_config_folder()
    return PROJECT_ROOT


def _existing_packaged_service_candidates():
    override = runtime_paths.get_service_exe_override()
    if override is not None and override.exists():
        yield "environment", override.resolve()

    app_root = get_app_root()
    candidates = [
        ("packaged-manager", app_root / "service" / SERVICE_EXE_NAME),
        ("packaged-manager", app_root / SERVICE_EXE_NAME),
        ("packaged-manager", app_root / "Biometric-Attendance-Sync-Service" / SERVICE_EXE_NAME),
        ("legacy-packaged-manager", app_root / "service" / LEGACY_SERVICE_EXE_NAME),
        ("legacy-packaged-manager", app_root / LEGACY_SERVICE_EXE_NAME),
        ("legacy-packaged-manager", app_root / "FPF-Biometric-Sync-Service" / LEGACY_SERVICE_EXE_NAME),
    ]
    for source, candidate in candidates:
        if candidate.exists():
            yield source, candidate.resolve()


def resolve_service_runtime(python_executable=None):
    for source, service_exe in _existing_packaged_service_candidates():
        return ServiceRuntime(
            runtime_type="packaged",
            executable=service_exe,
            command_prefix=(str(service_exe),),
            source=source,
        )

    python_executable = python_executable or sys.executable
    return ServiceRuntime(
        runtime_type="development",
        executable=Path(python_executable),
        command_prefix=(str(python_executable), str(SERVICE_SCRIPT)),
        source="development",
    )


def get_packaged_service_executable():
    runtime = resolve_service_runtime()
    if runtime.is_packaged:
        return runtime.executable
    return None


def resolve_from_project(path_value):
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def get_logs_folder(config_module=None):
    production_paths = is_frozen_app() or uses_packaged_service_runtime()
    if config_module is None and production_paths:
        return get_programdata_logs_folder()
    logs_directory = "logs"
    if config_module is not None:
        logs_directory = getattr(config_module, "LOGS_DIRECTORY", logs_directory)
        if production_paths and not Path(str(logs_directory)).is_absolute():
            return (get_programdata_root() / str(logs_directory)).resolve()
    return resolve_from_project(logs_directory)


def get_status_file(config_module=None):
    return get_logs_folder(config_module) / "status.json"


def get_manager_log_file(config_module=None):
    return get_logs_folder(config_module) / "manager.log"


def open_folder(path):
    folder = Path(path).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(folder))
    else:
        subprocess.Popen(["xdg-open", str(folder)])
