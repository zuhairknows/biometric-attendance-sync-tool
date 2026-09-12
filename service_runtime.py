"""Runtime path setup for the packaged FPF biometric Windows service."""

import importlib
import os
import sys
from pathlib import Path


PROGRAMDATA_ROOT = Path(os.environ.get("FPF_BIOMETRIC_PROGRAMDATA", r"C:\ProgramData\FPF\BiometricSync"))


def is_frozen_runtime():
    return bool(getattr(sys, "frozen", False))


def get_application_root():
    if is_frozen_runtime():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_external_config_dir():
    override = os.environ.get("FPF_BIOMETRIC_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen_runtime():
        return PROGRAMDATA_ROOT / "config"
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
    config = importlib.import_module("local_config")
    if is_frozen_runtime():
        logs_directory = Path(str(getattr(config, "LOGS_DIRECTORY", "logs")))
        if not logs_directory.is_absolute():
            config.LOGS_DIRECTORY = str(PROGRAMDATA_ROOT / "logs")
    return config
