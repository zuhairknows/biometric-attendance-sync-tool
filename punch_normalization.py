"""Validate source punches without depending on devices, HTTP, or configuration files."""
import datetime as dt
import hashlib
import json
import math
import re

SOURCE_FIELDS = ('user_id', 'timestamp', 'punch', 'status', 'uid')


def import_boundary(value):
    # Fail closed on a typo: silently ignoring the boundary could replay all history.
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[0-9]{8}', value)):
        raise ValueError('IMPORT_START_DATE must be exactly YYYYMMDD or None')
    return dt.datetime.strptime(value, '%Y%m%d') if value is not None else None


def source_integer(value):
    # int(1.9) silently becomes 1. That could merge distinct records into one identity.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('Invalid source integer')
    if isinstance(value, str) and not re.fullmatch(r'[0-9]+', value):
        raise ValueError('Invalid source integer')
    result = int(value)
    if result < 0:
        raise ValueError('Invalid source integer')
    return result


def timestamp(value):
    try:
        if isinstance(value, dt.datetime):
            result = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result = dt.datetime.fromtimestamp(value)
        elif isinstance(value, str):
            result = dt.datetime.fromisoformat(value)
        else:
            raise ValueError
    except (ValueError, OverflowError, OSError):
        # datetime's default error includes the supplied input; keep errors fixed and safe.
        raise ValueError('Invalid source timestamp') from None
    # ZKTeco timestamps are local wall-clock values, as in the existing payload.
    if result.tzinfo is not None:
        raise ValueError('Timezone-aware source timestamp is unsupported')
    return result


def normalize(record, device, boundary=None, in_codes=(0, 4), out_codes=(1, 5)):
    """Return a persistable row, or None for a valid punch before the boundary."""
    source = record if isinstance(record, dict) else getattr(record, '__dict__', {})
    # Only known attendance fields are evidence; arbitrary object fields may contain secrets.
    full_evidence = {key: str(source.get(key, '')) for key in SOURCE_FIELDS}
    evidence = {key: value[:256] for key, value in full_evidence.items()}
    parsed_time = None
    user_id = str(source.get('user_id') if source.get('user_id') is not None else '')
    error = None
    try:
        parsed_time = timestamp(source.get('timestamp'))
        if boundary and parsed_time < boundary:
            return None
        if isinstance(source.get('user_id'), bool) or not isinstance(source.get('user_id'), (str, int)):
            raise ValueError('Invalid source user ID')
        if not user_id.strip() or len(user_id) > 140:
            raise ValueError('Empty or oversized source user ID')
        punch = source_integer(source['punch'])
        status = source_integer(source['status'])
        if not (0 <= punch <= 255 and 0 <= status <= 255):
            raise ValueError('Invalid source punch/status code')
        uid = source.get('uid')
        if uid is not None:
            uid = source_integer(uid)
        direction = device.get('punch_direction')
        if direction == 'AUTO':
            direction = 'OUT' if punch in out_codes else 'IN' if punch in in_codes else None
        if direction not in (None, 'IN', 'OUT'):
            raise ValueError('Invalid punch direction')
        for key, bound in (('latitude', 90), ('longitude', 180)):
            coordinate = device.get(key)
            if coordinate is not None and (not math.isfinite(float(coordinate)) or abs(float(coordinate)) > bound):
                raise ValueError('Invalid geolocation coordinate')
        payload = dict(employee_field_value=user_id, timestamp=str(parsed_time),
                       device_id=device['device_id'], log_type=direction,
                       latitude=device.get('latitude'), longitude=device.get('longitude'))
        identity = [device['device_id'], user_id, str(parsed_time), punch, status, uid]
    except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
        # Fixed messages avoid persisting user-supplied strings from exception details.
        error = str(exc) if type(exc) is ValueError and str(exc).startswith(('Invalid ', 'Empty ', 'Timezone-')) else 'Invalid biometric source record'
        payload = None
        punch, status, uid = evidence['punch'], evidence['status'], evidence['uid']
        # Keep displayed evidence bounded, but hash the full fields to avoid truncation collisions.
        identity = [device['device_id'], 'invalid', full_evidence]
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(identity_key=key, device_id=device['device_id'], user_id=user_id[:140],
                device_uid=str(uid) if uid is not None else None,
                punch_timestamp=str(parsed_time) if parsed_time else None,
                punch_code=str(punch), status_code=str(status),
                source_evidence=json.dumps(evidence, sort_keys=True),
                payload=json.dumps(payload) if payload else None,
                state='invalid_record' if error else 'pending',
                error_category='source_validation' if error else None, error_message=error)
