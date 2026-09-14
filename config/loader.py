"""Configuration loader with JSON, legacy Python, then default precedence."""

import importlib
import json
import sys
from pathlib import Path

from . import paths
from .schema import (
    ConfigurationError,
    build_default_runtime_config,
    merge_with_defaults,
    normalize_legacy_runtime_config,
    to_legacy_runtime_config,
    validate_json_config,
)


def load_config(config_path=None, allow_legacy=True, paths_module=paths, secret_store=None):
    config_path = Path(config_path) if config_path is not None else paths_module.get_config_path()
    if config_path.is_file():
        return load_json_config(config_path, paths_module=paths_module, secret_store=secret_store)
    if allow_legacy:
        legacy_config = load_legacy_config(paths_module=paths_module)
        if legacy_config is not None:
            return legacy_config
    return build_default_runtime_config(paths_module=paths_module)


def load_json_config(config_path, paths_module=paths, secret_store=None):
    try:
        with Path(config_path).open("r", encoding="utf-8") as handle:
            raw_config = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            "Malformed JSON configuration in " + str(config_path) + ": " + exc.msg
        )

    validate_json_config(raw_config)
    merged_config = merge_with_defaults(raw_config)
    return to_legacy_runtime_config(
        merged_config,
        paths_module=paths_module,
        source="json",
        secret_store=secret_store,
    )


def load_legacy_config(paths_module=paths):
    if paths_module is not paths:
        legacy_config = sys.modules.get("local_config")
        if legacy_config is None:
            return None
        return normalize_legacy_runtime_config(legacy_config, paths_module=paths_module)

    try:
        legacy_config = importlib.import_module("local_config")
    except ModuleNotFoundError as exc:
        if exc.name == "local_config":
            return None
        raise
    return normalize_legacy_runtime_config(legacy_config, paths_module=paths_module)


def forget_cached_legacy_config():
    sys.modules.pop("local_config", None)
