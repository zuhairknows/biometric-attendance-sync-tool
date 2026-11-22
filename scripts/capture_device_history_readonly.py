"""Capture decoded ZKTeco evidence only. Dry-run by default; no synchronization imports."""
import argparse
import ast
import contextlib
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import uuid

# Also supports a reviewed diagnostic-only bundle with the same scripts/ layout.
sys.dont_write_bytecode = True  # Do not create SDK/helper caches in the production environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_history_safety import (CaptureError, finalize_no_replace, preflight_output,
                                          require_stopped, service_state)

ALLOWED_DEVICES = ('FP2_DEVICE_08', 'FP2_DEVICE_11')
CONNECTION_FIELDS = ('device_id', 'ip', 'host', 'port', 'password')
SOURCE_FIELDS = ('user_id', 'timestamp', 'uid', 'punch', 'status')
SENSITIVE_KEY = re.compile(r'password|secret|token|authorization|credential|api[_-]?key', re.I)


@dataclass(frozen=True)
class Device:
    device_id: str
    host: str
    port: int
    password: int = field(repr=False)

    def metadata(self):
        return dict(device_id=self.device_id, host=self.host, port=self.port)


def approved_devices(config_path, selected):
    """Read only literal device properties. Never import/execute the Python configuration."""
    path = Path(config_path)
    if path.stat().st_size > 1024 * 1024:
        raise CaptureError('configuration_too_large')
    try:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    except (SyntaxError, ValueError):
        raise CaptureError('configuration_syntax_invalid') from None
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'devices' for target in node.targets)]
    if len(assignments) != 1 or not isinstance(assignments[0].value, (ast.List, ast.Tuple)):
        raise CaptureError('device_configuration_must_be_literal')
    for node in tree.body:
        if node is not assignments[0] and any(isinstance(child, ast.Name) and child.id == 'devices' for child in ast.walk(node)):
            raise CaptureError('dynamic_device_configuration_unsupported')
    configured = {}
    for item in assignments[0].value.elts:
        if not isinstance(item, ast.Dict):
            raise CaptureError('device_configuration_must_be_literal')
        nodes = {}
        for key, value in zip(item.keys, item.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                raise CaptureError('device_configuration_must_be_literal')
            if key.value in CONNECTION_FIELDS:
                if key.value in nodes:
                    raise CaptureError('duplicate_device_property')
                nodes[key.value] = value
        try:
            device_id = ast.literal_eval(nodes.get('device_id'))
        except (ValueError, TypeError):
            raise CaptureError('invalid_configured_device_id') from None
        if not isinstance(device_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', device_id):
            raise CaptureError('invalid_configured_device_id')
        if device_id in configured:
            raise CaptureError('duplicate_configured_device_id')
        configured[device_id] = nodes
    result = []
    for device_id in dict.fromkeys(selected):
        if device_id not in configured:
            raise CaptureError('unknown_device_id')
        if device_id not in ALLOWED_DEVICES:
            raise CaptureError('unapproved_device_id')
        try:
            # Do not even evaluate passwords/connection settings for unselected devices.
            properties = {key: ast.literal_eval(value) for key, value in configured[device_id].items()}
        except (ValueError, TypeError):
            raise CaptureError('device_configuration_must_be_literal') from None
        host = properties.get('ip') or properties.get('host')
        try:
            if not isinstance(host, str) or not isinstance(ipaddress.ip_address(host), ipaddress.IPv4Address):
                raise ValueError
            if properties.get('ip') and properties.get('host') and properties['ip'] != properties['host']:
                raise ValueError
            port = properties.get('port', 4370)
            password = properties.get('password', 0)
            if type(port) is not int or not 1 <= port <= 65535 or type(password) is not int or not 0 <= password <= 0xFFFFFFFF:
                raise ValueError
        except ValueError:
            raise CaptureError('invalid_device_connection_properties') from None
        result.append(Device(device_id, host, port, password))
    return result


def local_datetime(value):
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise ValueError('Only local wall-clock datetimes are supported')
    return parsed


def source_timestamp(value):
    if isinstance(value, dt.datetime):
        result = value
    elif isinstance(value, str):
        result = local_datetime(value)
    elif type(value) in (int, float) and math.isfinite(value):
        result = dt.datetime.fromtimestamp(value)
    else:
        raise ValueError
    if result.tzinfo is not None:
        raise ValueError
    return result


def encode_raw(value, issues, seen=None):
    """Tagged JSON preserves common Python field types without unsafe repr/pickle calls."""
    seen = set() if seen is None else seen
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else {'__type__': 'float', 'ieee754_hex': struct.pack('>d', value).hex()}
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return {'__type__': type(value).__name__, 'iso': value.isoformat(), 'fold': getattr(value, 'fold', 0)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'__type__': type(value).__name__, 'hex': bytes(value).hex()}
    if id(value) in seen:
        issues.add('cyclic_source_value')
        return {'__type__': 'cycle', 'representation_unavailable': True}
    seen.add(id(value))
    try:
        if isinstance(value, dict):
            entries = []
            for key, item in value.items():
                if isinstance(key, str) and SENSITIVE_KEY.search(key):
                    issues.add('sensitive_field_redacted')
                    encoded = {'__redacted__': True}
                else:
                    encoded = encode_raw(item, issues, seen)
                entries.append((encode_raw(key, issues, seen), encoded))
            if all(type(key) is str for key in value):
                return dict(entries)
            return {'__type__': 'mapping', 'entries': entries}
        if isinstance(value, (list, tuple, set, frozenset)):
            return {'__type__': type(value).__name__, 'items': [encode_raw(item, issues, seen) for item in value]}
        issues.add('unsupported_source_type')
        return {'__type__': type(value).__name__, 'representation_unavailable': True}
    finally:
        seen.remove(id(value))


def record_evidence(record, index, reference_time):
    issues = set()
    fields = record if isinstance(record, dict) else getattr(record, '__dict__', None)
    if not isinstance(fields, dict):
        fields = {}
        issues.add('invalid_record_shape')
    raw = encode_raw(fields if fields else record, issues)
    normalized = {}
    user_id = fields.get('user_id')
    if type(user_id) not in (str, int):
        issues.add('invalid_user_id_type')
    elif not str(user_id).strip():
        issues.add('empty_user_id')
    else:
        normalized['user_id'] = str(user_id)
    try:
        timestamp = source_timestamp(fields.get('timestamp'))
        normalized['timestamp'] = timestamp.isoformat(sep=' ', timespec='microseconds')
        if timestamp < dt.datetime(2000, 1, 1):
            issues.add('timestamp_before_device_epoch')
        if timestamp > reference_time + dt.timedelta(minutes=5):
            issues.add('future_timestamp')
    except (ValueError, TypeError, OverflowError, OSError):
        issues.add('invalid_timestamp')
    for name in ('uid', 'punch', 'status'):
        value = fields.get(name)
        if name == 'uid' and value is None:
            normalized[name] = None  # UID is optional and never a deduplication key.
        elif type(value) is int and value >= 0 and (name == 'uid' or value <= 255):
            normalized[name] = value
        else:
            issues.add('invalid_' + name)
    return dict(source_order_index=index, raw=raw, normalized=normalized,
                valid=not issues, validation_categories=sorted(issues),
                raw_complete=not issues.intersection({'unsupported_source_type', 'cyclic_source_value', 'sensitive_field_redacted'}))


def atomic_json(directory, name, write_content):
    fd, temporary = tempfile.mkstemp(prefix='.capture-', suffix='.part', dir=directory)
    # On failure keep protected .part evidence; never give it a successful final filename.
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        result = write_content(stream)
        stream.flush()
        os.fsync(stream.fileno())
    destination = directory / name
    finalize_no_replace(Path(temporary), destination)
    digest = hashlib.sha256()
    with destination.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return destination, digest.hexdigest(), result


def safe_sdk_factory(device, timeout):
    # Reuse only the existing *checker*. Never install/patch pyzk automatically.
    from scripts.apply_pyzk_speedface_patch import validate_environment, is_patched
    try:
        _, _, source = validate_environment()
        if not is_patched(source):
            raise CaptureError('speedface_compatibility_unverified')
        from zk import ZK
        return ZK(device.host, port=device.port, timeout=timeout, password=device.password,
                  verbose=False, ommit_ping=True)
    except Exception:
        raise CaptureError('speedface_compatibility_unverified') from None


def read_device(device, timeout, pause, factory, check_service):
    client = connection = records = None
    failure = None
    cleanup = dict(reenable='not_requested', disconnect='not_connected')
    pause_attempted = False
    connect_attempted = False
    # Some SDKs print user records/passwords even on exceptions. Do not expose that output.
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            client = factory(device, timeout)
            require_stopped(check_service)  # Recheck after ACL/SDK preflight, immediately before connect.
            connect_attempted = True
            connection = client.connect()
            if connection is None:
                raise CaptureError('connection_failed')
            if pause:
                # The response could be lost after disable succeeds. Always attempt restore.
                pause_attempted = True
                if connection.disable_device() is not True:
                    raise CaptureError('device_pause_unconfirmed')
            records = connection.get_attendance()
            if records is None:
                raise CaptureError('retrieval_returned_no_dataset')
            if not isinstance(records, (list, tuple)):
                records = None
                raise CaptureError('unsupported_dataset_shape')
        except CaptureError as exc:
            failure = str(exc)
        except Exception:
            failure = 'device_read_failed'
        finally:
            if pause_attempted:
                cleanup['reenable'] = 'failed'
                try:
                    if connection.enable_device() is True:
                        cleanup['reenable'] = 'confirmed'
                except Exception:
                    pass
            target = connection if connection is not None else client
            if target is not None:
                if connect_attempted and (connection is not None or getattr(target, 'is_connect', False)):
                    try:
                        if target.disconnect() is not True:
                            raise CaptureError('disconnect_failed')
                        cleanup['disconnect'] = 'confirmed'
                    except Exception:
                        cleanup['disconnect'] = 'failed'
                elif connect_attempted:
                    # pyzk refuses CMD_EXIT when connect never established a session. Close
                    # its local socket instead; an ordinary failed connect must not block 11.
                    cleanup['disconnect'] = 'connect_unconfirmed'
                # Also close the local SDK socket if connect/disconnect raised before cleanup.
                sock = getattr(target, '_ZK__sock', None)
                if sock is not None:
                    try:
                        sock.close()
                        if cleanup['disconnect'] == 'connect_unconfirmed':
                            cleanup['disconnect'] = 'connect_unconfirmed_local_socket_closed'
                    except Exception:
                        cleanup['disconnect'] = 'failed'
                elif cleanup['disconnect'] == 'connect_unconfirmed':
                    cleanup['disconnect'] = 'failed'
    return records, failure, cleanup


def capture_one(device, directory, args, factory, check_service):
    started = dt.datetime.now().astimezone()
    report = dict(device.metadata(), status='failed', capture_started_at=started.isoformat())
    try:
        require_stopped(check_service)
        preflight_output(directory, live=True, acl_check=args.acl_check)
        print(json.dumps(dict(event='capture_starting', **device.metadata())), file=sys.stderr, flush=True)
        records, failure, cleanup = read_device(device, args.timeout, args.pause_device, factory, check_service)
        report['cleanup'] = cleanup
        if failure:
            report['error_category'] = failure
        if records is not None:
            report['fetched_count_from_sdk'] = len(records)
            # Retrieval and device cleanup finish before potentially slow serialization/disk I/O.
            counts = dict(total_fetched=0, valid=0, invalid=0, raw_incomplete=0, validation_counts={}, valid_in_requested_range=0,
                          oldest_valid_punch=None, newest_valid_punch=None,
                          oldest_valid_in_range=None, newest_valid_in_range=None)
            reference = dt.datetime.now()
            def write(stream):
                header = dict(device.metadata(), schema='zkteco-offline-capture-v1',
                              capture_started_at=started.isoformat(), pause_requested=args.pause_device,
                              requested_range=dict(start=str(args.start) if args.start else None, end=str(args.end) if args.end else None),
                              device_cleanup=cleanup)
                stream.write(json.dumps(header, ensure_ascii=True)[:-1] + ',"records":[')
                for index, record in enumerate(records):
                    try:
                        evidence = record_evidence(record, index, reference)
                    except Exception:
                        evidence = dict(source_order_index=index, raw={'representation_unavailable': True},
                                        normalized={}, valid=False, raw_complete=False, validation_categories=['record_serialization_failed'])
                    if index:
                        stream.write(',')
                    stream.write(json.dumps(evidence, ensure_ascii=True, allow_nan=False))
                    counts['total_fetched'] += 1
                    counts['valid' if evidence['valid'] else 'invalid'] += 1
                    counts['raw_incomplete'] += int(not evidence['raw_complete'])
                    for category in evidence['validation_categories']:
                        counts['validation_counts'][category] = counts['validation_counts'].get(category, 0) + 1
                    if evidence['valid']:
                        value = evidence['normalized']['timestamp']
                        counts['oldest_valid_punch'] = min(counts['oldest_valid_punch'] or value, value)
                        counts['newest_valid_punch'] = max(counts['newest_valid_punch'] or value, value)
                        parsed = dt.datetime.fromisoformat(value)
                        if (args.start is None or parsed >= args.start) and (args.end is None or parsed <= args.end):
                            counts['valid_in_requested_range'] += 1
                            counts['oldest_valid_in_range'] = min(counts['oldest_valid_in_range'] or value, value)
                            counts['newest_valid_in_range'] = max(counts['newest_valid_in_range'] or value, value)
                stream.write('],"capture_finished_at":' + json.dumps(dt.datetime.now().astimezone().isoformat()))
                stream.write(',"summary":' + json.dumps(counts) + '}\n')
            name = device.device_id + '_' + started.strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex + '.json'
            destination, digest, _ = atomic_json(directory, name, write)
            report.update(counts, evidence_file=destination.name, sha256=digest)
            if failure is None:
                report['status'] = 'captured'
        require_stopped(check_service)
    except CaptureError as exc:
        report.update(status='failed', error_category=str(exc))
    except Exception:
        report.update(status='failed', error_category='capture_or_persistence_failed')
    if any(value == 'failed' for value in report.get('cleanup', {}).values()):
        report.update(status='failed', error_category='device_cleanup_failed',
                      manual_action='Verify terminal enabled and all sessions released before continuing or restoring the service')
    report['capture_finished_at'] = dt.datetime.now().astimezone().isoformat()
    return report


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unrecognized arguments, including accidental passwords.
        raise CaptureError('invalid_arguments_use_help_no_ip_port_or_password_overrides')


def main(argv=None, *, factory=safe_sdk_factory, check_service=service_state, acl_check=None):
    from scripts.capture_history_safety import check_directory_acl
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--config', default=str(Path(__file__).resolve().parents[1] / 'local_config.py'))
    parser.add_argument('--devices', nargs='+', default=list(ALLOWED_DEVICES))
    parser.add_argument('--output', required=True, help='Existing private absolute directory outside the checkout')
    parser.add_argument('--start', type=local_datetime, help='Inclusive local ISO datetime; affects summary only')
    parser.add_argument('--end', type=local_datetime, help='Inclusive local ISO datetime; affects summary only')
    parser.add_argument('--timeout', type=int, default=10, help='Socket timeout seconds, 1..30 (not a whole-capture deadline)')
    authorization = parser.add_mutually_exclusive_group()
    authorization.add_argument('--capture', action='store_true', help='Live read only during an explicitly approved maintenance window')
    authorization.add_argument('--dry-run', action='store_true', help='Default: no SDK import, connection or file writes')
    parser.add_argument('--maintenance-window-confirmed', action='store_true', help='Required with --capture; operator confirms separate approval and session isolation')
    parser.add_argument('--pause-device', action='store_true', help='Optional temporary suspension; re-enable attempted in finally; include in maintenance approval')
    try:
        args = parser.parse_args(argv)
        args.acl_check = acl_check or check_directory_acl
        if not 1 <= args.timeout <= 30 or (args.start and args.end and args.start > args.end):
            raise CaptureError('invalid_timeout_or_datetime_range')
        if args.capture and not args.maintenance_window_confirmed:
            raise CaptureError('approved_maintenance_window_confirmation_required')
        devices = approved_devices(args.config, args.devices)
        directory = preflight_output(args.output, live=False, acl_check=args.acl_check)
        if not args.capture:
            try:
                state = check_service()
            except Exception:
                state = 'UNKNOWN'
            print(json.dumps(dict(mode='dry-run', devices=[d.metadata() for d in devices],
                output_directory=str(directory), service_state=state,
                live_capture_allowed_now=state == 'STOPPED',
                maintenance_approval_and_other_session_checks='required_before_capture',
                pause_requested=args.pause_device, writes_performed=False), indent=2))
            return 0
        require_stopped(check_service)
        reports = []
        for device in devices:
            report = capture_one(device, directory, args, factory, check_service)
            reports.append(report)
            if report.get('error_category') == 'device_cleanup_failed':
                break  # Operator must address a possibly disabled terminal before proceeding.
        summary = dict(mode='capture', status='complete' if all(r['status'] == 'captured' for r in reports) else 'failed',
                       devices=reports, all_selected_devices_captured=len(reports) == len(devices) and all(r['status'] == 'captured' for r in reports),
                       date_filter_affects_summary_only=True)
        name = 'capture_summary_' + dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex + '.json'
        destination, digest, _ = atomic_json(directory, name, lambda stream: json.dump(summary, stream, indent=2))
        summary.update(summary_file=destination.name, summary_sha256=digest)
        print(json.dumps(summary, indent=2))
        return 0 if summary['status'] == 'complete' else 1
    except CaptureError as exc:
        print(json.dumps(dict(status='failed', error_category=str(exc))))
        return 1
    except KeyboardInterrupt:
        print(json.dumps(dict(status='failed', error_category='operator_interrupted_verify_terminal_and_sessions')))
        return 130
    except Exception:
        print(json.dumps(dict(status='failed', error_category='preflight_or_summary_persistence_failed')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
