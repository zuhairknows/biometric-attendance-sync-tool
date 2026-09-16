import json
from dataclasses import dataclass, field

from .paths import get_status_file


@dataclass
class DeviceHealth:
    device_id: str
    ip: str
    port: int
    last_pull: str = ""
    last_push: str = ""


@dataclass
class HealthSnapshot:
    last_successful_sync: str = ""
    devices: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    status_file_found: bool = False


def read_status_data(config_module=None):
    status_file = get_status_file(config_module)
    if not status_file.exists():
        return {}, False
    try:
        return json.loads(status_file.read_text(encoding="utf-8")), True
    except (OSError, json.JSONDecodeError):
        return {}, True


def get_health_snapshot(config_module=None, sync_module=None):
    if sync_module is None:
        import erpnext_sync as sync_module
    if config_module is None:
        config_module = sync_module.config

    status_data, found = read_status_data(config_module)
    devices = []
    for raw_device in getattr(config_module, "devices", []) or []:
        try:
            device = sync_module.normalize_device_config(raw_device)
        except Exception as exc:
            devices.append(DeviceHealth(str(raw_device.get("device_id", "Unknown")), "", 0, last_pull="", last_push="Configuration problem: " + str(exc)))
            continue
        device_id = device["device_id"]
        devices.append(DeviceHealth(
            device_id=device_id,
            ip=str(device["ip"]),
            port=device["port"],
            last_pull=str(status_data.get(device_id + "_pull_timestamp") or ""),
            last_push=str(status_data.get(device_id + "_push_timestamp") or ""),
        ))

    warnings = _sync_warnings(config_module)
    return HealthSnapshot(
        last_successful_sync=str(status_data.get("mission_accomplished_timestamp") or ""),
        devices=devices,
        warnings=warnings,
        status_file_found=found,
    )


def _sync_warnings(config_module):
    logs_folder = get_status_file(config_module).parent
    missing_employee_count = 0
    retryable_failure_count = 0
    validation_failure_count = 0
    for missing_log in logs_folder.glob("attendance_missing_employee_log_*.log"):
        try:
            lines = missing_log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        except OSError:
            continue
        missing_employee_count += sum("MISSING_EMPLOYEE_MAPPING" in line for line in lines)
    for validation_log in logs_folder.glob("attendance_validation_failure_log_*.log"):
        try:
            lines = validation_log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        except OSError:
            continue
        validation_failure_count += sum("VALIDATION_FAILURE" in line for line in lines)
    for failed_log in logs_folder.glob("attendance_failed_log_*.log"):
        try:
            lines = failed_log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        except OSError:
            continue
        missing_employee_count += sum("No Employee found" in line for line in lines)
        retryable_failure_count += sum(line.strip() and "No Employee found" not in line for line in lines)
    warnings = []
    if missing_employee_count:
        warnings.append("Missing Employee mappings: " + str(missing_employee_count))
    if retryable_failure_count:
        warnings.append("Retryable synchronization failures: " + str(retryable_failure_count))
    if validation_failure_count:
        warnings.append("Permanent validation/data failures: " + str(validation_failure_count))
    return warnings
