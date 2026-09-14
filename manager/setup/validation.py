"""Validation and safe summary helpers for first-run setup."""

import copy

from config.schema import ConfigurationError, validate_json_config


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
                "password": int(device.password),
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
    validate_json_config(config_dict)
    return config_dict


def safe_review_summary(setup_config):
    enabled_devices = [device for device in setup_config.devices if device.enabled]
    return {
        "erpnext_url": setup_config.erpnext.url.strip(),
        "api_key": _mask_identifier(setup_config.erpnext.api_key),
        "api_secret": SECRET_MASK if setup_config.erpnext.api_secret else "",
        "verify_ssl": bool(setup_config.erpnext.verify_ssl),
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
