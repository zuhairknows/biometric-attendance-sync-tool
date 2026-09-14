"""Configuration status helpers for first-run setup and service safety."""

from dataclasses import dataclass, field
import sys

from . import paths
from .loader import load_config
from .schema import validate_runtime_config

UNCONFIGURED = "UNCONFIGURED"
CONFIGURED = "CONFIGURED"
LEGACY_CONFIGURED = "LEGACY_CONFIGURED"
INVALID = "INVALID"


@dataclass(frozen=True)
class ConfigurationStatus:
    state: str
    source: str = ""
    message: str = ""
    details: list = field(default_factory=list)

    @property
    def configured(self):
        return self.state in (CONFIGURED, LEGACY_CONFIGURED)

    @property
    def legacy(self):
        return self.state == LEGACY_CONFIGURED


def get_configuration_status(paths_module=paths):
    config_path = paths_module.get_config_path()
    if config_path.is_file():
        try:
            runtime_config = load_config(config_path=config_path, allow_legacy=False, paths_module=paths_module)
            validate_runtime_config(runtime_config)
            return ConfigurationStatus(CONFIGURED, "json", "Configured")
        except Exception as exc:
            return ConfigurationStatus(INVALID, "json", "Invalid configuration", _safe_details(exc))

    legacy_dir = getattr(paths_module, "get_legacy_config_dir", lambda: None)()
    if legacy_dir is not None and legacy_dir.exists() and str(legacy_dir) not in sys.path:
        sys.path.insert(0, str(legacy_dir))

    try:
        legacy_config = load_config(allow_legacy=True, paths_module=paths_module)
    except Exception as exc:
        return ConfigurationStatus(INVALID, "legacy", "Invalid legacy configuration", _safe_details(exc))

    source = getattr(legacy_config, "CONFIG_SOURCE", "")
    if source == "legacy":
        try:
            validate_runtime_config(legacy_config)
            return ConfigurationStatus(LEGACY_CONFIGURED, "legacy", "Legacy Configuration")
        except Exception as exc:
            return ConfigurationStatus(INVALID, "legacy", "Invalid legacy configuration", _safe_details(exc))
    return ConfigurationStatus(UNCONFIGURED, "defaults", "Product is not configured. Complete first-run setup.")


def is_configured(paths_module=paths):
    return get_configuration_status(paths_module=paths_module).configured


def _safe_details(exc):
    message = str(exc)
    if not message:
        return []
    details = []
    for line in message.splitlines():
        line = line.strip()
        if line.startswith("- "):
            details.append(line)
    return details or [message.replace("\n", " ")]
