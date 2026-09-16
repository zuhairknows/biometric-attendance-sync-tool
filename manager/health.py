import json
import os
from dataclasses import dataclass, field

from .paths import get_status_file

# Legacy log scanning is a fallback only. Audit files reach hundreds of megabytes
# during a bad device run, so never read one whole: take a bounded tail instead.
LOG_TAIL_LINE_LIMIT = 200
LOG_TAIL_BYTE_LIMIT = 256 * 1024


@dataclass
class DeviceHealth:
    device_id: str
    ip: str
    port: int
    last_pull: str = ""
    last_push: str = ""
    sync_outcome: str = ""


@dataclass
class HealthSnapshot:
    last_successful_sync: str = ""
    devices: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    cycle_summary: dict = field(default_factory=dict)
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
    cycle_summary = _safe_cycle_summary(status_data)
    devices = []
    for raw_device in getattr(config_module, "devices", []) or []:
        try:
            device = sync_module.normalize_device_config(raw_device)
        except Exception as exc:
            devices.append(DeviceHealth(str(raw_device.get("device_id", "Unknown")), "", 0, last_pull="", last_push="Configuration problem: " + str(exc)))
            continue
        device_id = device["device_id"]
        device_result = _device_result_from_cycle(cycle_summary, device_id)
        devices.append(DeviceHealth(
            device_id=device_id,
            ip=str(device["ip"]),
            port=device["port"],
            last_pull=str(status_data.get(device_id + "_pull_timestamp") or ""),
            last_push=str(status_data.get(device_id + "_push_timestamp") or ""),
            sync_outcome=str(device_result.get("outcome") or ""),
        ))

    warnings = _sync_warnings(config_module, status_data)
    return HealthSnapshot(
        last_successful_sync=str(status_data.get("mission_accomplished_timestamp") or ""),
        devices=devices,
        warnings=warnings,
        cycle_summary=cycle_summary,
        status_file_found=found,
    )


def _sync_warnings(config_module, status_data=None):
    structured_warnings = _structured_sync_warnings(status_data or {})
    if structured_warnings is not None:
        return structured_warnings
    logs_folder = get_status_file(config_module).parent
    missing_employee_count = 0
    retryable_failure_count = 0
    validation_failure_count = 0
    corrupt_record_count = 0
    invalid_record_count = 0
    for missing_log in logs_folder.glob("attendance_missing_employee_log_*.log"):
        lines = read_log_tail_lines(missing_log)
        missing_employee_count += sum("MISSING_EMPLOYEE_MAPPING" in line for line in lines)
    for validation_log in logs_folder.glob("attendance_validation_failure_log_*.log"):
        lines = read_log_tail_lines(validation_log)
        validation_failure_count += sum("VALIDATION_FAILURE" in line for line in lines)
    for corrupt_log in logs_folder.glob("attendance_corrupt_record_log_*.log"):
        lines = read_log_tail_lines(corrupt_log)
        corrupt_record_count += sum("CORRUPT_ATTENDANCE_RECORD" in line for line in lines)
    for invalid_log in logs_folder.glob("attendance_invalid_record_log_*.log"):
        lines = read_log_tail_lines(invalid_log)
        invalid_record_count += sum("INVALID_ATTENDANCE_RECORD" in line for line in lines)
    for failed_log in logs_folder.glob("attendance_failed_log_*.log"):
        lines = read_log_tail_lines(failed_log)
        missing_employee_count += sum("No Employee found" in line for line in lines)
        retryable_failure_count += sum(line.strip() and "No Employee found" not in line for line in lines)
    return _format_sync_warnings(
        missing_employee_count=missing_employee_count,
        retryable_failure_count=retryable_failure_count,
        validation_failure_count=validation_failure_count,
        corrupt_record_count=corrupt_record_count,
        invalid_record_count=invalid_record_count,
    )


def read_log_tail_lines(log_path, line_limit=LOG_TAIL_LINE_LIMIT, byte_limit=LOG_TAIL_BYTE_LIMIT):
    """Read at most the last `byte_limit` bytes of a log and return its last lines.

    A multi-megabyte audit file must never be pulled into memory just to count
    dashboard warnings, and the manager refreshes on a background worker that the
    Qt UI thread waits on.
    """
    try:
        with open(log_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - byte_limit), os.SEEK_SET)
            payload = handle.read()
    except OSError:
        return []
    if size > byte_limit:
        # The first line is very likely truncated mid-record; drop it.
        _, _, payload = payload.partition(b"\n")
    lines = payload.decode("utf-8", errors="replace").splitlines()
    return lines[-line_limit:]


def _format_sync_warnings(
    missing_employee_count=0,
    retryable_failure_count=0,
    validation_failure_count=0,
    corrupt_record_count=0,
    invalid_record_count=0,
    failed_count=0,
):
    warnings = []
    if missing_employee_count:
        warnings.append("Missing Employee mappings: " + str(missing_employee_count))
    if retryable_failure_count:
        warnings.append("Retryable synchronization failures: " + str(retryable_failure_count))
    if validation_failure_count:
        warnings.append("Permanent validation/data failures: " + str(validation_failure_count))
    if corrupt_record_count:
        warnings.append("Corrupt attendance records skipped: " + str(corrupt_record_count))
    if invalid_record_count:
        warnings.append("Invalid attendance records rejected locally: " + str(invalid_record_count))
    if failed_count:
        warnings.append("Device synchronization failures: " + str(failed_count))
    return warnings


def _safe_cycle_summary(status_data):
    cycle = status_data.get("latest_sync_cycle") if isinstance(status_data, dict) else None
    if not isinstance(cycle, dict):
        return {}
    return cycle


def _device_result_from_cycle(cycle_summary, device_id):
    for device_result in cycle_summary.get("devices", []) or []:
        if isinstance(device_result, dict) and str(device_result.get("device_id") or "") == str(device_id):
            return device_result
    return {}


def _structured_sync_warnings(status_data):
    cycle = _safe_cycle_summary(status_data)
    if not cycle:
        return None
    missing_employee_count = 0
    retryable_failure_count = int(cycle.get("retryable_failures") or 0)
    validation_failure_count = 0
    corrupt_record_count = 0
    invalid_record_count = 0
    failed_count = int(cycle.get("failed") or 0)
    for device_result in cycle.get("devices", []) or []:
        if not isinstance(device_result, dict):
            continue
        missing_employee_count += int(device_result.get("missing_employee_count") or 0)
        validation_failure_count += int(device_result.get("validation_failure_count") or 0)
        corrupt_record_count += int(device_result.get("corrupt_record_count") or 0)
        invalid_record_count += int(device_result.get("invalid_record_count") or 0)
    return _format_sync_warnings(
        missing_employee_count=missing_employee_count,
        retryable_failure_count=retryable_failure_count,
        validation_failure_count=validation_failure_count,
        corrupt_record_count=corrupt_record_count,
        invalid_record_count=invalid_record_count,
        failed_count=failed_count,
    )
