import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = PROJECT_ROOT / "erpnext_sync_win.py"
SERVICE_EXE_NAME = "FPF-Biometric-Sync-Service.exe"
CONFIG_FOLDER = PROJECT_ROOT
PROGRAM_DATA_ROOT = Path(os.environ.get("FPF_BIOMETRIC_PROGRAMDATA", r"C:\ProgramData\FPF\BiometricSync"))


def is_frozen_app():
    return bool(getattr(sys, "frozen", False))


def get_app_root():
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


def get_programdata_root():
    return PROGRAM_DATA_ROOT


def get_programdata_config_folder():
    return get_programdata_root() / "config"


def get_programdata_logs_folder():
    return get_programdata_root() / "logs"


def get_programdata_state_folder():
    return get_programdata_root() / "state"


def get_programdata_retry_folder():
    return get_programdata_root() / "retry"


def get_packaged_service_executable():
    override = os.environ.get("FPF_BIOMETRIC_SERVICE_EXE")
    if override:
        path = Path(override).expanduser()
        if path.exists():
            return path.resolve()

    app_root = get_app_root()
    candidates = [
        app_root / SERVICE_EXE_NAME,
        app_root / "FPF-Biometric-Sync-Service" / SERVICE_EXE_NAME,
        PROJECT_ROOT / "dist" / "FPF-Biometric-Sync-Service" / SERVICE_EXE_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def resolve_from_project(path_value):
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def get_logs_folder(config_module=None):
    if config_module is None and is_frozen_app():
        return get_programdata_logs_folder()
    logs_directory = "logs"
    if config_module is not None:
        logs_directory = getattr(config_module, "LOGS_DIRECTORY", logs_directory)
        if is_frozen_app() and not Path(str(logs_directory)).is_absolute():
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
