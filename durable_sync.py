"""Sequential device capture and bounded delivery, callable from the existing service."""
import datetime as dt
import math
import json
import os
from pathlib import Path
import sqlite3
import time

from durable_delivery import FrappeClient, deliver_batch
from punch_normalization import import_boundary, normalize
from sync_ledger import Ledger
from durable_startup import private_storage, worker_lock, validate_activation, cutover_time


class LegacyStatusSnapshot:
    """Read a legacy shift floor once; never let a broken status file block queue recovery."""
    def __init__(self, path, logger):
        self.data = {}
        self.usable = True
        path = Path(path)
        try:
            if path.exists():
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError('Oversized legacy status')
                self.data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(self.data, dict):
                    raise ValueError('Invalid legacy status')
        except (OSError, ValueError):
            self.data = {}
            self.usable = False
            logger.error('Legacy status unavailable; delivery continues, Shift Type updates require status audit')

    def get(self, key):
        return self.data.get(key)


def database_path(config):
    default = Path(os.environ.get('PROGRAMDATA', str(Path.home() / '.local' / 'share'))) / 'ERPNextBiometricPushService' / 'sync.sqlite3'
    path = Path(getattr(config, 'DURABLE_DB_PATH', default))
    if not path.is_absolute():
        raise ValueError('DURABLE_DB_PATH must be absolute')
    path = path.resolve()
    repository = Path(__file__).resolve().parent
    if path == repository or repository in path.parents:
        raise ValueError('DURABLE_DB_PATH must be outside the repository')
    return path


def delivery_options(config):
    values = dict(batch_size=getattr(config, 'DURABLE_BATCH_SIZE', 100),
                  rate=getattr(config, 'DURABLE_REQUESTS_PER_SECOND', 2),
                  base=getattr(config, 'DURABLE_RETRY_BASE_SECONDS', 30),
                  maximum=getattr(config, 'DURABLE_RETRY_MAX_SECONDS', 3600),
                  auth_cooldown=getattr(config, 'DURABLE_AUTH_COOLDOWN_SECONDS', 900),
                  lease_seconds=getattr(config, 'DURABLE_LEASE_SECONDS', 120))
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid durable delivery setting: ' + key)
    if not isinstance(values['batch_size'], int) or values['batch_size'] > 10000:
        raise ValueError('DURABLE_BATCH_SIZE must be an integer from 1 to 10000')
    timeout = getattr(config, 'ERPNEXT_REQUEST_TIMEOUT', getattr(config, 'REQUEST_TIMEOUT', 30))
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Invalid request timeout')
    if values['lease_seconds'] < timeout * 2 + 30 or values['maximum'] < values['base']:
        raise ValueError('Delivery lease too short or retry maximum below base')
    return values


def capture(ledger, engine, device, records=None):
    if device.get('clear_from_device_on_fetch'):
        raise ValueError('Durable synchronization prohibits device attendance deletion')
    boundary = import_boundary(engine.config.IMPORT_START_DATE)
    activation = validate_activation(ledger.path, engine.config)
    cutover = max(filter(None, (boundary, cutover_time(activation['cutover_time']))))
    started = str(dt.datetime.now())
    ledger.begin_fetch(device['device_id'], started)
    if records is None:
        if Path(engine.get_dump_file_name_and_directory(device['device_id'])).exists():
            engine.info_logger.warning('Legacy dump retained for device=%s; use explicit import-legacy procedure', device['device_id'])
        records = engine.get_all_attendance_from_device(device['ip'], port=device['port'],
                    password=device['password'], device_id=device['device_id'], clear_from_device_on_fetch=False)
    def retained_rows():
        for record in records:
            # Keep historical evidence, including pre-import-date records. Holding is NOT delivery.
            row = normalize(record, device, None, engine.device_punch_values_IN, engine.device_punch_values_OUT)
            if row['state'] == 'pending' and dt.datetime.fromisoformat(row['punch_timestamp']) < cutover:
                row.update(state='rejected', error_category='cutover_hold',
                           error_message='Historical punch retained outside approved live cutover scope')
            yield row
    rows = retained_rows()
    return ledger.ingest(rows, device['device_id'], fetched_at=started)


def update_shifts(ledger, engine, stop_requested=lambda: False):
    if not getattr(engine.status, 'usable', True) or ledger.destination_cooldown() > time.time():
        return
    activation = validate_activation(ledger.path, engine.config)
    boundary = max(filter(None, (import_boundary(engine.config.IMPORT_START_DATE),
                                cutover_time(activation['cutover_time']))))
    for mapping in getattr(engine.config, 'shift_type_device_mapping', []):
        watermark = ledger.safe_watermark(mapping['related_device_id'], cutover=boundary)
        if watermark is None:
            continue
        shifts = mapping['shift_type_name']
        for shift in [shifts] if isinstance(shifts, str) else shifts:
            if stop_requested():
                return
            previous = ledger.shift_watermark(shift)
            # Retain the legacy local watermark as a floor during cutover, never as evidence
            # that pending punches were delivered. Remote state must be audited before cutover.
            legacy = engine._safe_convert_date(engine.status.get(f'{shift}_sync_timestamp'), '%Y-%m-%d %H:%M:%S.%f')
            floor = max([value for value in (previous, legacy) if value], default=None)
            if floor and watermark <= floor:
                continue
            response_code = engine.send_shift_sync_to_erpnext(shift, watermark)
            if response_code == 200:
                ledger.save_shift_watermark(shift, watermark)
            else:
                options = delivery_options(engine.config)
                delay = options['auth_cooldown'] if response_code in (401, 403) else options['base']
                ledger.pause_destination(time.time() + delay)
                return


def run(engine, stop_requested=lambda: False):
    path = database_path(engine.config)
    private_storage(path)
    with worker_lock(path):
        validate_activation(path, engine.config)
        return _run_locked(engine, stop_requested)


def _run_locked(engine, stop_requested):
    config = engine.config
    engine.validate_unique_device_ids(config.devices)
    import_boundary(config.IMPORT_START_DATE)
    options = delivery_options(config)
    with Ledger(database_path(config)) as ledger:
        # This flag gates only polling. Delivery runs every service tick, including restarts.
        polling_due = ledger.polling_due([device.get('device_id') for device in config.devices], config.PULL_FREQUENCY, dt.datetime.now())
        if polling_due:
            for original in config.devices:
                if stop_requested():
                    return
                try:
                    device = engine.normalize_device_config(original)
                    report = capture(ledger, engine, device)
                    engine.info_logger.info(json.dumps(dict(event='durable_capture', device_id=device['device_id'], counts=report)))
                except (sqlite3.Error, OSError):
                    # Disk-full/corruption errors must stop the cycle, never report a healthy fetch.
                    raise
                except Exception as exc:
                    device_id = str(original.get('device_id', 'unknown'))
                    # Even a configuration failure must invalidate a previous successful scan.
                    ledger.begin_fetch(device_id, str(dt.datetime.now()))
                    engine.error_logger.error(json.dumps(dict(event='durable_scan_failed', device_id=device_id,
                                                               category=type(exc).__name__)))
        client = FrappeClient(config, engine.requests.request)
        activation = validate_activation(ledger.path, config)
        live_boundary = max(filter(None, (import_boundary(config.IMPORT_START_DATE), cutover_time(activation['cutover_time']))))
        deliver_batch(ledger, client, **options, stop_requested=stop_requested, logger=engine.info_logger,
                      not_before=live_boundary)
        if not stop_requested():
            update_shifts(ledger, engine, stop_requested)
        engine.info_logger.info(json.dumps(dict(event='durable_health', **ledger.diagnostics()), sort_keys=True))
