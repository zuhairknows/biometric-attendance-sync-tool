
import local_config as config
import requests
import datetime
import json
import os
import re
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from pickledb import PickleDB
from zk import ZK, const

EMPLOYEE_NOT_FOUND_ERROR_MESSAGE = "No Employee found for the given employee field value"
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
DEVICE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')

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

def main():
    """Takes care of checking if it is time to pull data based on config,
    then calling the relevent functions to pull data and push to EPRNext.

    """
    try:
        last_lift_off_timestamp = _safe_convert_date(status.get('lift_off_timestamp'), "%Y-%m-%d %H:%M:%S.%f")
        if (last_lift_off_timestamp and last_lift_off_timestamp < datetime.datetime.now() - datetime.timedelta(minutes=config.PULL_FREQUENCY)) or not last_lift_off_timestamp:
            status.set('lift_off_timestamp', str(datetime.datetime.now()))
            status.save()
            info_logger.info("Cleared for lift off!")
            validate_unique_device_ids(config.devices)
            for device in config.devices:
                device_attendance_logs = None
                try:
                    device = normalize_device_config(device)
                    info_logger.info("Processing Device: "+ device['device_id'])
                    dump_file = get_dump_file_name_and_directory(device['device_id'])
                    if os.path.exists(dump_file):
                        info_logger.error('Device Attendance Dump Found in Log Directory. This can mean the program crashed unexpectedly. Retrying with dumped data.')
                        with open(dump_file, 'r') as f:
                            file_contents = f.read()
                            if file_contents:
                                device_attendance_logs = read_attendance_dump(file_contents)
                    pull_process_and_push_data(device, device_attendance_logs)
                    status.set(f'{device["device_id"]}_push_timestamp', str(datetime.datetime.now()))
                    status.save()
                    if os.path.exists(dump_file):
                        os.remove(dump_file)
                    info_logger.info("Successfully processed Device: "+ device['device_id'])
                except:
                    error_logger.exception('exception when calling pull_process_and_push_data function for device'+json.dumps(redact_device_config(device), default=str))
            if hasattr(config,'shift_type_device_mapping'):
                update_shift_last_sync_timestamp(config.shift_type_device_mapping)
            status.set('mission_accomplished_timestamp', str(datetime.datetime.now()))
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
    attendance_success_log_file = '_'.join(["attendance_success_log", device['device_id']])
    attendance_failed_log_file = '_'.join(["attendance_failed_log", device['device_id']])
    attendance_success_logger = setup_logger(attendance_success_log_file, '/'.join([config.LOGS_DIRECTORY, attendance_success_log_file])+'.log')
    attendance_failed_logger = setup_logger(attendance_failed_log_file, '/'.join([config.LOGS_DIRECTORY, attendance_failed_log_file])+'.log')
    if not device_attendance_logs:
        device_attendance_logs = get_all_attendance_from_device(device['ip'], port=device['port'], password=device['password'], device_id=device['device_id'], clear_from_device_on_fetch=device['clear_from_device_on_fetch'])
        if not device_attendance_logs:
            return
    device_attendance_logs = normalize_attendance_logs(device_attendance_logs)
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
                return

    for device_attendance_log in device_attendance_logs[index_of_last+1:]:
        punch_direction = device['punch_direction']
        if punch_direction == 'AUTO':
            if device_attendance_log['punch'] in device_punch_values_OUT:
                punch_direction = 'OUT'
            elif device_attendance_log['punch'] in device_punch_values_IN:
                punch_direction = 'IN'
            else:
                punch_direction = None
        erpnext_status_code, erpnext_message = send_to_erpnext(device_attendance_log['user_id'], device_attendance_log['timestamp'], device['device_id'], punch_direction, latitude=device.get('latitude'), longitude=device.get('longitude'))
        if erpnext_status_code == 200:
            attendance_success_logger.info("\t".join([erpnext_message, str(device_attendance_log['uid']),
                str(device_attendance_log['user_id']), str(device_attendance_log['timestamp'].timestamp()),
                str(device_attendance_log['punch']), str(device_attendance_log['status']),
                json.dumps(device_attendance_log, default=str)]))
        elif is_duplicate_employee_checkin_response(erpnext_status_code, erpnext_message):
            attendance_success_logger.info("\t".join(['DUPLICATE_ALREADY_SYNCED: '+erpnext_message, str(device_attendance_log['uid']),
                str(device_attendance_log['user_id']), str(device_attendance_log['timestamp'].timestamp()),
                str(device_attendance_log['punch']), str(device_attendance_log['status']),
                json.dumps(device_attendance_log, default=str)]))
        else:
            attendance_failed_logger.error("\t".join([str(erpnext_status_code), str(device_attendance_log['uid']),
                str(device_attendance_log['user_id']), str(device_attendance_log['timestamp'].timestamp()),
                str(device_attendance_log['punch']), str(device_attendance_log['status']),
                json.dumps(device_attendance_log, default=str)]))
            if not(any(error in erpnext_message for error in allowlisted_errors)):
                raise Exception('API Call to ERPNext Failed.')


def get_all_attendance_from_device(ip, port=DEFAULT_ZK_PORT, timeout=30, password=DEFAULT_ZK_PASSWORD, device_id=None, clear_from_device_on_fetch=False):
    #  Sample Attendance Logs [{'punch': 255, 'user_id': '22', 'uid': 12349, 'status': 1, 'timestamp': datetime.datetime(2019, 2, 26, 20, 31, 29)},{'punch': 255, 'user_id': '7', 'uid': 7, 'status': 1, 'timestamp': datetime.datetime(2019, 2, 26, 20, 31, 36)}]
    port = validate_port(port)
    password = validate_password(password)
    zk = ZK(ip, port=port, timeout=timeout, password=password)
    conn = None
    attendances = []
    try:
        conn = zk.connect()
        x = conn.disable_device()
        # device is disabled when fetching data
        info_logger.info("\t".join((ip, "Device Disable Attempted. Result:", str(x))))
        attendances = conn.get_attendance()
        info_logger.info("\t".join((ip, "Attendances Fetched:", str(len(attendances)))))
        status.set(f'{device_id}_push_timestamp', None)
        status.set(f'{device_id}_pull_timestamp', str(datetime.datetime.now()))
        status.save()
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
            if clear_from_device_on_fetch:
                x = conn.clear_attendance()
                info_logger.info("\t".join((ip, "Attendance Clear Attempted. Result:", str(x))))
        x = conn.enable_device()
        info_logger.info("\t".join((ip, "Device Enable Attempted. Result:", str(x))))
    except:
        error_logger.exception(str(ip)+' exception when fetching from device...')
        raise Exception('Device fetch failed.')
    finally:
        if conn:
            conn.disconnect()
    return normalize_attendance_logs(list(map(lambda x: x.__dict__, attendances)))


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
    response = requests.request("POST", url, headers=headers, json=data, timeout=ERPNEXT_REQUEST_TIMEOUT)
    if response.status_code == 200:
        return 200, json.loads(response._content)['message']['name']
    else:
        error_str = _safe_get_error_str(response)
        if EMPLOYEE_NOT_FOUND_ERROR_MESSAGE in error_str:
            error_logger.error('\t'.join(['Error during ERPNext API Call.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type), error_str]))
            # TODO: send email?
        else:
            error_logger.error('\t'.join(['Error during ERPNext API Call.', str(employee_field_value), str(timestamp.timestamp()), str(device_id), str(log_type), error_str]))
        return response.status_code, error_str

def is_duplicate_employee_checkin_response(status_code, message):
    return status_code == 417 and DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE in message

def validate_runtime_config(config_module=None):
    config_module = config_module or config
    errors = []

    for key in ['ERPNEXT_URL', 'ERPNEXT_API_KEY', 'ERPNEXT_API_SECRET', 'LOGS_DIRECTORY']:
        if not str(getattr(config_module, key, '')).strip():
            errors.append(key+' is required.')

    erpnext_url = str(getattr(config_module, 'ERPNEXT_URL', '')).strip()
    if erpnext_url and not erpnext_url.startswith(('http://', 'https://')):
        errors.append('ERPNEXT_URL must start with http:// or https://.')

    for key in ['ERPNEXT_API_KEY', 'ERPNEXT_API_SECRET']:
        if str(getattr(config_module, key, '')).strip().startswith('YOUR_REAL_'):
            errors.append(key+' must be set to the real local credential.')

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
        except ValueError as e:
            errors.append(str(e))
        for index, device in enumerate(devices):
            try:
                normalize_device_config(device)
            except ValueError as e:
                errors.append('devices['+str(index)+']: '+str(e))

    if errors:
        raise ValueError('Invalid configuration:\n- ' + '\n- '.join(errors))

    return True

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
    return config.LOGS_DIRECTORY + '/' + device_id + '_last_fetch_dump.json'

def normalize_device_config(device):
    device_id = device.get('device_id')
    ip = device.get('ip') or device.get('host')
    if not device_id:
        raise ValueError('Device configuration is missing required device_id.')
    validate_device_id(device_id)
    if not ip:
        raise ValueError('Device configuration for device_id '+str(device_id)+' is missing required ip or host.')

    normalized_device = dict(device)
    normalized_device['device_id'] = str(device_id)
    normalized_device['ip'] = ip
    normalized_device['port'] = validate_port(device.get('port', DEFAULT_ZK_PORT))
    normalized_device['password'] = validate_password(device.get('password', DEFAULT_ZK_PASSWORD))
    normalized_device['punch_direction'] = device.get('punch_direction')
    normalized_device['clear_from_device_on_fetch'] = bool(device.get('clear_from_device_on_fetch', False))
    if normalized_device['clear_from_device_on_fetch']:
        info_logger.warning('Device '+str(device_id)+' has clear_from_device_on_fetch enabled. This can delete attendance records from the biometric device.')
    normalized_device['latitude'] = device.get('latitude')
    normalized_device['longitude'] = device.get('longitude')
    return normalized_device

def validate_device_id(device_id):
    device_id = str(device_id)
    if not DEVICE_ID_PATTERN.match(device_id):
        raise ValueError('Device ID '+device_id+' is invalid. Use only letters, numbers, underscore, and hyphen.')
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
        raise ValueError('Duplicate device_id values found: '+', '.join(sorted(set(duplicate_device_ids))))

def redact_device_config(device):
    redacted_device = dict(device)
    if 'password' in redacted_device:
        redacted_device['password'] = '***'
    return redacted_device

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
error_logger = setup_logger('error_logger', '/'.join([config.LOGS_DIRECTORY, 'error.log']), logging.ERROR)
info_logger = setup_logger('info_logger', '/'.join([config.LOGS_DIRECTORY, 'logs.log']))
status = PickleDB('/'.join([config.LOGS_DIRECTORY, 'status.json']))

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
