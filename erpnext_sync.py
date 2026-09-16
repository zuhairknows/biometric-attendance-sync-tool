
from config.loader import load_config
from config.schema import (
    normalize_runtime_device_config,
    validate_device_id as validate_runtime_device_id,
    validate_password as validate_runtime_password,
    validate_port as validate_runtime_port,
    validate_runtime_config as validate_normalized_runtime_config,
    validate_unique_device_ids as validate_runtime_unique_device_ids,
)
import requests
import datetime
import json
import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from dataclasses import asdict, dataclass
from struct import unpack
from pickledb import PickleDB
from zk import ZK, const

try:
    from service_runtime import get_loaded_runtime_config
except Exception:
    get_loaded_runtime_config = None


config = get_loaded_runtime_config() if get_loaded_runtime_config else None
if config is None:
    config = load_config()

EMPLOYEE_NOT_FOUND_ERROR_MESSAGE = "No Employee found for the given employee field value"
EMPLOYEE_NOT_FOUND_ATTENDANCE_DEVICE_ID_MESSAGE = "No Employee found for attendance_device_id"
EMPLOYEE_INACTIVE_ERROR_MESSAGE = "Transactions cannot be created for an Inactive Employee"
DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE = "This employee already has a log with the same timestamp"
allowlisted_errors = [EMPLOYEE_NOT_FOUND_ERROR_MESSAGE, EMPLOYEE_INACTIVE_ERROR_MESSAGE, DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE]

if hasattr(config,'allowed_exceptions'):
    allowlisted_errors_temp = []
    for error_number in config.allowed_exceptions:
        allowlisted_errors_temp.append(allowlisted_errors[error_number-1])
    allowlisted_errors = allowlisted_errors_temp

device_punch_values_IN = getattr(config, 'device_punch_values_IN', [0,4])
device_punch_values_OUT = getattr(config, 'device_punch_values_OUT', [1,5])
ERPNEXT_VERSION = getattr(config, 'ERPNEXT_VERSION', 14)
ERPNEXT_REQUEST_TIMEOUT = getattr(config, 'ERPNEXT_REQUEST_TIMEOUT', getattr(config, 'REQUEST_TIMEOUT', 30))
DEFAULT_ZK_PORT = 4370
DEFAULT_ZK_PASSWORD = 0

SUCCESS = "SUCCESS"
IDEMPOTENT_SUCCESS = "IDEMPOTENT_SUCCESS"
TERMINAL_DATA_FAILURE = "TERMINAL_DATA_FAILURE"
RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
VALIDATION_FAILURE = "VALIDATION_FAILURE"

DEVICE_SUCCESS = "DEVICE_SUCCESS"
DEVICE_SUCCESS_WITH_WARNINGS = "DEVICE_SUCCESS_WITH_WARNINGS"
DEVICE_RETRYABLE_FAILURE = "DEVICE_RETRYABLE_FAILURE"
DEVICE_FAILED = "DEVICE_FAILED"
LATEST_SYNC_CYCLE_STATUS_KEY = "latest_sync_cycle"


@dataclass
class SyncOutcome:
    status_code: int
    message: str
    category: str

    def __iter__(self):
        yield self.status_code
        yield self.message


@dataclass
class DecodedAttendance:
    user_id: str
    timestamp: datetime.datetime
    status: int
    punch: int = 0
    uid: int = 0


class AttendanceFetchResult(list):
    def __init__(self, attendances=None, corrupt_record_count=0):
        super().__init__(attendances or [])
        self.corrupt_record_count = corrupt_record_count


class RetryableSyncError(Exception):
    pass


@dataclass
class DeviceSyncResult:
    device_id: str
    outcome: str
    started_at: str
    completed_at: str = ""
    fetched_record_count: int = 0
    successful_record_count: int = 0
    duplicate_record_count: int = 0
    missing_employee_count: int = 0
    validation_failure_count: int = 0
    corrupt_record_count: int = 0
    retryable_failure_count: int = 0
    error_category: str = ""
    message: str = ""


@dataclass
class CycleSyncResult:
    started_at: str
    completed_at: str = ""
    total_enabled_devices_attempted: int = 0
    successful: int = 0
    successful_with_warnings: int = 0
    retryable_failures: int = 0
    failed: int = 0
    stopped_early: bool = False
    devices: list = None

# possible area of further developemt
    # Real-time events - setup getting events pushed from the machine rather then polling.
        #- this is documented as 'Real-time events' in the ZKProtocol manual.

# Notes:
# Status Keys in status.json
#  - lift_off_timestamp
#  - mission_accomplished_timestamp
#  - <device_id>_pull_timestamp
#  - <device_id>_push_timestamp
#  - <shift_type>_sync_timestamp

def main(stop_requested=None):
    """Takes care of checking if it is time to pull data based on config,
    then calling the relevent functions to pull data and push to EPRNext.

    """
    if getattr(config, 'CONFIG_SOURCE', '') == 'defaults':
        info_logger.info("Product is not configured. Complete first-run setup.")
        return
    if _stop_requested(stop_requested):
        info_logger.info("Synchronization stop requested; ending cycle.")
        return
    try:
        last_lift_off_timestamp = _safe_convert_date(status.get('lift_off_timestamp'), "%Y-%m-%d %H:%M:%S.%f")
        if (last_lift_off_timestamp and last_lift_off_timestamp < datetime.datetime.now() - datetime.timedelta(minutes=config.PULL_FREQUENCY)) or not last_lift_off_timestamp:
            cycle_result = CycleSyncResult(started_at=str(datetime.datetime.now()), devices=[])
            status.set('lift_off_timestamp', cycle_result.started_at)
            status.save()
            info_logger.info("Cleared for lift off!")
            validate_unique_device_ids(config.devices)
            stopped_early = False
            for device in config.devices:
                device_attendance_logs = None
                try:
                    device = normalize_device_config(device)
                    if not device['enabled']:
                        info_logger.info("Skipping disabled Device: "+ device['device_id'])
                        continue
                    if _stop_requested(stop_requested):
                        info_logger.info("Synchronization stop requested; no additional devices will be processed")
                        stopped_early = True
                        break
                    info_logger.info("Processing Device: "+ device['device_id'])
                    cycle_result.total_enabled_devices_attempted += 1
                    dump_file = get_dump_file_name_and_directory(device['device_id'])
                    if os.path.exists(dump_file):
                        info_logger.error('Device Attendance Dump Found in Log Directory. This can mean the program crashed unexpectedly. Retrying with dumped data.')
                        with open(dump_file, 'r') as f:
                            file_contents = f.read()
                            if file_contents:
                                device_attendance_logs = read_attendance_dump(file_contents)
                    device_result = normalize_device_sync_result(
                        pull_process_and_push_data(device, device_attendance_logs),
                        device['device_id'],
                    )
                    cycle_result.devices.append(asdict(device_result))
                    apply_device_result_to_cycle(cycle_result, device_result)
                    status.set(f'{device["device_id"]}_push_timestamp', str(datetime.datetime.now()))
                    status.save()
                    if os.path.exists(dump_file):
                        os.remove(dump_file)
                    info_logger.info("Successfully processed Device: "+ device['device_id'])
                except Exception as exc:
                    device_result = device_sync_result_from_exception(device, exc)
                    if 'cycle_result' in locals() and device_result:
                        cycle_result.devices.append(asdict(device_result))
                        apply_device_result_to_cycle(cycle_result, device_result)
                    error_logger.exception('exception when calling pull_process_and_push_data function for device'+json.dumps(redact_device_config(device), default=str))
                if _stop_requested(stop_requested):
                    info_logger.info("Synchronization stop requested; no additional devices will be processed")
                    stopped_early = True
                    break
            if stopped_early:
                info_logger.info("Synchronization cycle stopped early")
                cycle_result.stopped_early = True
                cycle_result.completed_at = str(datetime.datetime.now())
                persist_cycle_result(cycle_result)
                return
            if hasattr(config,'shift_type_device_mapping'):
                update_shift_last_sync_timestamp(config.shift_type_device_mapping)
            status.set('mission_accomplished_timestamp', str(datetime.datetime.now()))
            cycle_result.completed_at = status.get('mission_accomplished_timestamp')
            persist_cycle_result(cycle_result)
            status.save()
            info_logger.info("Mission Accomplished!")
    except:
        error_logger.exception('exception has occurred in the main function...')


def pull_process_and_push_data(device, device_attendance_logs=None):
    """ Takes a single device config as param and pulls data from that device.

    params:
    device: a single device config object from the local_config file
    device_attendance_logs: fetching from device is skipped if this param is passed. used to restart failed fetches from previous runs.
    """
    device = normalize_device_config(device)
    result = DeviceSyncResult(device_id=device['device_id'], outcome=DEVICE_SUCCESS, started_at=str(datetime.datetime.now()))
    attendance_success_log_file = '_'.join(["attendance_success_log", device['device_id']])
    attendance_failed_log_file = '_'.join(["attendance_failed_log", device['device_id']])
    attendance_missing_employee_log_file = '_'.join(["attendance_missing_employee_log", device['device_id']])
    attendance_validation_failure_log_file = '_'.join(["attendance_validation_failure_log", device['device_id']])
    attendance_success_logger = setup_logger(attendance_success_log_file, '/'.join([config.LOGS_DIRECTORY, attendance_success_log_file])+'.log')
    attendance_failed_logger = setup_logger(attendance_failed_log_file, '/'.join([config.LOGS_DIRECTORY, attendance_failed_log_file])+'.log')
    attendance_missing_employee_logger = setup_logger(attendance_missing_employee_log_file, '/'.join([config.LOGS_DIRECTORY, attendance_missing_employee_log_file])+'.log')
    attendance_validation_failure_logger = setup_logger(attendance_validation_failure_log_file, '/'.join([config.LOGS_DIRECTORY, attendance_validation_failure_log_file])+'.log')
    if not device_attendance_logs:
        device_attendance_logs = get_all_attendance_from_device(device['ip'], port=device['port'], password=device['password'], device_id=device['device_id'], clear_from_device_on_fetch=device['clear_from_device_on_fetch'])
        result.corrupt_record_count = getattr(device_attendance_logs, "corrupt_record_count", 0)
        if not device_attendance_logs:
            result.completed_at = str(datetime.datetime.now())
            result.outcome = classify_completed_device_outcome(result)
            return result
    device_attendance_logs = normalize_attendance_logs(device_attendance_logs)
    result.fetched_record_count = len(device_attendance_logs)
    # for finding the last successfull push and restart from that point (or) from a set 'config.IMPORT_START_DATE' (whichever is later)
    index_of_last = -1
    last_line = get_last_line_from_file('/'.join([config.LOGS_DIRECTORY, attendance_success_log_file])+'.log')
    import_start_date = _safe_convert_date(config.IMPORT_START_DATE, "%Y%m%d")
    if last_line or import_start_date:
        last_user_id = None
        last_timestamp = None
        if last_line:
            last_user_id, last_timestamp = last_line.split("\t")[4:6]
            last_timestamp = datetime.datetime.fromtimestamp(float(last_timestamp))
        if import_start_date:
            if last_timestamp:
                if last_timestamp < import_start_date:
                    last_timestamp = import_start_date
                    last_user_id = None
            else:
                last_timestamp = import_start_date
        for i, x in enumerate(device_attendance_logs):
            if last_user_id and last_timestamp:
                if last_user_id == str(x['user_id']) and last_timestamp == x['timestamp']:
                    index_of_last = i
                    break
            elif last_timestamp:
                if x['timestamp'] >= last_timestamp:
                    index_of_last = i - 1
                    break
        else:
            if last_timestamp and not last_user_id:
                result.completed_at = str(datetime.datetime.now())
                result.outcome = classify_completed_device_outcome(result)
                return result

    for device_attendance_log in device_attendance_logs[index_of_last+1:]:
        punch_direction = device['punch_direction']
        if punch_direction == 'AUTO':
            if device_attendance_log['punch'] in device_punch_values_OUT:
                punch_direction = 'OUT'
            elif device_attendance_log['punch'] in device_punch_values_IN:
                punch_direction = 'IN'
            else:
                punch_direction = None
        outcome = normalize_sync_outcome(send_to_erpnext(device_attendance_log['user_id'], device_attendance_log['timestamp'], device['device_id'], punch_direction, latitude=device.get('latitude'), longitude=device.get('longitude')))
        if outcome.category == SUCCESS:
            result.successful_record_count += 1
            attendance_success_logger.info("\t".join([outcome.message, str(device_attendance_log['uid']),
                str(device_attendance_log['user_id']), str(device_attendance_log['timestamp'].timestamp()),
                str(device_attendance_log['punch']), str(device_attendance_log['status']),
                json.dumps(device_attendance_log, default=str)]))
        elif outcome.category == IDEMPOTENT_SUCCESS:
            result.duplicate_record_count += 1
            attendance_success_logger.info("\t".join(['DUPLICATE_ALREADY_SYNCED: '+outcome.message, str(device_attendance_log['uid']),
                str(device_attendance_log['user_id']), str(device_attendance_log['timestamp'].timestamp()),
                str(device_attendance_log['punch']), str(device_attendance_log['status']),
                json.dumps(device_attendance_log, default=str)]))
        elif outcome.category == TERMINAL_DATA_FAILURE:
            result.missing_employee_count += 1
            missing_employee_audit = missing_employee_audit_context(device['device_id'], device_attendance_log)
            attendance_missing_employee_logger.warning("\t".join([
                "MISSING_EMPLOYEE_MAPPING",
                missing_employee_audit["device_id"],
                missing_employee_audit["attendance_device_id"],
                missing_employee_audit["timestamp"],
                missing_employee_audit["category"],
            ]))
            attendance_success_logger.info("\t".join(['MISSING_EMPLOYEE_MAPPING: '+json.dumps(missing_employee_audit, sort_keys=True), str(device_attendance_log['uid']),
                str(device_attendance_log['user_id']), str(device_attendance_log['timestamp'].timestamp()),
                str(device_attendance_log['punch']), str(device_attendance_log['status']),
                json.dumps(device_attendance_log, default=str)]))
        elif outcome.category == VALIDATION_FAILURE:
            result.validation_failure_count += 1
            validation_audit = validation_failure_audit_context(device['device_id'], device_attendance_log, outcome)
            attendance_validation_failure_logger.warning("\t".join([
                "VALIDATION_FAILURE",
                validation_audit["device_id"],
                validation_audit["attendance_device_id"],
                validation_audit["timestamp"],
                validation_audit["status_code"],
                validation_audit["category"],
            ]))
            attendance_success_logger.info("\t".join(['VALIDATION_FAILURE: '+json.dumps(validation_audit, sort_keys=True), str(device_attendance_log['uid']),
                str(device_attendance_log['user_id']), str(device_attendance_log['timestamp'].timestamp()),
                str(device_attendance_log['punch']), str(device_attendance_log['status']),
                json.dumps(device_attendance_log, default=str)]))
        else:
            result.retryable_failure_count += 1
            result.outcome = DEVICE_RETRYABLE_FAILURE
            result.completed_at = str(datetime.datetime.now())
            attendance_failed_logger.error("\t".join([str(outcome.status_code), str(device_attendance_log['uid']),
                str(device_attendance_log['user_id']), str(device_attendance_log['timestamp'].timestamp()),
                str(device_attendance_log['punch']), str(device_attendance_log['status']),
                json.dumps(device_attendance_log, default=str)]))
            raise RetryableSyncError('API Call to ERPNext Failed.')
    result.completed_at = str(datetime.datetime.now())
    result.outcome = classify_completed_device_outcome(result)
    return result


def get_all_attendance_from_device(ip, port=DEFAULT_ZK_PORT, timeout=30, password=DEFAULT_ZK_PASSWORD, device_id=None, clear_from_device_on_fetch=False):
    #  Sample Attendance Logs [{'punch': 255, 'user_id': '22', 'uid': 12349, 'status': 1, 'timestamp': datetime.datetime(2019, 2, 26, 20, 31, 29)},{'punch': 255, 'user_id': '7', 'uid': 7, 'status': 1, 'timestamp': datetime.datetime(2019, 2, 26, 20, 31, 36)}]
    port = validate_port(port)
    password = validate_password(password)
    zk = ZK(ip, port=port, timeout=timeout, password=password)
    conn = None
    device_disabled = False
    enable_failed = False
    attendances = []
    try:
        conn = zk.connect()
        x = conn.disable_device()
        device_disabled = True
        # device is disabled when fetching data
        info_logger.info("\t".join((ip, "Device Disable Attempted. Result:", str(x))))
        attendances = get_attendance_with_corrupt_record_recovery(conn, device_id, ip)
        info_logger.info("\t".join((ip, "Attendances Fetched:", str(len(attendances)))))
        status.set(f'{device_id}_push_timestamp', None)
        status.set(f'{device_id}_pull_timestamp', str(datetime.datetime.now()))
        status.save()
        corrupt_record_count = getattr(attendances, "corrupt_record_count", 0)
        if len(attendances):
            # keeping a backup before clearing data incase the programs fails.
            # if everything goes well then this file is removed automatically at the end.
            dump_file_name = get_dump_file_name_and_directory(device_id)
            with open(dump_file_name, 'w+') as f:
                f.write(json.dumps({
                    'device_id': device_id,
                    'ip': ip,
                    'port': port,
                    'fetched_at': datetime.datetime.now(),
                    'attendances': list(map(lambda x: x.__dict__, attendances))
                }, default=datetime.datetime.timestamp))
            if clear_from_device_on_fetch and corrupt_record_count:
                info_logger.warning("\t".join((ip, "Attendance Clear Skipped.", "Corrupt attendance records were skipped during fetch.")))
            elif clear_from_device_on_fetch:
                x = conn.clear_attendance()
                info_logger.info("\t".join((ip, "Attendance Clear Attempted. Result:", str(x))))
    except:
        error_logger.exception(str(ip)+' exception when fetching from device...')
        raise Exception('Device fetch failed.')
    finally:
        if conn:
            if device_disabled:
                try:
                    x = conn.enable_device()
                    info_logger.info("\t".join((ip, "Device Enable Attempted. Result:", str(x))))
                except:
                    enable_failed = True
                    error_logger.exception(str(ip)+' exception when re-enabling device...')
            conn.disconnect()
    if enable_failed:
        raise Exception('Device re-enable failed.')
    normalized_attendances = normalize_attendance_logs(list(map(lambda x: x.__dict__, attendances)))
    return AttendanceFetchResult(normalized_attendances, getattr(attendances, "corrupt_record_count", 0))


def classify_completed_device_outcome(result):
    if (
        result.missing_employee_count
        or result.validation_failure_count
        or result.corrupt_record_count
    ):
        return DEVICE_SUCCESS_WITH_WARNINGS
    return DEVICE_SUCCESS


def normalize_device_sync_result(result, device_id):
    if isinstance(result, DeviceSyncResult):
        return result
    now = str(datetime.datetime.now())
    return DeviceSyncResult(
        device_id=str(device_id or ""),
        outcome=DEVICE_SUCCESS,
        started_at=now,
        completed_at=now,
    )


def device_sync_result_from_exception(device, exc):
    device_id = ""
    try:
        device_id = str(device.get('device_id') or "")
    except Exception:
        device_id = "UNKNOWN"
    now = str(datetime.datetime.now())
    message = str(exc)
    is_retryable = isinstance(exc, RetryableSyncError) or message in ("Device fetch failed.", "API Call to ERPNext Failed.")
    return DeviceSyncResult(
        device_id=device_id,
        outcome=DEVICE_RETRYABLE_FAILURE if is_retryable else DEVICE_FAILED,
        started_at=now,
        completed_at=now,
        retryable_failure_count=1 if is_retryable else 0,
        error_category=RETRYABLE_FAILURE if is_retryable else "OPERATIONAL_FAILURE",
        message=safe_device_result_message(exc),
    )


def safe_device_result_message(exc):
    if isinstance(exc, RetryableSyncError):
        return "Retryable synchronization failure."
    text = str(exc)
    if text == "Device fetch failed.":
        return "Device communication failed."
    if text == "Device re-enable failed.":
        return "Device re-enable failed."
    if isinstance(exc, (ValueError, TypeError)):
        return text
    return type(exc).__name__


def apply_device_result_to_cycle(cycle_result, device_result):
    if device_result.outcome == DEVICE_SUCCESS:
        cycle_result.successful += 1
    elif device_result.outcome == DEVICE_SUCCESS_WITH_WARNINGS:
        cycle_result.successful_with_warnings += 1
    elif device_result.outcome == DEVICE_RETRYABLE_FAILURE:
        cycle_result.retryable_failures += 1
    else:
        cycle_result.failed += 1


def persist_cycle_result(cycle_result):
    status.set(LATEST_SYNC_CYCLE_STATUS_KEY, asdict(cycle_result))
    status.save()


def get_attendance_with_corrupt_record_recovery(conn, device_id=None, ip=None):
    """Read attendance using pyzk's buffer API so one bad timestamp can be skipped.

    Older/fake connection objects used by tests may only expose get_attendance();
    keep that path untouched. Real pyzk connections expose the lower-level methods
    used by get_attendance(), which lets us isolate timestamp decode failures per
    record without modifying site-packages.
    """
    required_attrs = ["read_sizes", "get_users", "read_with_buffer", "_ZK__decode_time"]
    if not all(hasattr(conn, attr) for attr in required_attrs):
        return conn.get_attendance()

    conn.read_sizes()
    if getattr(conn, "records", 0) == 0:
        return []
    users = conn.get_users()
    attendances = AttendanceFetchResult()
    corrupt_record_count = 0
    attendance_data, size = conn.read_with_buffer(const.CMD_ATTLOG_RRQ)
    if size < 4:
        return []
    total_size = unpack("I", attendance_data[:4])[0]
    if not getattr(conn, "records", 0):
        return []
    record_size = int(total_size / conn.records)
    attendance_data = attendance_data[4:]

    if record_size == 8:
        record_index = 0
        while len(attendance_data) >= 8:
            raw_record = attendance_data[:8]
            attendance_data = attendance_data[8:]
            try:
                uid, status, timestamp, punch = unpack('HB4sB', raw_record.ljust(8, b'\x00')[:8])
                tuser = list(filter(lambda x: x.uid == uid, users))
                user_id = str(uid) if not tuser else tuser[0].user_id
                timestamp = decode_attendance_timestamp(conn, timestamp)
                attendances.append(DecodedAttendance(user_id, timestamp, status, punch, uid))
            except (ValueError, OverflowError, TypeError) as exc:
                audit_corrupt_attendance_record(device_id, ip, record_index, record_size, raw_record, exc)
                corrupt_record_count += 1
            record_index += 1
    elif record_size == 16:
        record_index = 0
        while len(attendance_data) >= 16:
            raw_record = attendance_data[:16]
            attendance_data = attendance_data[16:]
            try:
                user_id, timestamp, status, punch, reserved, workcode = unpack('<I4sBB2sI', raw_record.ljust(16, b'\x00')[:16])
                user_id = str(user_id)
                tuser = list(filter(lambda x: x.user_id == user_id, users))
                if not tuser:
                    uid = str(user_id)
                    tuser = list(filter(lambda x: x.uid == user_id, users))
                    if tuser:
                        uid = tuser[0].uid
                        user_id = tuser[0].user_id
                else:
                    uid = tuser[0].uid
                timestamp = decode_attendance_timestamp(conn, timestamp)
                attendances.append(DecodedAttendance(user_id, timestamp, status, punch, uid))
            except (ValueError, OverflowError, TypeError) as exc:
                audit_corrupt_attendance_record(device_id, ip, record_index, record_size, raw_record, exc)
                corrupt_record_count += 1
            record_index += 1
    else:
        record_index = 0
        while len(attendance_data) >= 40:
            raw_record = attendance_data[:40]
            attendance_data = attendance_data[40:]
            try:
                uid, user_id, status, timestamp, punch, space = unpack('<H24sB4sB8s', raw_record.ljust(40, b'\x00')[:40])
                user_id = (user_id.split(b'\x00')[0]).decode(errors='ignore')
                timestamp = decode_attendance_timestamp(conn, timestamp)
                attendances.append(DecodedAttendance(user_id, timestamp, status, punch, uid))
            except (ValueError, OverflowError, TypeError) as exc:
                audit_corrupt_attendance_record(device_id, ip, record_index, 40, raw_record, exc)
                corrupt_record_count += 1
            record_index += 1
    attendances.corrupt_record_count = corrupt_record_count
    return attendances


def decode_attendance_timestamp(conn, raw_timestamp):
    return getattr(conn, "_ZK__decode_time")(raw_timestamp)


def audit_corrupt_attendance_record(device_id, ip, record_index, record_size, raw_record, exc):
    safe_device_id = str(device_id or "UNKNOWN")
    corrupt_log_file = '_'.join(["attendance_corrupt_record_log", safe_device_id])
    corrupt_logger = setup_logger(corrupt_log_file, '/'.join([config.LOGS_DIRECTORY, corrupt_log_file])+'.log')
    corrupt_logger.warning("\t".join([
        "CORRUPT_ATTENDANCE_RECORD",
        safe_device_id,
        str(ip or ""),
        str(record_index),
        str(record_size),
        bytes(raw_record).hex(),
        type(exc).__name__,
    ]))


def send_to_erpnext(employee_field_value, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
    """
    Examples: 
    
    For ERPNext, Frappe HR <= v14
    send_to_erpnext('12349',datetime.datetime.now(),'HO1','IN')

    For ERPNext, Frappe HR v15 onwards
    If 'Allow Geolocation Tracking' is on
    send_to_erpnext('12349',datetime.datetime.now(),'HO1','IN',latitude=12.34, longitude=56.78)
    """
    endpoint_app = "hrms" if ERPNEXT_VERSION > 13 else "erpnext"
    url = f"{config.ERPNEXT_URL}/api/method/{endpoint_app}.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field"
    headers = {
        'Authorization': "token "+ config.ERPNEXT_API_KEY + ":" + config.ERPNEXT_API_SECRET,
        'Accept': 'application/json'
    }
    data = {
        'employee_field_value' : employee_field_value,
        'timestamp' : timestamp.__str__(),
        'device_id' : device_id,
        'log_type' : log_type,
        'latitude' : latitude,
        'longitude' : longitude
    }
    try:
        response = requests.request("POST", url, headers=headers, json=data, timeout=ERPNEXT_REQUEST_TIMEOUT)
    except _request_timeout_exception():
        error_logger.error('\t'.join(['Retryable ERPNext API timeout.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type)]))
        return SyncOutcome(0, "ERPNext request timed out.", RETRYABLE_FAILURE)
    except _request_connection_exception():
        error_logger.error('\t'.join(['Retryable ERPNext API connection failure.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type)]))
        return SyncOutcome(0, "ERPNext connection failed.", RETRYABLE_FAILURE)
    except Exception as exc:
        error_logger.error('\t'.join(['Retryable ERPNext API transport failure.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type), type(exc).__name__]))
        return SyncOutcome(0, "ERPNext transport failure.", RETRYABLE_FAILURE)
    if response.status_code == 200:
        return SyncOutcome(response.status_code, json.loads(response._content)['message']['name'], SUCCESS)
    error_str = _safe_get_error_str(response)
    outcome = classify_erpnext_outcome(response.status_code, error_str)
    if outcome.category == IDEMPOTENT_SUCCESS:
        info_logger.info('\t'.join(['Duplicate Employee Checkin already exists in ERPNext.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type)]))
    elif outcome.category == TERMINAL_DATA_FAILURE:
        error_logger.error('\t'.join(['Terminal ERPNext data issue.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type), outcome.category]))
    elif outcome.category == VALIDATION_FAILURE:
        error_logger.error('\t'.join(['Permanent ERPNext validation failure.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type), str(outcome.status_code)]))
    else:
        error_logger.error('\t'.join(['Retryable ERPNext API failure.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type), str(outcome.status_code)]))
    return outcome

def normalize_sync_outcome(result):
    if isinstance(result, SyncOutcome):
        return result
    status_code, message = result
    return classify_erpnext_outcome(status_code, message)

def classify_erpnext_outcome(status_code, message):
    try:
        normalized_status = int(status_code or 0)
    except (TypeError, ValueError):
        normalized_status = 0
    text = str(message or "")
    if 200 <= normalized_status < 300:
        return SyncOutcome(normalized_status, text, SUCCESS)
    if is_duplicate_employee_checkin_response(normalized_status, text):
        return SyncOutcome(normalized_status, text, IDEMPOTENT_SUCCESS)
    if is_missing_employee_response(text):
        return SyncOutcome(normalized_status, text, TERMINAL_DATA_FAILURE)
    if normalized_status in (500, 502, 503, 504) or normalized_status == 0:
        return SyncOutcome(normalized_status, text, RETRYABLE_FAILURE)
    if normalized_status in (400, 417):
        return SyncOutcome(normalized_status, text, VALIDATION_FAILURE)
    if 400 <= normalized_status < 500:
        return SyncOutcome(normalized_status, text, VALIDATION_FAILURE)
    return SyncOutcome(normalized_status, text, RETRYABLE_FAILURE)

def _request_timeout_exception():
    return getattr(getattr(requests, "exceptions", object), "Timeout", TimeoutError)

def _request_connection_exception():
    return getattr(getattr(requests, "exceptions", object), "ConnectionError", ConnectionError)

def is_duplicate_employee_checkin_response(status_code, message):
    return int(status_code or 0) == 417 and DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE in str(message or "")

def is_missing_employee_response(message):
    text = str(message or "")
    return EMPLOYEE_NOT_FOUND_ERROR_MESSAGE in text or EMPLOYEE_NOT_FOUND_ATTENDANCE_DEVICE_ID_MESSAGE in text

def missing_employee_audit_context(device_id, device_attendance_log):
    timestamp = device_attendance_log.get('timestamp')
    if hasattr(timestamp, 'isoformat'):
        timestamp_value = timestamp.isoformat(sep=' ')
    else:
        timestamp_value = str(timestamp or "")
    return {
        "category": "missing_employee_mapping",
        "device_id": str(device_id or ""),
        "attendance_device_id": str(device_attendance_log.get('user_id') or ""),
        "timestamp": timestamp_value,
    }

def validation_failure_audit_context(device_id, device_attendance_log, outcome):
    timestamp = device_attendance_log.get('timestamp')
    if hasattr(timestamp, 'isoformat'):
        timestamp_value = timestamp.isoformat(sep=' ')
    else:
        timestamp_value = str(timestamp or "")
    return {
        "category": "validation_failure",
        "device_id": str(device_id or ""),
        "attendance_device_id": str(device_attendance_log.get('user_id') or ""),
        "timestamp": timestamp_value,
        "status_code": str(getattr(outcome, "status_code", "") or ""),
    }

def is_non_retryable_attendance_failure(message):
    text = str(message or "")
    for error in allowlisted_errors:
        if error == DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE:
            continue
        if error == EMPLOYEE_NOT_FOUND_ERROR_MESSAGE and is_missing_employee_response(text):
            return True
        if error in text:
            return True
    return False

def validate_runtime_config(config_module=None):
    config_module = config_module or config
    return validate_normalized_runtime_config(config_module)

def update_shift_last_sync_timestamp(shift_type_device_mapping):
    """
    ### algo for updating the sync_current_timestamp
    - get a list of devices to check
    - check if all the devices have a non 'None' push_timestamp
        - check if the earliest of the pull timestamp is greater than sync_current_timestamp for each shift name
            - then update this min of pull timestamp to the shift

    """
    for shift_type_device_map in shift_type_device_mapping:
        all_devices_pushed = True
        pull_timestamp_array = []
        for device_id in shift_type_device_map['related_device_id']:
            if not status.get(f'{device_id}_push_timestamp'):
                all_devices_pushed = False
                break
            pull_timestamp_array.append(_safe_convert_date(status.get(f'{device_id}_pull_timestamp'), "%Y-%m-%d %H:%M:%S.%f"))
        if all_devices_pushed:
            min_pull_timestamp = min(pull_timestamp_array)
            if isinstance(shift_type_device_map['shift_type_name'], str): # for backward compatibility of config file
                shift_type_device_map['shift_type_name'] = [shift_type_device_map['shift_type_name']]
            for shift in shift_type_device_map['shift_type_name']:
                try:
                    sync_current_timestamp = _safe_convert_date(status.get(f'{shift}_sync_timestamp'), "%Y-%m-%d %H:%M:%S.%f")
                    if (sync_current_timestamp and min_pull_timestamp > sync_current_timestamp) or (min_pull_timestamp and not sync_current_timestamp):
                        response_code = send_shift_sync_to_erpnext(shift, min_pull_timestamp)
                        if response_code == 200:
                            status.set(f'{shift}_sync_timestamp', str(min_pull_timestamp))
                            status.save()
                except:
                    error_logger.exception('Exception in update_shift_last_sync_timestamp, for shift:'+shift)

def send_shift_sync_to_erpnext(shift_type_name, sync_timestamp):
    url = config.ERPNEXT_URL + "/api/resource/Shift Type/" + shift_type_name
    headers = {
        'Authorization': "token "+ config.ERPNEXT_API_KEY + ":" + config.ERPNEXT_API_SECRET,
        'Accept': 'application/json'
    }
    data = {
        "last_sync_of_checkin" : str(sync_timestamp)
    }
    try:
        response = requests.request("PUT", url, headers=headers, data=json.dumps(data), timeout=ERPNEXT_REQUEST_TIMEOUT)
        if response.status_code == 200:
            info_logger.info("\t".join(['Shift Type last_sync_of_checkin Updated', str(shift_type_name), str(sync_timestamp.timestamp())]))
        else:
            error_str = _safe_get_error_str(response)
            error_logger.error('\t'.join(['Error during ERPNext Shift Type API Call.', str(shift_type_name), str(sync_timestamp.timestamp()), error_str]))
        return response.status_code
    except:
        error_logger.exception("\t".join(['exception when updating last_sync_of_checkin in Shift Type', str(shift_type_name), str(sync_timestamp.timestamp())]))

def get_last_line_from_file(file):
    # concerns to address(may be much later):
        # how will last line lookup work with log rotation when a new file is created?
            #- will that new file be empty at any time? or will it have a partial line from the previous file?
    line = None
    if os.stat(file).st_size < 5000:
        # quick hack to handle files with one line
        with open(file, 'r') as f:
            for line in f:
                pass
    else:
        # optimized for large log files
        with open(file, 'rb') as f:
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b'\n':
                f.seek(-2, os.SEEK_CUR)
            line = f.readline().decode()
    return line


def setup_logger(name, log_file, level=logging.INFO, formatter=None):

    if not formatter:
        formatter = logging.Formatter('%(asctime)s\t%(levelname)s\t%(message)s')

    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.hasHandlers():
        handler = RotatingFileHandler(log_file, maxBytes=10000000, backupCount=50, encoding='utf-8')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger

def get_dump_file_name_and_directory(device_id, device_ip=None):
    retry_directory = getattr(config, 'RETRY_DIRECTORY', config.LOGS_DIRECTORY)
    return os.path.join(retry_directory, device_id + '_last_fetch_dump.json')

def normalize_device_config(device):
    normalized_device = normalize_runtime_device_config(device)
    device_id = normalized_device['device_id']
    if normalized_device['clear_from_device_on_fetch']:
        info_logger.warning('Device '+str(device_id)+' has clear_from_device_on_fetch enabled. This can delete attendance records from the biometric device.')
    return normalized_device

def validate_device_id(device_id):
    return validate_runtime_device_id(device_id)

def validate_unique_device_ids(devices):
    return validate_runtime_unique_device_ids(devices)

def _stop_requested(stop_requested):
    if stop_requested is None:
        return False
    try:
        return bool(stop_requested())
    except Exception:
        error_logger.exception('exception when checking service stop request...')
        return False

def redact_device_config(device):
    redacted_device = dict(device)
    if 'password' in redacted_device:
        redacted_device['password'] = '***'
    return redacted_device

def validate_port(port):
    return validate_runtime_port(port)

def validate_password(password):
    return validate_runtime_password(password)

def normalize_attendance_logs(attendance_logs):
    return sorted(attendance_logs, key=lambda row: (
        row.get('timestamp') or datetime.datetime.min,
        row.get('uid') if row.get('uid') is not None else -1,
        str(row.get('user_id', ''))
    ))

def read_attendance_dump(file_contents):
    dump_data = json.loads(file_contents)
    if isinstance(dump_data, dict):
        attendance_logs = dump_data.get('attendances', [])
    else:
        attendance_logs = dump_data
    return normalize_attendance_logs(list(map(lambda x: _apply_function_to_key(x, 'timestamp', datetime.datetime.fromtimestamp), attendance_logs)))

def _apply_function_to_key(obj, key, fn):
    obj[key] = fn(obj[key])
    return obj

def _safe_convert_date(datestring, pattern):
    try:
        return datetime.datetime.strptime(datestring, pattern)
    except:
        return None

def _safe_get_error_str(res):
    try:
        error_json = json.loads(res._content)
        if 'exc' in error_json: # this means traceback is available
            error_str = json.loads(error_json['exc'])[0]
        else:
            error_str = json.dumps(error_json)
    except:
        error_str = str(res.__dict__)
    return error_str

# setup logger and status
if not os.path.exists(config.LOGS_DIRECTORY):
    os.makedirs(config.LOGS_DIRECTORY)
state_file_path = getattr(config, 'STATE_FILE_PATH', os.path.join(config.LOGS_DIRECTORY, 'status.json'))
state_directory = os.path.dirname(state_file_path)
if state_directory and not os.path.exists(state_directory):
    os.makedirs(state_directory)
retry_directory = getattr(config, 'RETRY_DIRECTORY', config.LOGS_DIRECTORY)
if retry_directory and not os.path.exists(retry_directory):
    os.makedirs(retry_directory)
error_logger = setup_logger('error_logger', '/'.join([config.LOGS_DIRECTORY, 'error.log']), logging.ERROR)
info_logger = setup_logger('info_logger', '/'.join([config.LOGS_DIRECTORY, 'logs.log']))
status = PickleDB(state_file_path)

def infinite_loop(sleep_time=15):
    print("Service Running...")
    while True:
        try:
            main()
            time.sleep(sleep_time)
        except BaseException as e:
            print(e)

if __name__ == "__main__":
    infinite_loop()
