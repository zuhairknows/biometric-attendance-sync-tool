"""Validation and safe summary helpers for first-run setup."""

import copy

from config.schema import ConfigurationError, validate_json_config
from config.secrets import device_password_secret_id, erpnext_api_key_secret_id, erpnext_api_secret_secret_id


SECRET_MASK = "********"


def build_config_dict(setup_config):
    return {
        "schema_version": 1,
        "erpnext": {
            "url": setup_config.erpnext.url.strip(),
            "api_key": setup_config.erpnext.api_key.strip(),
            "api_secret": setup_config.erpnext.api_secret,
            "verify_ssl": bool(setup_config.erpnext.verify_ssl),
            "request_timeout_seconds": int(setup_config.erpnext.request_timeout_seconds),
        },
        "devices": [
            {
                "device_id": device.device_id.strip(),
                "name": device.name.strip(),
                "ip": device.ip.strip(),
                "port": int(device.port),
                "enabled": bool(device.enabled),
                "password": _normalize_device_password(device.password),
                "clear_from_device_on_fetch": bool(device.clear_from_device_on_fetch),
            }
            for device in setup_config.devices
        ],
        "sync": {
            "pull_frequency_minutes": int(setup_config.sync.pull_frequency_minutes),
            "import_start_date": setup_config.sync.import_start_date,
        },
        "logging": {
            "level": setup_config.logging_level,
            "retention_days": int(setup_config.logging_retention_days),
        },
    }


def validate_setup_config(setup_config):
    config_dict = build_config_dict(setup_config)
    _apply_existing_secret_refs_for_validation(config_dict, setup_config)
    validate_json_config(config_dict)
    return config_dict


def safe_review_summary(setup_config):
    enabled_devices = [device for device in setup_config.devices if device.enabled]
    device_test_states = getattr(setup_config, "device_test_states", {}) or {}
    return {
        "erpnext_url": setup_config.erpnext.url.strip(),
        "api_key": _mask_identifier(setup_config.erpnext.api_key),
        "api_secret": SECRET_MASK if setup_config.erpnext.api_secret else "",
        "verify_ssl": bool(setup_config.erpnext.verify_ssl),
        "erpnext_tested": bool(getattr(setup_config, "erpnext_tested", False)),
        "erpnext_ok": bool(getattr(setup_config, "erpnext_ok", False)),
        "device_count": len(setup_config.devices),
        "enabled_device_count": len(enabled_devices),
        "devices": [
            {
                "name": device.name.strip(),
                "device_id": device.device_id.strip(),
                "ip": device.ip.strip(),
                "port": int(device.port),
                "enabled": bool(device.enabled),
                "password": SECRET_MASK if str(device.password) not in ("", "0") else "",
                "test_state": device_test_states.get(device.device_id.strip(), "Not tested"),
            }
            for device in setup_config.devices
        ],
        "pull_frequency_minutes": int(setup_config.sync.pull_frequency_minutes),
        "import_start_date": setup_config.sync.import_start_date,
    }


def redact_error_message(message):
    return str(message).replace("\n", " ")


def clone_setup_config(setup_config):
    return copy.deepcopy(setup_config)


def _mask_identifier(value):
    value = str(value or "")
    if len(value) <= 4:
        return SECRET_MASK if value else ""
    return value[:2] + "..." + value[-2:]


def _normalize_device_password(value):
    if value in (None, ""):
        return ""
    return int(value)


def _apply_existing_secret_refs_for_validation(config_dict, setup_config):
    erpnext = config_dict["erpnext"]
    if not erpnext["api_key"] and setup_config.erpnext.has_existing_api_key:
        erpnext.pop("api_key")
        erpnext["api_key_ref"] = erpnext_api_key_secret_id()
    if not erpnext["api_secret"] and setup_config.erpnext.has_existing_api_secret:
        erpnext.pop("api_secret")
        erpnext["api_secret_ref"] = erpnext_api_secret_secret_id()

    setup_devices = {device.device_id.strip(): device for device in setup_config.devices}
    for device in config_dict["devices"]:
        setup_device = setup_devices.get(device["device_id"])
        if setup_device is None:
            continue
        if device.get("password") == "" and setup_device.has_existing_password:
            device.pop("password", None)
            device["password_ref"] = device_password_secret_id(device["device_id"])
