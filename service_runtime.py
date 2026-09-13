"""Runtime path setup for the packaged biometric attendance Windows service."""

import os
import sys
from pathlib import Path

import runtime_paths
from config.loader import load_config


def is_frozen_runtime():
    return bool(getattr(sys, "frozen", False))


def get_application_root():
    if is_frozen_runtime():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_programdata_root():
    return runtime_paths.resolve_active_programdata_root()


def get_external_config_dir():
    override = runtime_paths.get_config_dir_override()
    if override:
        return override
    if is_frozen_runtime():
        return get_programdata_root() / "config"
    return get_application_root()


def prepare_runtime_paths():
    app_root = get_application_root()
    config_dir = get_external_config_dir()
    if config_dir.exists():
        sys.path.insert(0, str(config_dir))
    if str(app_root) not in sys.path:
        sys.path.insert(1 if config_dir.exists() else 0, str(app_root))
    os.chdir(str(config_dir if config_dir.exists() else app_root))


def load_runtime_config():
    config = load_config()
    if is_frozen_runtime():
        logs_directory = Path(str(getattr(config, "LOGS_DIRECTORY", "logs")))
        if not logs_directory.is_absolute():
            config.LOGS_DIRECTORY = str(get_programdata_root() / "logs")
    return config
