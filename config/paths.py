"""Centralized filesystem paths for customer-specific runtime data."""

from pathlib import Path

import runtime_paths

CONFIG_FILE_NAME = "config.json"
STATE_FILE_NAME = "state.json"


def get_app_data_dir():
    return runtime_paths.resolve_programdata_root()


def get_active_app_data_dir():
    return runtime_paths.resolve_active_programdata_root()


def get_config_path():
    return get_active_app_data_dir() / CONFIG_FILE_NAME


def get_state_dir():
    return get_active_app_data_dir() / "state"


def get_state_path():
    return get_state_dir() / STATE_FILE_NAME


def get_logs_dir():
    return get_active_app_data_dir() / "logs"


def get_retry_dir():
    return get_active_app_data_dir() / "retry"


def get_secrets_dir():
    return get_active_app_data_dir() / "secrets"


def get_legacy_config_dir():
    override = runtime_paths.get_config_dir_override()
    if override:
        return override
    return get_active_app_data_dir() / "config"


def ensure_directory(path):
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def ensure_runtime_directories():
    """Create mutable runtime folders from an explicit setup/startup call."""
    return {
        "app_data": ensure_directory(get_active_app_data_dir()),
        "logs": ensure_directory(get_logs_dir()),
        "state": ensure_directory(get_state_dir()),
        "retry": ensure_directory(get_retry_dir()),
        "secrets": ensure_directory(get_secrets_dir()),
    }
