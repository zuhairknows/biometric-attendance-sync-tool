"""Versioned JSON configuration validation and legacy-shape conversion."""

import datetime
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

SCHEMA_VERSION = 1
DEFAULT_ZK_PORT = 4370
DEFAULT_ZK_PASSWORD = 0
DEVICE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')
PLACEHOLDER_CREDENTIALS = {'YOUR_API_KEY', 'YOUR_API_SECRET', 'YOUR_REAL_API_KEY', 'YOUR_REAL_API_SECRET'}
SENSITIVE_KEYS = {
    "api_key",
    "api_secret",
    "api_token",
    "password",
    "api_key_ref",
    "api_secret_ref",
    "password_ref",
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


def normalize_legacy_runtime_config(legacy_config, paths_module=None):
    if paths_module is None:
        from . import paths as paths_module

    runtime = SimpleNamespace()
    runtime.CONFIG_SOURCE = "legacy"
    runtime.ERPNEXT_URL = str(getattr(legacy_config, "ERPNEXT_URL", "") or "").rstrip("/")
    runtime.ERPNEXT_API_KEY = str(getattr(legacy_config, "ERPNEXT_API_KEY", "") or "")
    runtime.ERPNEXT_API_SECRET = str(getattr(legacy_config, "ERPNEXT_API_SECRET", "") or "")
    runtime.ERPNEXT_VERSION = int(getattr(legacy_config, "ERPNEXT_VERSION", 14))
    runtime.ERPNEXT_VERIFY_SSL = bool(getattr(legacy_config, "ERPNEXT_VERIFY_SSL", True))
    runtime.ERPNEXT_REQUEST_TIMEOUT = int(getattr(legacy_config, "ERPNEXT_REQUEST_TIMEOUT", getattr(legacy_config, "REQUEST_TIMEOUT", 30)))
    runtime.REQUEST_TIMEOUT = runtime.ERPNEXT_REQUEST_TIMEOUT
    runtime.PULL_FREQUENCY = int(getattr(legacy_config, "PULL_FREQUENCY", 0))
    runtime.IMPORT_START_DATE = getattr(legacy_config, "IMPORT_START_DATE", None)
    logs_directory = getattr(legacy_config, "LOGS_DIRECTORY", None)
    runtime.LOGS_DIRECTORY = str(logs_directory or paths_module.get_logs_dir())
    default_state_path = Path(runtime.LOGS_DIRECTORY) / "status.json" if logs_directory else paths_module.get_state_path()
    runtime.STATE_FILE_PATH = str(getattr(legacy_config, "STATE_FILE_PATH", default_state_path))
    runtime.RETRY_DIRECTORY = str(getattr(legacy_config, "RETRY_DIRECTORY", runtime.LOGS_DIRECTORY))
    runtime.SECRETS_DIRECTORY = str(getattr(legacy_config, "SECRETS_DIRECTORY", paths_module.get_secrets_dir()))
    runtime.devices = deepcopy(getattr(legacy_config, "devices", []))
    runtime.shift_type_device_mapping = deepcopy(getattr(legacy_config, "shift_type_device_mapping", []))
    runtime.allowed_exceptions = deepcopy(getattr(legacy_config, "allowed_exceptions", [1, 2, 3]))
    runtime.device_punch_values_IN = deepcopy(getattr(legacy_config, "device_punch_values_IN", [0, 4]))
    runtime.device_punch_values_OUT = deepcopy(getattr(legacy_config, "device_punch_values_OUT", [1, 5]))
    return runtime


def validate_runtime_config(config_module):
    errors = []

    for key in ['ERPNEXT_URL', 'ERPNEXT_API_KEY', 'ERPNEXT_API_SECRET', 'LOGS_DIRECTORY']:
        if not str(getattr(config_module, key, '')).strip():
            errors.append(key + ' is required.')

    erpnext_url = str(getattr(config_module, 'ERPNEXT_URL', '')).strip()
    if erpnext_url and not erpnext_url.startswith(('http://', 'https://')):
        errors.append('ERPNEXT_URL must start with http:// or https://.')

    for key in ['ERPNEXT_API_KEY', 'ERPNEXT_API_SECRET']:
        if is_placeholder_credential(getattr(config_module, key, '')):
            errors.append(key + ' must be set to the real local credential.')

    try:
        pull_frequency = int(getattr(config_module, 'PULL_FREQUENCY', 0))
        if pull_frequency <= 0:
            errors.append('PULL_FREQUENCY must be greater than 0.')
    except (TypeError, ValueError):
        errors.append('PULL_FREQUENCY must be a positive integer.')

    devices = getattr(config_module, 'devices', None)
    if not isinstance(devices, list) or not devices:
        errors.append('devices must be a non-empty list.')
    else:
        try:
            validate_unique_device_ids(devices)
        except ValueError as exc:
            errors.append(str(exc))
        for index, device in enumerate(devices):
            try:
                normalize_runtime_device_config(device)
            except ValueError as exc:
                errors.append('devices[' + str(index) + ']: ' + str(exc))

    if errors:
        raise ConfigurationError('Invalid configuration:\n- ' + '\n- '.join(errors))

    return True


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


def to_legacy_runtime_config(config_data, paths_module=None, source="json", secret_store=None):
    if paths_module is None:
        from . import paths as paths_module

    erpnext = config_data["erpnext"]
    sync = config_data["sync"]
    logging_config = config_data["logging"]

    runtime = SimpleNamespace()
    runtime.SCHEMA_VERSION = config_data["schema_version"]
    runtime.CONFIG_SOURCE = source
    runtime.CONFIG_USES_PROTECTED_SECRETS = bool(erpnext.get("api_key_ref") or erpnext.get("api_secret_ref"))
    runtime.CONFIG_USES_PLAINTEXT_SECRETS = bool(erpnext.get("api_key") or erpnext.get("api_secret"))
    runtime.ERPNEXT_URL = str(erpnext.get("url") or "").rstrip("/")
    runtime.ERPNEXT_API_KEY = _resolve_secret_value(
        erpnext,
        "api_key",
        "api_key_ref",
        secret_store,
        paths_module,
    ) or str(erpnext.get("api_user") or "")
    runtime.ERPNEXT_API_SECRET = _resolve_secret_value(
        erpnext,
        "api_secret",
        "api_secret_ref",
        secret_store,
        paths_module,
    )
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
    runtime.devices = _resolve_device_secrets(config_data.get("devices", []), secret_store, paths_module)
    runtime.shift_type_device_mapping = deepcopy(config_data.get("shift_type_device_mapping", []))
    runtime.allowed_exceptions = deepcopy(config_data.get("allowed_exceptions", [1, 2, 3]))
    runtime.device_punch_values_IN = deepcopy(config_data.get("device_punch_values_IN", [0, 4]))
    runtime.device_punch_values_OUT = deepcopy(config_data.get("device_punch_values_OUT", [1, 5]))
    return runtime


def normalize_runtime_device_config(device):
    device_id = device.get('device_id')
    ip = device.get('ip') or device.get('host')
    if not device_id:
        raise ValueError('Device configuration is missing required device_id.')
    validate_device_id(device_id)
    if not ip:
        raise ValueError('Device configuration for device_id ' + str(device_id) + ' is missing required ip or host.')

    normalized_device = dict(device)
    normalized_device['device_id'] = str(device_id)
    normalized_device['ip'] = ip
    normalized_device['port'] = validate_port(device.get('port', DEFAULT_ZK_PORT))
    normalized_device['password'] = validate_password(device.get('password', DEFAULT_ZK_PASSWORD))
    normalized_device['enabled'] = bool(device.get('enabled', True))
    normalized_device['punch_direction'] = device.get('punch_direction')
    normalized_device['clear_from_device_on_fetch'] = bool(device.get('clear_from_device_on_fetch', False))
    normalized_device['latitude'] = device.get('latitude')
    normalized_device['longitude'] = device.get('longitude')
    return normalized_device


def validate_device_id(device_id):
    device_id = str(device_id)
    if not DEVICE_ID_PATTERN.match(device_id):
        raise ValueError('Device ID ' + device_id + ' is invalid. Use only letters, numbers, underscore, and hyphen.')
    return device_id


def validate_unique_device_ids(devices):
    seen_device_ids = set()
    duplicate_device_ids = []
    for device in devices:
        device_id = device.get('device_id')
        if not device_id:
            continue
        try:
            device_id = validate_device_id(device_id)
        except ValueError:
            continue
        if device_id in seen_device_ids:
            duplicate_device_ids.append(device_id)
        seen_device_ids.add(device_id)
    if duplicate_device_ids:
        raise ValueError('Duplicate device_id values found: ' + ', '.join(sorted(set(duplicate_device_ids))))


def validate_port(port):
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError('Device port must be an integer between 1 and 65535.')
    if port < 1 or port > 65535:
        raise ValueError('Device port must be between 1 and 65535.')
    return port


def validate_password(password):
    try:
        return int(password)
    except (TypeError, ValueError):
        raise ValueError('Device password must be an integer. Use 0 when the device has no connection password.')


def is_placeholder_credential(value):
    return str(value).strip() in PLACEHOLDER_CREDENTIALS


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

    _validate_secret_or_ref(erpnext, "api_key", "api_key_ref", "erpnext.api_key", errors, fallback_key="api_user")
    _validate_secret_or_ref(erpnext, "api_secret", "api_secret_ref", "erpnext.api_secret", errors)

    _validate_positive_int(
        erpnext.get("request_timeout_seconds", 30),
        "erpnext.request_timeout_seconds",
        errors,
    )


def _validate_devices(devices, errors):
    if not devices:
        errors.append("devices must contain at least one device.")
        return
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
        password_ref = device.get("password_ref")
        if password_ref not in (None, ""):
            try:
                from .secrets import validate_secret_id

                validate_secret_id(password_ref)
            except Exception:
                errors.append(prefix + ".password_ref is invalid.")


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


def _validate_secret_or_ref(section, value_key, ref_key, label, errors, fallback_key=None):
    value = section.get(value_key)
    ref = section.get(ref_key)
    fallback_value = section.get(fallback_key) if fallback_key else None
    if str(value or fallback_value or "").strip():
        return
    if str(ref or "").strip():
        try:
            from .secrets import validate_secret_id

            validate_secret_id(ref)
        except Exception:
            errors.append(label + "_ref is invalid.")
        return
    errors.append(label + " is required.")


def _resolve_secret_value(section, value_key, ref_key, secret_store, paths_module):
    ref = section.get(ref_key)
    if str(ref or "").strip():
        store = secret_store or _default_secret_store(paths_module)
        return store.get_secret(ref)
    return str(section.get(value_key) or "")


def _resolve_device_secrets(devices, secret_store, paths_module):
    resolved_devices = deepcopy(devices)
    for device in resolved_devices:
        ref = device.get("password_ref")
        if str(ref or "").strip():
            store = secret_store or _default_secret_store(paths_module)
            device["password"] = store.get_secret(ref)
    return resolved_devices


def _default_secret_store(paths_module):
    from .secrets import create_secret_store

    return create_secret_store(paths_module=paths_module)


def _to_legacy_import_start_date(value):
    if value in (None, ""):
        return None
    parsed = datetime.datetime.strptime(str(value), "%Y-%m-%d")
    return parsed.strftime("%Y%m%d")
