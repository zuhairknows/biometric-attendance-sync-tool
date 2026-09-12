import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = PROJECT_ROOT / "erpnext_sync_win.py"
CONFIG_FOLDER = PROJECT_ROOT


def resolve_from_project(path_value):
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def get_logs_folder(config_module=None):
    logs_directory = "logs"
    if config_module is not None:
        logs_directory = getattr(config_module, "LOGS_DIRECTORY", logs_directory)
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

