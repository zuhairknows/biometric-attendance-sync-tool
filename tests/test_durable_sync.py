"""Offline acceptance tests: temporary SQLite only; never import real local_config."""
import contextlib
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from durable_delivery import FrappeClient, backoff, classify, deliver_batch, retry_after
from durable_sync import capture, database_path, delivery_options, run, update_shifts
from legacy_import import import_dump
from punch_normalization import import_boundary, normalize
from sync_cli import main as cli
from sync_ledger import Ledger
from test_erpnext_sync import load_sync_module, FakeAttendance, FakeZK

DEVICE = dict(device_id='FP1_DEVICE_01', ip='192.0.2.10', port=4370,
              password=0, punch_direction=None, clear_from_device_on_fetch=False)
PUNCH_TIME = dt.datetime(2026, 10, 13, 8)
ROOT = Path(__file__).resolve().parents[1]


def record(uid=1, **changes):
    result = dict(user_id='100', timestamp=PUNCH_TIME, punch=0, status=1, uid=uid)
    result.update(changes)
    return result


def response(status=200, message=None, headers=None):
    content = {'message': {'name': 'CHECKIN-0001'}} if message is None else {'exception': message}
    return types.SimpleNamespace(status_code=status, _content=json.dumps(content).encode(), headers=headers or {})


class DurableSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='biometric-phase1-tests-')
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.ledger = Ledger(self.path)
        self.config = types.SimpleNamespace(ERPNEXT_URL='https://erp.example.test', ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY='TEST_KEY', ERPNEXT_API_SECRET='TEST_SECRET', ERPNEXT_REQUEST_TIMEOUT=30)
        self.request = mock.Mock(return_value=response())
        self.client = FrappeClient(self.config, self.request)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def ingest(self, records=None, device=None):
        device = device or DEVICE
        return self.ledger.ingest((normalize(row, device) for row in (records if records is not None else [record()])),
                                  device['device_id'], fetched_at='2026-10-13 09:00:00')

    def batch(self, **options):
        options.setdefault('clock', lambda: 1000.0)
        options.setdefault('sleep', lambda _: None)
        return deliver_batch(self.ledger, self.client, **options)

    def punch(self, index=0):
        return self.ledger.db.execute('SELECT * FROM punches ORDER BY id').fetchall()[index]

    def test_persisted_before_api_delivery_and_success_name(self):
        self.ingest()
        def send(*args, **kwargs):
            with Ledger(self.path, readonly=True) as observer:
                self.assertEqual(observer.db.execute('SELECT state FROM punches').fetchone()[0], 'delivering')
            return response()
        self.request.side_effect = send
        self.assertEqual(self.batch(), 1)
        self.assertEqual(self.punch()['state'], 'synced')
        self.assertEqual(self.punch()['checkin_name'], 'CHECKIN-0001')

    def test_repeated_ingestion_and_large_history_do_not_post_again(self):
        self.ingest([record(uid=i, timestamp=PUNCH_TIME + dt.timedelta(seconds=i)) for i in range(2000)])
        self.batch(batch_size=2000)
        self.ingest([record(uid=i, timestamp=PUNCH_TIME + dt.timedelta(seconds=i)) for i in range(2000)])
        self.assertEqual(self.ledger.diagnostics()['total'], 2000)
        self.assertEqual(self.batch(), 0)
        self.assertEqual(self.request.call_count, 2000)

    def test_distinct_source_attributes_do_not_merge(self):
        self.ingest([record(), record(uid=2), record(punch=1), record(status=2), record(user_id='101')])
        self.assertEqual(self.ledger.diagnostics()['total'], 5)

    def test_device_identity_survives_ip_and_port_changes(self):
        self.ingest()
        self.ingest(device=dict(DEVICE, ip='192.0.2.20', port=4378))
        self.assertEqual(self.ledger.diagnostics()['total'], 1)
        self.ingest(device=dict(DEVICE, device_id='FP2_DEVICE_08'))
        self.assertEqual(self.ledger.diagnostics()['total'], 2)

    def test_recognized_duplicate_is_complete_but_unrelated_417_is_rejected(self):
        self.ingest([record(), record(uid=2)])
        self.request.side_effect = [response(417, 'This employee already has a log with the same timestamp'), response(417, 'Invalid log type')]
        self.assertEqual(self.batch(), 2)
        self.assertEqual(self.punch()['state'], 'duplicate')
        self.assertEqual(self.punch(1)['state'], 'rejected')
        self.assertEqual(self.batch(), 0)

    def test_server_errors_retry_and_stop_destination_batch(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                self.ledger.db.execute('DELETE FROM delivery_attempts')
                self.ledger.db.execute('DELETE FROM punches')
                self.ledger.db.execute('UPDATE destination_state SET cooldown_until=0,prefer_retry=0,prefer_recent=0')
                self.ledger.db.commit()
                self.ingest([record(), record(uid=2)])
                self.request.reset_mock()
                self.request.return_value = response(code, 'Internal error')
                self.assertEqual(self.batch(), 1)
                self.assertEqual(self.punch()['state'], 'retry')
                self.assertEqual(self.punch(1)['state'], 'pending')
                self.assertGreater(self.punch()['next_retry'], 1000)
                self.assertEqual(self.batch(), 0)
                self.assertEqual(self.request.call_count, 1)

    def test_network_timeouts_are_durable_and_sanitized(self):
        self.ingest()
        self.request.side_effect = TimeoutError('Authorization: TEST_SECRET')
        self.batch()
        self.assertEqual(self.punch()['state'], 'retry')
        self.assertNotIn('TEST_SECRET', self.punch()['error_message'])

    def test_429_retry_after_seconds_and_date(self):
        self.ingest()
        self.request.return_value = response(429, 'Rate limited', {'Retry-After': '120'})
        self.batch()
        self.assertEqual(self.punch()['next_retry'], 1120)
        self.assertEqual(retry_after(response(headers={'Retry-After': 'Thu, 01 Jan 1970 00:20:00 GMT'}), 1000), 200)
        self.assertEqual(retry_after(response(headers={'Retry-After': 'broken'}), 1000), 0)

    def test_408_is_retryable(self):
        self.assertEqual(classify(408, {})[0], 'retry')

    def test_authentication_cooldown_survives_restart(self):
        for code in (401, 403):
            with self.subTest(code=code):
                self.ledger.db.execute('UPDATE destination_state SET cooldown_until=0')
                self.ledger.db.commit()
                self.ingest([record(uid=code), record(uid=code + 10)])
                self.request.return_value = response(code, 'Forbidden TEST_SECRET')
                self.batch()
                with Ledger(self.path) as restarted:
                    self.assertIsNone(restarted.claim(now=1001))
                    self.assertEqual(restarted.db.execute('SELECT cooldown_until FROM destination_state').fetchone()[0], 1900)

    def test_unmatched_and_inactive_employee_quarantine_does_not_halt_later_record(self):
        self.ingest([record(user_id='unmatched'), record(uid=2, user_id='inactive'), record(uid=3, user_id='later')])
        outcomes = {'unmatched': response(417, 'No Employee found for attendance_device_id: unmatched'),
                    'inactive': response(417, 'Transactions cannot be created for an Inactive Employee'),
                    'later': response()}
        self.request.side_effect = lambda *args, **kwargs: outcomes[kwargs['json']['employee_field_value']]
        self.assertEqual(self.batch(), 3)
        self.assertEqual([self.punch(i)['state'] for i in range(3)], ['unmatched_employee', 'rejected', 'synced'])

    def test_malformed_timestamp_and_empty_user_do_not_stop_later_records(self):
        self.ingest([record(timestamp='bad'), record(uid=2, user_id=''), record(uid=3)])
        self.assertEqual(self.batch(), 1)
        self.assertEqual([self.punch(i)['state'] for i in range(3)], ['invalid_record', 'invalid_record', 'synced'])

    def test_import_start_boundary_is_inclusive_and_bad_config_fails_closed(self):
        boundary = import_boundary('20261013')
        self.assertIsNone(normalize(record(timestamp=dt.datetime(2026, 10, 12, 23, 59)), DEVICE, boundary))
        self.assertEqual(normalize(record(timestamp=boundary), DEVICE, boundary)['state'], 'pending')
        with self.assertRaises(ValueError):
            import_boundary('2026-10-13')

    def test_committed_punch_survives_process_exit(self):
        # Run an actual child process with os._exit immediately after committed ingestion.
        script = "import sys,os; from sync_ledger import Ledger; from punch_normalization import normalize; db=Ledger(sys.argv[1]); db.ingest([normalize({'user_id':'100','timestamp':'2026-10-13 08:00:00','punch':0,'status':1,'uid':1},{'device_id':'FP1_DEVICE_01'})],'FP1_DEVICE_01'); os._exit(23)"
        result = subprocess.run([sys.executable, '-c', script, str(self.path)], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.punch()['state'], 'pending')

    def test_crash_after_remote_commit_resolves_by_duplicate_after_lease(self):
        self.ingest()
        self.request.side_effect = SystemExit('crash after server commits')
        with self.assertRaises(SystemExit):
            self.batch()
        self.assertEqual(self.punch()['state'], 'delivering')
        self.request.side_effect = None
        self.request.return_value = response(417, 'This employee already has a log with the same timestamp')
        with Ledger(self.path) as restarted:
            self.assertIsNone(restarted.claim(now=1100))
            row = restarted.claim(now=1121)
            outcome, _ = self.client.send(json.loads(row['payload']), 1121)
            restarted.finish(row, outcome, now=1121)
        self.assertEqual(self.punch()['state'], 'duplicate')
        self.assertEqual(self.punch()['attempt_count'], 2)

    def test_restart_resumes_pending_automatically(self):
        self.ingest()
        with Ledger(self.path) as restarted:
            self.assertEqual(deliver_batch(restarted, self.client, sleep=lambda _: None), 1)
        self.assertEqual(self.punch()['state'], 'synced')

    def test_log_rotation_cannot_change_delivery_checkpoint(self):
        self.ingest()
        self.batch()
        logs = Path(self.temp.name) / 'attendance_success_log_FP1_DEVICE_01.log'
        logs.write_text('old checkpoint')
        logs.rename(logs.with_suffix('.log.1'))
        logs.write_text('')
        self.ingest()
        self.assertEqual(self.batch(), 0)

    def test_thousands_pending_are_processed_in_bounded_batches(self):
        self.ingest([record(uid=i) for i in range(4000)])
        self.assertEqual(self.batch(batch_size=13), 13)
        self.assertEqual(self.ledger.diagnostics()['states']['pending'], 3987)
        plan = self.ledger.db.execute("EXPLAIN QUERY PLAN SELECT * FROM punches WHERE state='pending' AND next_retry<=1000 ORDER BY next_retry,id LIMIT 1").fetchall()
        self.assertTrue(any('due_punches' in row[3] for row in plan))

    def test_new_and_retry_work_are_fair(self):
        self.ingest([record(uid=i) for i in range(8)])
        self.ledger.db.execute("UPDATE punches SET state='retry' WHERE id<=4")
        self.ledger.db.commit()
        claims = []
        for _ in range(4):
            row = self.ledger.claim(now=1000)
            claims.append(row['id'])
            self.ledger.finish(row, ('synced', 200, None, None, 'TEST'), now=1000)
        self.assertEqual(claims, [5, 1, 8, 2])

    def test_fresh_pending_captures_progress_in_an_untouched_outage_backlog(self):
        self.ingest([record(uid=i) for i in range(8)])
        self.ingest([record(uid=9, timestamp=PUNCH_TIME + dt.timedelta(hours=1))])
        claims = []
        for _ in range(4):
            row = self.ledger.claim(now=1000)
            claims.append(row['id'])
            self.ledger.finish(row, ('synced', 200, None, None, 'TEST'), now=1000)
        self.assertEqual(claims, [1, 9, 2, 8])

    def test_legacy_migration_is_idempotent_nondestructive_and_date_filtered(self):
        path = Path(self.temp.name) / 'FP1_DEVICE_01_last_fetch_dump.json'
        original = json.dumps({'device_id': DEVICE['device_id'], 'attendances': [
            record(timestamp=PUNCH_TIME.timestamp()), record(uid=2, timestamp='bad'),
            record(uid=3, timestamp=dt.datetime(2026, 10, 12).timestamp())]}).encode()
        path.write_bytes(original)
        boundary = import_boundary('20261013')
        preview = import_dump(None, path, DEVICE, boundary)
        self.assertEqual((preview['imported'], preview['invalid'], preview['excluded']), (2, 1, 1))
        self.assertEqual(self.ledger.diagnostics()['total'], 0)
        first = import_dump(self.ledger, path, DEVICE, boundary, apply=True)
        second = import_dump(self.ledger, path, DEVICE, boundary, apply=True)
        self.assertEqual(first['imported'], 2)
        self.assertEqual(second['already_known'], 2)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM legacy_imports').fetchone()[0], 1)
        self.assertIsNone(self.ledger.safe_watermark([DEVICE['device_id']]))

    def test_corrupted_partial_legacy_dump_is_retained_and_reported(self):
        path = Path(self.temp.name) / 'FP1_DEVICE_01_last_fetch_dump.json'
        original = b'{"attendances": [{"user_id": "100"},'
        path.write_bytes(original)
        report = import_dump(self.ledger, path, DEVICE, apply=True)
        self.assertEqual(report['failed'], 1)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.ledger.diagnostics()['total'], 0)

    def test_wrong_device_legacy_dump_cannot_be_imported(self):
        path = Path(self.temp.name) / 'dump.json'
        path.write_text(json.dumps({'device_id': 'other', 'attendances': []}))
        self.assertEqual(import_dump(self.ledger, path, DEVICE, apply=True)['failed'], 1)

    def test_persistence_failure_rolls_back_ingestion_and_migration_metadata(self):
        self.ledger.begin_fetch(DEVICE['device_id'], '2026-10-13 09:00:00')
        self.ledger.db.execute("CREATE TRIGGER disk_full BEFORE INSERT ON punches WHEN NEW.device_uid='2' BEGIN SELECT RAISE(ABORT,'disk full'); END")
        self.ledger.db.commit()
        with self.assertRaises(sqlite3.Error):
            self.ingest([record(), record(uid=2)])
        self.assertEqual(self.ledger.diagnostics()['total'], 0)
        self.assertEqual(self.ledger.db.execute('SELECT fetch_ok FROM device_state').fetchone()[0], 0)
        path = Path(self.temp.name) / 'dump.json'
        path.write_text(json.dumps([record(timestamp=str(PUNCH_TIME)), record(uid=2, timestamp=str(PUNCH_TIME))]))
        self.assertEqual(import_dump(self.ledger, path, DEVICE, apply=True)['failed'], 1)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM legacy_imports').fetchone()[0], 0)

    def test_watermark_never_passes_any_unresolved_or_unknown_timestamp(self):
        self.ingest()
        self.assertLess(self.ledger.safe_watermark([DEVICE['device_id']]), PUNCH_TIME)
        for state in ('pending', 'retry', 'delivering', 'unmatched_employee', 'rejected', 'invalid_record'):
            with self.subTest(state=state):
                self.ledger.db.execute('UPDATE punches SET state=?', (state,))
                self.ledger.db.commit()
                self.assertLess(self.ledger.safe_watermark([DEVICE['device_id']]), PUNCH_TIME)
        self.ledger.db.execute("UPDATE punches SET state='synced'")
        self.ledger.db.commit()
        self.assertEqual(self.ledger.safe_watermark([DEVICE['device_id']]), dt.datetime(2026, 10, 13, 9))
        self.ingest([record(uid=2, timestamp='bad')])
        self.assertIsNone(self.ledger.safe_watermark([DEVICE['device_id']]))

    def test_watermark_requires_all_mapped_devices_and_successful_latest_scan(self):
        self.ingest()
        self.batch()
        self.assertIsNone(self.ledger.safe_watermark([DEVICE['device_id'], 'FP2_DEVICE_08']))
        self.ledger.begin_fetch(DEVICE['device_id'], '2026-10-13 10:00:00')
        self.assertIsNone(self.ledger.safe_watermark([DEVICE['device_id']]))

    def test_http_payload_semantics_and_erpnext_version_selection(self):
        row = normalize(record(), dict(DEVICE, punch_direction='AUTO', latitude=12.34, longitude=56.78))
        self.client.send(json.loads(row['payload']), 1000)
        self.assertEqual(self.request.call_args.kwargs['json'], dict(employee_field_value='100',
            timestamp='2026-10-13 08:00:00', device_id=DEVICE['device_id'], log_type='IN', latitude=12.34, longitude=56.78))
        self.assertIn('/hrms.hr.', self.request.call_args.args[1])
        self.config.ERPNEXT_VERSION = 13
        self.assertIn('/erpnext.hr.', FrappeClient(self.config, self.request).url)

    def test_unsupported_schema_is_not_modified(self):
        self.ledger.db.execute('UPDATE schema_version SET version=99')
        self.ledger.db.commit()
        original = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Unsupported'):
            Ledger(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_interrupted_sqlite_transaction_rolls_back_on_process_death(self):
        script = "import sys,os; from sync_ledger import Ledger; db=Ledger(sys.argv[1]); db.db.execute('BEGIN IMMEDIATE'); db.db.execute('UPDATE schema_version SET version=99'); os._exit(24)"
        result = subprocess.run([sys.executable, '-c', script, str(self.path)], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 24, result.stderr)
        with Ledger(self.path) as recovered:
            self.assertEqual(recovered.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(recovered.diagnostics()['schema_version'], 1)

    def test_diagnostic_cli_is_readonly_and_backup_consistent(self):
        self.ingest()
        original = self.path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli(['--db', str(self.path), 'status']), 0)
        self.assertEqual(json.loads(output.getvalue())['total'], 1)
        self.assertNotIn('user_id', output.getvalue())
        self.assertEqual(self.path.read_bytes(), original)
        backup = Path(self.temp.name) / 'backup.sqlite3'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli(['--db', str(self.path), 'backup', '--output', str(backup)]), 0)
        with Ledger(backup, readonly=True) as copied:
            self.assertEqual(copied.diagnostics()['total'], 1)
        missing = Path(self.temp.name) / 'missing.sqlite3'
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli(['--db', str(missing), 'status']), 1)
        self.assertFalse(missing.exists())

    def test_backoff_is_bounded_and_attempt_history_is_bounded(self):
        self.assertEqual(backoff(1000000, jitter=lambda: 1), 3600)
        self.assertEqual(backoff(1, jitter=lambda: 0), 30)
        self.ingest()
        for index in range(25):
            row = self.ledger.claim(now=1000 + index)
            self.ledger.finish(row, ('retry', 503, 'transient', 'safe', None), now=1000 + index)
        self.assertEqual(self.punch()['attempt_count'], 25)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM delivery_attempts').fetchone()[0], 20)
        self.assertEqual(self.punch()['state'], 'retry')

    def test_unknown_and_unconfirmed_responses_are_conservative(self):
        self.assertEqual(classify(404, {'message': 'secret'})[0], 'retry')
        self.assertEqual(classify(200, {})[0], 'retry')
        self.assertEqual(classify(503, {'exception': 'This employee already has a log with the same timestamp'})[0], 'retry')

    def test_duplicate_phrase_echoed_in_unmatched_id_cannot_be_success(self):
        data = {'exception': 'No Employee found for attendance_device_id: This employee already has a log with the same timestamp'}
        self.assertEqual(classify(417, data)[0], 'unmatched_employee')
        data = {'exception': 'This employee already has a log with the same timestamp', 'exc_type': 'PermissionError'}
        self.assertEqual(classify(417, data)[0], 'rejected')

    def test_invalid_timestamp_error_does_not_echo_input(self):
        row = normalize(record(timestamp='Invalid input with private data'), DEVICE)
        self.assertEqual(row['error_message'], 'Invalid source timestamp')

    def test_request_rate_is_limited_inside_batch(self):
        self.ingest([record(), record(uid=2), record(uid=3)])
        sleep = mock.Mock()
        self.assertEqual(self.batch(rate=2, sleep=sleep), 3)
        self.assertEqual(sleep.call_args_list, [mock.call(0.5), mock.call(0.5), mock.call(0.5)])

    def test_cli_migration_preview_does_not_create_database_and_apply_is_explicit(self):
        config = Path(self.temp.name) / 'offline_config.py'
        config.write_text('IMPORT_START_DATE="20261013"\ndevices=' + repr([DEVICE]))
        dump = Path(self.temp.name) / 'FP1_DEVICE_01_last_fetch_dump.json'
        dump.write_text(json.dumps([record(timestamp=str(PUNCH_TIME))]))
        new_db = Path(self.temp.name) / 'new.sqlite3'
        args = ['--db', str(new_db), 'import-legacy', '--config', str(config), '--directory', self.temp.name]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli(args), 0)
        self.assertEqual(json.loads(output.getvalue())[0]['imported'], 1)
        self.assertFalse(new_db.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli(args + ['--apply']), 0)
        with Ledger(new_db, readonly=True) as applied:
            self.assertEqual(applied.diagnostics()['total'], 1)
        self.assertTrue(dump.exists())

    def test_configuration_database_path_is_external_and_explicit(self):
        self.config.DURABLE_DB_PATH = str(ROOT / 'real.sqlite3')
        with self.assertRaises(ValueError):
            database_path(self.config)
        self.config.DURABLE_DB_PATH = 'relative.sqlite3'
        with self.assertRaises(ValueError):
            database_path(self.config)
        self.config.DURABLE_DB_PATH = str(self.path)
        self.assertEqual(database_path(self.config), self.path.resolve())
        self.config.DURABLE_BATCH_SIZE = 0
        with self.assertRaises(ValueError):
            delivery_options(self.config)


class EngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='biometric-engine-tests-')
        self.logs = Path(self.temp.name) / 'logs'
        self.sync = load_sync_module(self.logs, import_start_date='20261013')
        self.sync.config.DURABLE_SYNC_ENABLED = True
        self.sync.config.DURABLE_DB_PATH = str(Path(self.temp.name) / 'queue.sqlite3')
        self.sync.config.devices = [DEVICE]

    def tearDown(self):
        import logging
        for name in list(logging.root.manager.loggerDict):
            for handler in list(logging.getLogger(name).handlers):
                handler.close()
                logging.getLogger(name).removeHandler(handler)
        self.temp.cleanup()

    def test_existing_main_routes_to_durable_and_resumes_between_poll_cycles(self):
        self.sync.status.set('lift_off_timestamp', str(dt.datetime.now()))
        with Ledger(Path(self.sync.config.DURABLE_DB_PATH)) as ledger:
            ledger.begin_fetch(DEVICE['device_id'], str(dt.datetime.now()))
            ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
        self.sync.main()
        with Ledger(Path(self.sync.config.DURABLE_DB_PATH), readonly=True) as ledger:
            self.assertEqual(ledger.diagnostics()['states']['synced'], 1)
        self.assertEqual(FakeZK.instances, [])

    def test_durable_fetch_keeps_existing_legacy_dump_and_quarantines_bad_record(self):
        dump = self.logs / 'FP1_DEVICE_01_last_fetch_dump.json'
        dump.write_bytes(b'original evidence')
        FakeZK.attendances = [FakeAttendance(1, '100', 'bad'), FakeAttendance(2, '100', PUNCH_TIME)]
        self.sync.main()
        self.assertEqual(dump.read_bytes(), b'original evidence')
        with Ledger(Path(self.sync.config.DURABLE_DB_PATH), readonly=True) as ledger:
            self.assertEqual(ledger.diagnostics()['states']['invalid_record'], 1)
            self.assertEqual(ledger.diagnostics()['states']['synced'], 1)
        self.assertIsNone(self.sync.status.get('FP1_DEVICE_01_push_timestamp'))

    def test_nonrecord_device_entry_does_not_prevent_later_capture(self):
        FakeZK.attendances = [None, FakeAttendance(2, '100', PUNCH_TIME)]
        self.sync.main()
        with Ledger(Path(self.sync.config.DURABLE_DB_PATH), readonly=True) as ledger:
            self.assertEqual(ledger.diagnostics()['states']['invalid_record'], 1)
            self.assertEqual(ledger.diagnostics()['states']['synced'], 1)

    def test_prohibits_device_deletion_before_connecting(self):
        with self.assertRaises(ValueError):
            self.sync.get_all_attendance_from_device('192.0.2.10', clear_from_device_on_fetch=True)
        self.assertFalse(FakeZK.instances)

    def test_fetch_failure_reenables_terminal_and_blocks_watermark(self):
        connection = mock.Mock()
        connection.get_attendance.side_effect = RuntimeError('device failed')
        with mock.patch.object(self.sync, 'ZK') as connector:
            connector.return_value.connect.return_value = connection
            self.sync.main()
        connection.enable_device.assert_called_once()
        connection.disconnect.assert_called_once()
        with Ledger(Path(self.sync.config.DURABLE_DB_PATH), readonly=True) as ledger:
            self.assertIsNone(ledger.safe_watermark([DEVICE['device_id']]))

    def test_storage_failure_is_critical_and_never_posts(self):
        FakeZK.attendances = [FakeAttendance(1, '100', PUNCH_TIME)]
        with mock.patch.object(Ledger, 'ingest', side_effect=sqlite3.OperationalError('disk full')):
            self.sync.main()
        self.sync.requests.request.assert_not_called()
        self.assertIn('CRITICAL', (self.logs / 'error.log').read_text())
        with Ledger(Path(self.sync.config.DURABLE_DB_PATH), readonly=True) as ledger:
            self.assertEqual(ledger.db.execute('SELECT fetch_ok FROM device_state').fetchone()[0], 0)

    def test_shift_updates_use_ledger_watermark_and_do_not_mutate_mapping(self):
        mapping = dict(shift_type_name='Morning', related_device_id=[DEVICE['device_id']])
        self.sync.config.shift_type_device_mapping = [mapping]
        self.sync.send_shift_sync_to_erpnext = mock.Mock(return_value=200)
        with Ledger(Path(self.sync.config.DURABLE_DB_PATH)) as ledger:
            ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'], fetched_at='2026-10-13 09:00:00')
            update_shifts(ledger, self.sync)
            self.sync.send_shift_sync_to_erpnext.assert_called_with('Morning', PUNCH_TIME - dt.timedelta(seconds=1))
            self.assertEqual(mapping['shift_type_name'], 'Morning')
            self.assertEqual(ledger.shift_watermark('Morning'), PUNCH_TIME - dt.timedelta(seconds=1))

    def test_shift_failures_use_destination_cooldown_and_do_not_save_watermark(self):
        self.sync.config.shift_type_device_mapping = [dict(shift_type_name='Morning', related_device_id=[DEVICE['device_id']])]
        self.sync.send_shift_sync_to_erpnext = mock.Mock(return_value=403)
        with Ledger(Path(self.sync.config.DURABLE_DB_PATH)) as ledger:
            ledger.ingest([], DEVICE['device_id'], fetched_at='2026-10-13 09:00:00')
            update_shifts(ledger, self.sync)
            self.assertIsNone(ledger.shift_watermark('Morning'))
            update_shifts(ledger, self.sync)
            self.sync.send_shift_sync_to_erpnext.assert_called_once()

    def test_shift_exception_logs_do_not_expose_credentials(self):
        self.sync.requests.request.side_effect = RuntimeError('Authorization: secret')
        self.sync.send_shift_sync_to_erpnext('Morning', PUNCH_TIME)
        self.assertNotIn('Authorization', (self.logs / 'error.log').read_text())
        self.assertNotIn('secret', (self.logs / 'error.log').read_text())

    def test_windows_entry_point_keeps_service_identity_and_calls_main(self):
        fake_manager = types.ModuleType('servicemanager')
        fake_base = types.ModuleType('SMWinservice')
        fake_base.SMWinservice = type('SMWinservice', (), {})
        fake_event = types.ModuleType('win32event')
        fake_event.WaitForSingleObject = mock.Mock()
        with mock.patch.dict(sys.modules, {'servicemanager': fake_manager, 'SMWinservice': fake_base, 'win32event': fake_event}):
            spec = importlib.util.spec_from_file_location('offline_service_wrapper', ROOT / 'erpnext_sync_win.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        service = module.PythonCornerExample()
        service.start()
        module.main = mock.Mock(side_effect=lambda **kwargs: service.stop())
        service.main()
        module.main.assert_called_once()
        self.assertTrue(module.main.call_args.kwargs['stop_requested']())
        fake_event.WaitForSingleObject.assert_not_called()
        self.assertEqual(service._svc_name_, 'ERPNextBiometricPushService')
        self.assertEqual(service._exe_name_, sys.executable)


if __name__ == '__main__':
    unittest.main()
