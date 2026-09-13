"""Versioned JSON configuration validation and legacy-shape conversion."""

import datetime
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlparse

SCHEMA_VERSION = 1
SENSITIVE_KEYS = {
    "api_key",
    "api_secret",
    "api_token",
    "password",
    "secret",
    "secret_ref",
}

SAFE_DEFAULTS = {
    "schema_version": SCHEMA_VERSION,
    "erpnext": {
        "url": "",
        "api_key": "",
        "api_secret": "",
        "verify_ssl": True,
        "request_timeout_seconds": 30,
        "version": 15,
    },
    "devices": [],
    "sync": {
        "pull_frequency_minutes": 60,
        "import_start_date": None,
    },
    "logging": {
        "level": "INFO",
        "retention_days": 30,
    },
}


class ConfigurationError(ValueError):
    pass


def build_default_runtime_config(paths_module=None):
    config = deepcopy(SAFE_DEFAULTS)
    return to_legacy_runtime_config(config, paths_module=paths_module, source="defaults")


def validate_json_config(config_data):
    errors = []
    if not isinstance(config_data, dict):
        raise ConfigurationError("Configuration must be a JSON object.")

    schema_version = config_data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ConfigurationError(
            "Unsupported schema_version "
            + str(schema_version)
            + ". Supported schema_version is "
            + str(SCHEMA_VERSION)
            + "."
        )

    erpnext = config_data.get("erpnext", {})
    if not isinstance(erpnext, dict):
        errors.append("erpnext must be an object.")
        erpnext = {}
    _validate_erpnext(erpnext, errors)

    devices = config_data.get("devices", [])
    if not isinstance(devices, list):
        errors.append("devices must be a list.")
        devices = []
    _validate_devices(devices, errors)

    sync = config_data.get("sync", {})
    if not isinstance(sync, dict):
        errors.append("sync must be an object.")
        sync = {}
    _validate_sync(sync, errors)

    logging_config = config_data.get("logging", {})
    if logging_config is not None and not isinstance(logging_config, dict):
        errors.append("logging must be an object.")

    if errors:
        raise ConfigurationError("Invalid configuration:\n- " + "\n- ".join(errors))

    return True


def merge_with_defaults(config_data):
    merged = deepcopy(SAFE_DEFAULTS)
    for section in ("erpnext", "sync", "logging"):
        if isinstance(config_data.get(section), dict):
            merged[section].update(config_data[section])
    if "devices" in config_data:
        merged["devices"] = deepcopy(config_data["devices"])
    merged["schema_version"] = config_data.get("schema_version", SCHEMA_VERSION)
    return merged


def to_legacy_runtime_config(config_data, paths_module=None, source="json"):
    if paths_module is None:
        from . import paths as paths_module

    erpnext = config_data["erpnext"]
    sync = config_data["sync"]
    logging_config = config_data["logging"]

    runtime = SimpleNamespace()
    runtime.SCHEMA_VERSION = config_data["schema_version"]
    runtime.CONFIG_SOURCE = source
    runtime.ERPNEXT_URL = str(erpnext.get("url") or "").rstrip("/")
    runtime.ERPNEXT_API_KEY = str(erpnext.get("api_key") or erpnext.get("api_user") or "")
    runtime.ERPNEXT_API_SECRET = str(erpnext.get("api_secret") or "")
    runtime.ERPNEXT_VERIFY_SSL = erpnext.get("verify_ssl", True)
    runtime.ERPNEXT_VERSION = int(erpnext.get("version", 15))
    runtime.ERPNEXT_REQUEST_TIMEOUT = int(erpnext.get("request_timeout_seconds", 30))
    runtime.REQUEST_TIMEOUT = runtime.ERPNEXT_REQUEST_TIMEOUT
    runtime.PULL_FREQUENCY = int(sync.get("pull_frequency_minutes", 60))
    runtime.IMPORT_START_DATE = _to_legacy_import_start_date(sync.get("import_start_date"))
    runtime.LOG_LEVEL = str(logging_config.get("level", "INFO"))
    runtime.LOG_RETENTION_DAYS = int(logging_config.get("retention_days", 30))
    runtime.LOGS_DIRECTORY = str(paths_module.get_logs_dir())
    runtime.STATE_FILE_PATH = str(paths_module.get_state_path())
    runtime.RETRY_DIRECTORY = str(paths_module.get_retry_dir())
    runtime.SECRETS_DIRECTORY = str(paths_module.get_secrets_dir())
    runtime.devices = deepcopy(config_data.get("devices", []))
    runtime.shift_type_device_mapping = deepcopy(config_data.get("shift_type_device_mapping", []))
    runtime.allowed_exceptions = deepcopy(config_data.get("allowed_exceptions", [1, 2, 3]))
    runtime.device_punch_values_IN = deepcopy(config_data.get("device_punch_values_IN", [0, 4]))
    runtime.device_punch_values_OUT = deepcopy(config_data.get("device_punch_values_OUT", [1, 5]))
    return runtime


def _validate_erpnext(erpnext, errors):
    url = str(erpnext.get("url") or "").strip()
    if not url:
        errors.append("erpnext.url is required.")
    else:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            errors.append("erpnext.url must be a valid http:// or https:// URL.")

    if not isinstance(erpnext.get("verify_ssl", True), bool):
        errors.append("erpnext.verify_ssl must be true or false.")

    _validate_positive_int(
        erpnext.get("request_timeout_seconds", 30),
        "erpnext.request_timeout_seconds",
        errors,
    )


def _validate_devices(devices, errors):
    seen = set()
    for index, device in enumerate(devices):
        prefix = "devices[" + str(index) + "]"
        if not isinstance(device, dict):
            errors.append(prefix + " must be an object.")
            continue
        device_id = str(device.get("device_id") or "").strip()
        if not device_id:
            errors.append(prefix + ".device_id is required.")
        elif device_id in seen:
            errors.append("Duplicate device_id values found: " + device_id)
        seen.add(device_id)

        if not str(device.get("name") or "").strip():
            errors.append(prefix + ".name is required.")
        if not str(device.get("ip") or device.get("host") or "").strip():
            errors.append(prefix + ".ip or host is required.")
        _validate_port(device.get("port", 4370), prefix + ".port", errors)
        if not isinstance(device.get("enabled", True), bool):
            errors.append(prefix + ".enabled must be true or false.")
        if not isinstance(device.get("clear_from_device_on_fetch", False), bool):
            errors.append(prefix + ".clear_from_device_on_fetch must be true or false.")


def _validate_sync(sync, errors):
    _validate_positive_int(sync.get("pull_frequency_minutes", 60), "sync.pull_frequency_minutes", errors)
    import_start_date = sync.get("import_start_date")
    if import_start_date in (None, ""):
        return
    try:
        datetime.datetime.strptime(str(import_start_date), "%Y-%m-%d")
    except ValueError:
        errors.append("sync.import_start_date must use YYYY-MM-DD.")


def _validate_positive_int(value, label, errors):
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors.append(label + " must be a positive integer.")
        return
    if number <= 0:
        errors.append(label + " must be greater than 0.")


def _validate_port(value, label, errors):
    try:
        port = int(value)
    except (TypeError, ValueError):
        errors.append(label + " must be an integer between 1 and 65535.")
        return
    if port < 1 or port > 65535:
        errors.append(label + " must be between 1 and 65535.")


def _to_legacy_import_start_date(value):
    if value in (None, ""):
        return None
    parsed = datetime.datetime.strptime(str(value), "%Y-%m-%d")
    return parsed.strftime("%Y%m%d")
