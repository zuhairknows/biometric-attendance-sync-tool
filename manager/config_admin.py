"""Commercial configuration administration helpers for the Manager."""

import datetime
import json
import shutil
import zipfile
from pathlib import Path

from config import paths as config_paths
from config.loader import load_config
from config.status import CONFIGURED, LEGACY_CONFIGURED, get_configuration_status
from manager.health import get_health_snapshot, read_status_data
from manager import support

try:
    from version import PRODUCT_VERSION
except Exception:
    PRODUCT_VERSION = "unknown"


BACKUP_METADATA_NAME = "metadata.json"
BACKUP_CONFIG_NAME = "config.json"
BACKUP_SECRETS_PREFIX = "secrets/"


class ConfigurationAdminError(RuntimeError):
    pass


def get_configuration_summary(paths_module=config_paths, status=None, service_status=None):
    status = status or get_configuration_status(paths_module=paths_module)
    raw_config = _read_json_if_present(paths_module.get_config_path())
    erpnext = raw_config.get("erpnext", {}) if isinstance(raw_config.get("erpnext"), dict) else {}
    sync = raw_config.get("sync", {}) if isinstance(raw_config.get("sync"), dict) else {}
    devices = raw_config.get("devices", []) if isinstance(raw_config.get("devices"), list) else []
    runtime = _load_runtime_if_configured(status, paths_module)
    if runtime is not None:
        erpnext = {
            "url": getattr(runtime, "ERPNEXT_URL", ""),
            "verify_ssl": getattr(runtime, "ERPNEXT_VERIFY_SSL", ""),
            "api_key": getattr(runtime, "ERPNEXT_API_KEY", ""),
            "api_secret": getattr(runtime, "ERPNEXT_API_SECRET", ""),
        }
        sync = {
            "pull_frequency_minutes": getattr(runtime, "PULL_FREQUENCY", ""),
            "import_start_date": _display_import_start_date(getattr(runtime, "IMPORT_START_DATE", "")),
        }
        devices = getattr(runtime, "devices", [])
    enabled_devices = [device for device in devices if isinstance(device, dict) and device.get("enabled", True)]

    return {
        "configuration_state": status.state,
        "configuration_source": _source_label(status),
        "status_message": status.message,
        "status_details": list(status.details or []),
        "schema_version": raw_config.get("schema_version", getattr(status, "schema_version", "")),
        "last_updated_at": raw_config.get("last_updated_at", ""),
        "erpnext_url": erpnext.get("url", ""),
        "verify_ssl": erpnext.get("verify_ssl", ""),
        "credentials_configured": _credentials_present(erpnext),
        "enabled_devices": len(enabled_devices),
        "total_devices": len(devices),
        "sync_interval_minutes": sync.get("pull_frequency_minutes", ""),
        "import_start_date": sync.get("import_start_date", ""),
        "service_state": getattr(service_status, "state", "") if service_status is not None else "",
        "config_path": str(paths_module.get_config_path()),
        "logs_path": str(paths_module.get_logs_dir()),
        "app_data_path": str(paths_module.get_config_path().parent),
    }


def create_configuration_backup(paths_module=config_paths, backup_dir=None, timestamp=None):
    config_path = paths_module.get_config_path()
    if not config_path.is_file():
        raise ConfigurationAdminError("No commercial configuration exists to back up.")

    backup_root = Path(backup_dir) if backup_dir is not None else _backups_dir(paths_module)
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = backup_root / ("BiometricAttendanceSync-Config-" + timestamp + ".zip")

    metadata = {
        "created_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "format": "biometric-attendance-sync-config-backup-v1",
        "contains": ["config.json", "secrets"],
        "dpapi_scope": "machine",
        "warning": "Protected secrets may not decrypt on another Windows machine.",
    }
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(config_path, BACKUP_CONFIG_NAME)
        archive.writestr(BACKUP_METADATA_NAME, json.dumps(metadata, indent=2) + "\n")
        secrets_dir = paths_module.get_secrets_dir()
        if secrets_dir.exists():
            for secret_file in sorted(secrets_dir.rglob("*.secret")):
                if secret_file.is_file():
                    archive.write(secret_file, BACKUP_SECRETS_PREFIX + secret_file.relative_to(secrets_dir).as_posix())
    return archive_path


def restore_configuration_backup(archive_path, paths_module=config_paths, validate=True):
    archive_path = Path(archive_path)
    _validate_backup_archive(archive_path)
    snapshot = _snapshot_commercial_files(paths_module)
    target_config = paths_module.get_config_path()
    target_secrets = paths_module.get_secrets_dir()
    target_config.parent.mkdir(parents=True, exist_ok=True)
    target_secrets.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            config_bytes = archive.read(BACKUP_CONFIG_NAME)
            secret_files = {
                Path(name[len(BACKUP_SECRETS_PREFIX):]): archive.read(name)
                for name in archive.namelist()
                if name.startswith(BACKUP_SECRETS_PREFIX) and not name.endswith("/")
            }
        target_config.write_bytes(config_bytes)
        _replace_directory_contents(None, target_secrets)
        for relative_path, contents in secret_files.items():
            target = target_secrets / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
        if validate:
            load_config(config_path=target_config, allow_legacy=False, paths_module=paths_module)
    except Exception:
        _restore_commercial_files(paths_module, snapshot)
        raise
    return target_config


def reset_commercial_configuration(paths_module=config_paths, service_controller=None):
    backup_path = None
    service_status = service_controller.get_status() if service_controller is not None else None
    if service_controller is not None and getattr(service_status, "state", "") == "Running":
        stop_result = service_controller.stop_service()
        if not stop_result.success:
            raise ConfigurationAdminError("Could not stop the Windows service before reset: " + stop_result.message)
    if paths_module.get_config_path().is_file():
        backup_path = create_configuration_backup(paths_module=paths_module)
    try:
        paths_module.get_config_path().unlink()
    except FileNotFoundError:
        pass
    _replace_directory_contents(None, paths_module.get_secrets_dir())
    return backup_path


def export_diagnostics_report(paths_module=config_paths, service_status=None, output_dir=None):
    status = get_configuration_status(paths_module=paths_module)
    summary = get_configuration_summary(paths_module=paths_module, status=status, service_status=service_status)
    raw_config = _read_json_if_present(paths_module.get_config_path())
    runtime = _load_runtime_if_configured(status, paths_module)
    health = None
    if runtime is not None:
        try:
            health = get_health_snapshot(runtime)
        except Exception:
            health = None
    status_data, status_file_found = _read_support_status_data(paths_module, runtime)
    report = {
        "created_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "product": {
            "name": "Biometric Attendance Sync",
            "version": PRODUCT_VERSION,
        },
        "platform": support.platform_summary(),
        "configuration": summary,
        "devices": _safe_devices(raw_config.get("devices", [])),
        "service": _safe_service_status(service_status),
        "synchronization": _safe_sync_status(status_data, status_file_found),
        "device_health": _safe_device_health(getattr(health, "devices", []) if health else [], status_data, raw_config.get("devices", [])),
        "warnings": _safe_warnings(health),
        "paths": {
            "config": str(paths_module.get_config_path()),
            "logs": str(paths_module.get_logs_dir()),
            "state": str(paths_module.get_state_path()),
            "retry": str(paths_module.get_retry_dir()),
            "app_data": str(paths_module.get_config_path().parent),
        },
    }
    output_root = Path(output_dir) if output_dir is not None else _diagnostics_dir(paths_module)
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / ("BiometricAttendanceSync-Diagnostics-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def build_diagnostics_summary_text(configuration_status, service_status, health, configuration_summary, erpnext_text="Not tested"):
    configuration_summary = configuration_summary or {}
    warnings = list(getattr(health, "warnings", []) or [])
    enabled = int(configuration_summary.get("enabled_devices") or 0)
    configured = int(configuration_summary.get("total_devices") or 0)
    device_count = len(getattr(health, "devices", []) or [])
    configured = max(configured, device_count)
    last_sync = getattr(health, "last_successful_sync", "") if health is not None else ""
    lines = [
        "Biometric Attendance Sync",
        "Version: " + str(PRODUCT_VERSION),
        "Configuration: " + _configuration_label(configuration_status),
        "Service: " + support.status_label_for_service(service_status),
        "ERPNext: " + str(erpnext_text or "Not tested"),
        "Devices: " + str(configured) + " configured / " + str(enabled) + " enabled",
        "Last Sync: " + (str(last_sync) if last_sync else "No successful synchronization recorded"),
        "Warnings: " + str(len(warnings)),
    ]
    return "\n".join(lines)


def build_change_summary(setup_config, paths_module=config_paths):
    current = _read_json_if_present(paths_module.get_config_path())
    if not current:
        return ["New commercial configuration will be created."]
    changes = []
    erpnext = current.get("erpnext", {}) if isinstance(current.get("erpnext"), dict) else {}
    sync = current.get("sync", {}) if isinstance(current.get("sync"), dict) else {}
    if str(erpnext.get("url") or "") != setup_config.erpnext.url.strip():
        changes.append("ERPNext URL: " + str(erpnext.get("url") or "") + " -> " + setup_config.erpnext.url.strip())
    if bool(erpnext.get("verify_ssl", True)) != bool(setup_config.erpnext.verify_ssl):
        changes.append("SSL Verification: " + str(bool(erpnext.get("verify_ssl", True))) + " -> " + str(bool(setup_config.erpnext.verify_ssl)))
    if int(sync.get("pull_frequency_minutes", 60)) != int(setup_config.sync.pull_frequency_minutes):
        changes.append("Synchronization Interval: " + str(sync.get("pull_frequency_minutes", 60)) + " -> " + str(int(setup_config.sync.pull_frequency_minutes)) + " minutes")
    if str(sync.get("import_start_date") or "") != str(setup_config.sync.import_start_date or ""):
        changes.append("Attendance Import Start Date: " + str(sync.get("import_start_date") or "") + " -> " + str(setup_config.sync.import_start_date or ""))
    changes.extend(_device_changes(current.get("devices", []), setup_config.devices))
    changes.extend(_secret_changes(setup_config))
    return changes or ["No configuration changes detected."]


def _device_changes(current_devices, setup_devices):
    current_by_id = {str(device.get("device_id") or ""): device for device in current_devices if isinstance(device, dict)}
    setup_by_id = {device.device_id.strip(): device for device in setup_devices}
    changes = []
    for device_id in sorted(set(setup_by_id) - set(current_by_id)):
        changes.append("Device added: " + device_id)
    for device_id in sorted(set(current_by_id) - set(setup_by_id)):
        changes.append("Device removed: " + device_id)
    for device_id in sorted(set(current_by_id) & set(setup_by_id)):
        current_enabled = bool(current_by_id[device_id].get("enabled", True))
        setup_enabled = bool(setup_by_id[device_id].enabled)
        if current_enabled != setup_enabled:
            changes.append(("Device enabled: " if setup_enabled else "Device disabled: ") + device_id)
    return changes


def _secret_changes(setup_config):
    changes = []
    if str(setup_config.erpnext.api_key or "").strip():
        changes.append("Credentials: API key will be replaced.")
    if str(setup_config.erpnext.api_secret or "").strip():
        changes.append("Credentials: API secret will be replaced.")
    for device in setup_config.devices:
        if str(device.password or "").strip() and str(device.password) != "0":
            changes.append("Device password will be replaced: " + device.device_id.strip())
    return changes


def _safe_devices(devices):
    safe = []
    for device in devices if isinstance(devices, list) else []:
        if not isinstance(device, dict):
            continue
        safe.append({
            "device_id": device.get("device_id", ""),
            "name": device.get("name", ""),
            "ip": device.get("ip", device.get("host", "")),
            "port": device.get("port", ""),
            "enabled": device.get("enabled", True),
            "clear_from_device_on_fetch": device.get("clear_from_device_on_fetch", False),
        })
    return safe


def _safe_service_status(service_status):
    if service_status is None:
        return {"installed": None, "state": "Unknown", "startup": "Unknown", "recovery": "Unknown"}
    return {
        "installed": bool(getattr(service_status, "installed", False)),
        "state": str(getattr(service_status, "state", "") or "Unknown"),
        "startup": str(getattr(service_status, "startup", "") or "Unknown"),
        "recovery": str(getattr(service_status, "recovery", "") or "Unknown"),
    }


def _safe_sync_status(status_data, status_file_found):
    cycle = _safe_cycle_summary(status_data)
    return {
        "status_file_found": bool(status_file_found),
        "last_successful_sync": str(status_data.get("mission_accomplished_timestamp") or ""),
        "latest_sync_cycle": cycle,
    }


def _read_support_status_data(paths_module, runtime):
    status_data, found = read_status_data(runtime)
    if status_data or found:
        return status_data, found
    state_path = paths_module.get_state_path()
    if not state_path.is_file():
        return {}, False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}, True
    except Exception:
        return {}, True


def _safe_device_health(devices, status_data=None, configured_devices=None):
    status_data = status_data or {}
    cycle_devices = _cycle_devices_by_id(status_data)
    safe = []
    for device in devices:
        device_id = str(getattr(device, "device_id", "") or "")
        safe.append({
            "device_id": device_id,
            "ip": str(getattr(device, "ip", "") or ""),
            "port": getattr(device, "port", ""),
            "last_pull": str(getattr(device, "last_pull", "") or status_data.get(device_id + "_pull_timestamp") or ""),
            "last_push": str(getattr(device, "last_push", "") or status_data.get(device_id + "_push_timestamp") or ""),
            "latest_sync": cycle_devices.get(device_id, {}),
        })
    if safe:
        return safe
    for device in configured_devices if isinstance(configured_devices, list) else []:
        if not isinstance(device, dict):
            continue
        device_id = str(device.get("device_id") or "")
        safe.append({
            "device_id": device_id,
            "ip": str(device.get("ip") or device.get("host") or ""),
            "port": device.get("port", ""),
            "last_pull": str(status_data.get(device_id + "_pull_timestamp") or ""),
            "last_push": str(status_data.get(device_id + "_push_timestamp") or ""),
            "latest_sync": cycle_devices.get(device_id, {}),
        })
    return safe


def _safe_cycle_summary(status_data):
    cycle = status_data.get("latest_sync_cycle") if isinstance(status_data, dict) else None
    if not isinstance(cycle, dict):
        return {}
    return {
        "started_at": str(cycle.get("started_at") or ""),
        "completed_at": str(cycle.get("completed_at") or ""),
        "total_enabled_devices_attempted": _safe_int(cycle.get("total_enabled_devices_attempted")),
        "successful": _safe_int(cycle.get("successful")),
        "successful_with_warnings": _safe_int(cycle.get("successful_with_warnings")),
        "retryable_failures": _safe_int(cycle.get("retryable_failures")),
        "failed": _safe_int(cycle.get("failed")),
        "stopped_early": bool(cycle.get("stopped_early", False)),
    }


def _cycle_devices_by_id(status_data):
    cycle = status_data.get("latest_sync_cycle") if isinstance(status_data, dict) else None
    if not isinstance(cycle, dict):
        return {}
    safe = {}
    for device in cycle.get("devices", []) or []:
        if not isinstance(device, dict):
            continue
        device_id = str(device.get("device_id") or "")
        if not device_id:
            continue
        safe[device_id] = {
            "outcome": str(device.get("outcome") or ""),
            "successful_record_count": _safe_int(device.get("successful_record_count")),
            "duplicate_record_count": _safe_int(device.get("duplicate_record_count")),
            "missing_employee_count": _safe_int(device.get("missing_employee_count")),
            "validation_failure_count": _safe_int(device.get("validation_failure_count")),
            "corrupt_record_count": _safe_int(device.get("corrupt_record_count")),
            "retryable_failure_count": _safe_int(device.get("retryable_failure_count")),
            "error_category": str(device.get("error_category") or ""),
            "message": _safe_device_message(device.get("message")),
        }
    return safe


def _safe_device_message(message):
    text = str(message or "")
    blocked_tokens = ("Traceback", "frappe.exceptions", "Authorization", "api_secret", "password", "raw_record")
    if any(token.lower() in text.lower() for token in blocked_tokens):
        return ""
    return text[:200]


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_warnings(health):
    warnings = list(getattr(health, "warnings", []) or []) if health is not None else []
    missing_employee_count = 0
    for warning in warnings:
        text = str(warning)
        token = text.split(" ", 1)[0]
        if token.isdigit() and "attendance records could not be matched" in text:
            missing_employee_count += int(token)
    return {
        "count": len(warnings),
        "missing_employee_count": missing_employee_count,
        "messages": warnings,
    }


def _configuration_label(configuration_status):
    if configuration_status is None:
        return "Unknown"
    state = getattr(configuration_status, "state", "")
    if state in (CONFIGURED, LEGACY_CONFIGURED):
        return "Valid"
    if state == "INVALID":
        return "Invalid"
    return "Not configured"


def _validate_backup_archive(archive_path):
    if not archive_path.is_file():
        raise ConfigurationAdminError("Backup file does not exist.")
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if BACKUP_CONFIG_NAME not in names:
            raise ConfigurationAdminError("Backup does not contain config.json.")
        for name in names:
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ConfigurationAdminError("Backup contains an unsafe path: " + name)
            if name not in (BACKUP_CONFIG_NAME, BACKUP_METADATA_NAME) and not name.startswith(BACKUP_SECRETS_PREFIX):
                raise ConfigurationAdminError("Backup contains unsupported content: " + name)


def _snapshot_commercial_files(paths_module):
    snapshot = {"config": None, "secrets": {}}
    config_path = paths_module.get_config_path()
    if config_path.is_file():
        snapshot["config"] = config_path.read_bytes()
    secrets_dir = paths_module.get_secrets_dir()
    if secrets_dir.exists():
        for path in secrets_dir.rglob("*.secret"):
            if path.is_file():
                snapshot["secrets"][path.relative_to(secrets_dir)] = path.read_bytes()
    return snapshot


def _restore_commercial_files(paths_module, snapshot):
    config_path = paths_module.get_config_path()
    if snapshot["config"] is None:
        try:
            config_path.unlink()
        except FileNotFoundError:
            pass
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_bytes(snapshot["config"])
    secrets_dir = paths_module.get_secrets_dir()
    _replace_directory_contents(None, secrets_dir)
    for relative, data in snapshot["secrets"].items():
        target = secrets_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _replace_directory_contents(source_dir, target_dir):
    target_dir = Path(target_dir)
    if target_dir.exists():
        for child in target_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    target_dir.mkdir(parents=True, exist_ok=True)
    if source_dir is None or not Path(source_dir).exists():
        return
    for source in Path(source_dir).rglob("*"):
        if source.is_file():
            target = target_dir / source.relative_to(source_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _backups_dir(paths_module):
    if hasattr(paths_module, "get_backups_dir"):
        return paths_module.get_backups_dir()
    return paths_module.get_config_path().parent / "backups"


def _diagnostics_dir(paths_module):
    if hasattr(paths_module, "get_diagnostics_dir"):
        return paths_module.get_diagnostics_dir()
    return paths_module.get_config_path().parent / "diagnostics"


def _read_json_if_present(path):
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_runtime_if_configured(status, paths_module):
    if status.state not in (CONFIGURED, LEGACY_CONFIGURED):
        return None
    try:
        return load_config(paths_module=paths_module)
    except Exception:
        return None


def _display_import_start_date(value):
    if value in (None, ""):
        return ""
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return text[0:4] + "-" + text[4:6] + "-" + text[6:8]
    return text


def _source_label(status):
    if status.state == "LEGACY_CONFIGURED":
        return "Legacy local_config.py"
    if status.source == "json":
        return "Commercial config.json"
    return status.message or status.source


def _credentials_present(erpnext):
    has_key = bool(str(erpnext.get("api_key") or erpnext.get("api_user") or "").strip() or erpnext.get("api_key_ref"))
    has_secret = bool(str(erpnext.get("api_secret") or "").strip() or erpnext.get("api_secret_ref"))
    return has_key and has_secret
