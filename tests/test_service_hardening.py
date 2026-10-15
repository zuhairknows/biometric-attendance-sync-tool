"""Synthetic service safeguards. Never import real local_config or access live endpoints."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from durable_delivery import FrappeClient, deliver_batch
from durable_startup import (StartupError, activation_path, cutover_time, destination_key,
                             private_storage, provision, validate_activation, worker_lock, worker_session)
from durable_sync import run
from punch_normalization import normalize
from sync_ledger import Ledger
from sync_cli import main as cli
from test_durable_sync import DEVICE, record, response
from test_erpnext_sync import FakeZK, load_sync_module


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-hardening-')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.close_loggers)
        self.root = Path(self.temp.name)
        self.path = self.root / 'queue.sqlite3'
        self.config = types.SimpleNamespace(ERPNEXT_URL='https://erp.example.test', ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY='SYNTH-KEY', ERPNEXT_API_SECRET='SYNTH-SECRET', REQUEST_TIMEOUT=30)
        self.boundary = dt.datetime(2026, 10, 13, 9)

    @staticmethod
    def close_loggers():
        import logging
        for name in list(logging.root.manager.loggerDict):
            for handler in list(logging.getLogger(name).handlers):
                handler.close()
                logging.getLogger(name).removeHandler(handler)

    def provision(self):
        return provision(self.path, self.config.ERPNEXT_URL, 15, str(self.boundary),
                         'synthetic review only', storage_check=lambda _: None,
                         now=self.boundary - dt.timedelta(minutes=1))

    def open_ledger(self):
        ledger = Ledger(self.path)
        self.addCleanup(ledger.close)
        return ledger

    def test_explicit_future_cutover_and_no_secret_url(self):
        for value in ('2026-10-13', '2026-10-13T09:00:00', '2026-10-13 09:00:00.1', '2026-10-13 09:00:00+03:00'):
            with self.subTest(value=value), self.assertRaises(StartupError):
                cutover_time(value)
        with self.assertRaises(StartupError):
            provision(self.path, self.config.ERPNEXT_URL, 15, str(self.boundary), 'review',
                      storage_check=lambda _: None, now=self.boundary)
        self.assertFalse(self.path.exists())
        for url in ('http://erp.example.test', 'https://user:password@erp.example.test',
                    'https://erp.example.test?token=secret', 'https://erp.example.test/#secret',
                    'https://erp.example.test:bad', 'https://erp.example.test:0', 'https://erp.example.test bad'):
            with self.subTest(url=url), self.assertRaises(StartupError):
                destination_key(url)
        for version in (True, '15', 15.0, 0):
            with self.subTest(version=version), self.assertRaises(StartupError):
                destination_key(self.config.ERPNEXT_URL, version)

    def test_console_interrupt_releases_worker_ownership(self):
        sync = load_sync_module(self.root / 'logs')
        sync.config.DURABLE_SYNC_ENABLED = True
        sync.config.DURABLE_DB_PATH = str(self.path)
        with mock.patch.object(sync, 'main'), mock.patch.object(sync.time, 'sleep', side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                sync.infinite_loop()
        with worker_lock(self.path):
            pass
        self.assertFalse(self.path.exists())

    def test_missing_ledger_marker_and_replaced_database_fail_closed(self):
        self.provision()
        saved = self.path.read_bytes()
        self.path.unlink()
        with self.assertRaises(StartupError):
            validate_activation(self.path, self.config)
        self.assertFalse(self.path.exists())
        with Ledger(self.path):
            pass
        with self.assertRaises(StartupError):
            validate_activation(self.path, self.config)
        self.path.write_bytes(saved)
        self.assertEqual(validate_activation(self.path, self.config)['cutover_time'], str(self.boundary))
        activation_path(self.path).unlink()
        with self.assertRaises(StartupError):
            validate_activation(self.path, self.config)
        with self.assertRaises(StartupError):
            self.provision()

    def test_destination_and_endpoint_family_binding(self):
        self.provision()
        for url, version in (('https://other.example.test', 15), (self.config.ERPNEXT_URL, 13)):
            other = types.SimpleNamespace(ERPNEXT_URL=url, ERPNEXT_VERSION=version)
            with self.assertRaises(StartupError):
                validate_activation(self.path, other)
        self.assertEqual(destination_key('https://ERP.EXAMPLE.TEST/', 15), destination_key(self.config.ERPNEXT_URL, 15))

    def test_nonempty_ledger_cannot_be_silently_adopted(self):
        with Ledger(self.path) as ledger:
            ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
        before = self.path.read_bytes()
        with self.assertRaises(StartupError):
            self.provision()
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(activation_path(self.path).exists())

    def test_single_worker_and_release_after_exception(self):
        errors = []
        def contender():
            try:
                with worker_lock(self.path):
                    errors.append('incorrectly_acquired')
            except StartupError:
                errors.append('blocked')
        with self.assertRaises(RuntimeError):
            with worker_lock(self.path):
                thread = threading.Thread(target=contender)
                thread.start()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, ['blocked'])
                raise RuntimeError('synthetic interruption')
        with worker_lock(self.path):
            pass

    def test_worker_lock_is_cross_process_and_recovers_after_process_death(self):
        root = Path(__file__).resolve().parents[1]
        body = "from durable_startup import worker_lock,StartupError; import sys\ntry:\n with worker_lock(sys.argv[1]): pass\nexcept StartupError: sys.exit(23)"
        with worker_lock(self.path):
            result = subprocess.run([sys.executable, '-B', '-c', body, str(self.path)], cwd=root,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 23, result.stderr)
            if sys.platform == 'win32':
                other = subprocess.run([sys.executable, '-B', '-c', body, str(self.root / 'other.sqlite3')],
                                       cwd=root, capture_output=True, timeout=15)
                self.assertEqual(other.returncode, 23, other.stderr)
        crash = "from durable_startup import worker_lock; import os,sys\nwith worker_lock(sys.argv[1]): os._exit(24)"
        result = subprocess.run([sys.executable, '-B', '-c', crash, str(self.path)], cwd=root,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 24, result.stderr)
        with worker_lock(self.path):
            pass

    def test_worker_session_stays_exclusive_between_ticks_and_keeps_legacy_disabled(self):
        config = types.SimpleNamespace(DURABLE_SYNC_ENABLED=True, DURABLE_DB_PATH=str(self.path))
        blocked = []
        def other_tick():
            try:
                with worker_lock(self.path):
                    blocked.append(False)
            except StartupError:
                blocked.append(True)
        with worker_session(config):
            with worker_lock(self.path):
                pass  # Nested engine cycle releases only its own ownership level.
            thread = threading.Thread(target=other_tick)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(blocked, [True])
        self.assertFalse(self.path.exists())
        config.DURABLE_SYNC_ENABLED = False
        with mock.patch('durable_startup.worker_lock', side_effect=AssertionError('legacy must not lock')):
            with worker_session(config):
                pass

    def test_provisioning_cli_is_explicit_offline_and_refuses_reprovision(self):
        boundary = (dt.datetime.now() + dt.timedelta(minutes=5)).replace(microsecond=0)
        args = ['--db', str(self.path), 'provision', '--destination-url', self.config.ERPNEXT_URL,
                '--erpnext-version', '15', '--cutover-time', str(boundary), '--review-reference',
                'SYNTHETIC ONLY', '--acknowledge-cutover']
        console = io.StringIO()
        with mock.patch('durable_startup.private_storage'), contextlib.redirect_stdout(console):
            self.assertEqual(cli(args), 0)
        self.assertFalse(json.loads(console.getvalue())['recovery_authorized'])
        self.assertEqual(validate_activation(self.path, self.config)['cutover_time'], str(boundary))
        before = self.path.read_bytes()
        with mock.patch('durable_startup.private_storage'), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli(args), 1)
        self.assertEqual(before, self.path.read_bytes())

    def test_critical_storage_fault_and_failed_device_are_structured_and_sanitized(self):
        import sqlite3
        self.provision()
        sync = load_sync_module(self.root / 'logs', '20261013')
        sync.config.DURABLE_SYNC_ENABLED = True
        sync.config.DURABLE_DB_PATH = str(self.path)
        sync.config.devices = [DEVICE]
        sync.get_all_attendance_from_device = mock.Mock(return_value=[record(timestamp=self.boundary)])
        with mock.patch('durable_sync.private_storage'), mock.patch.object(Ledger, 'ingest',
                side_effect=sqlite3.OperationalError('SYNTH-SECRET')):
            sync.main()
        sync.requests.request.assert_not_called()
        text = (self.root / 'logs' / 'error.log').read_text()
        self.assertIn('"event": "durable_critical"', text)
        self.assertNotIn('SYNTH-SECRET', text)
        with Ledger(self.path, readonly=True) as ledger:
            self.assertEqual(ledger.diagnostics()['failed_scans'], 1)
        sync.config.devices = [DEVICE, dict(DEVICE, device_id='SYNTH-SECOND')]
        sync.get_all_attendance_from_device.side_effect = [RuntimeError('SYNTH-SECRET'), [record(timestamp=self.boundary)]]
        with mock.patch('durable_sync.private_storage'):
            sync.main()
        text = (self.root / 'logs' / 'error.log').read_text()
        self.assertIn('"event": "durable_scan_failed"', text)
        self.assertNotIn('SYNTH-SECRET', text)
        self.assertEqual(sync.requests.request.call_count, 1)
        with Ledger(self.path, readonly=True) as ledger:
            self.assertEqual(ledger.diagnostics()['states']['synced'], 1)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows production ACL/drive checks')
    def test_private_storage_rejects_failed_broad_or_unavailable_acl_checks(self):
        for result in (types.SimpleNamespace(returncode=0, stdout='{"safe":false}'),
                       types.SimpleNamespace(returncode=1, stdout=''),
                       types.SimpleNamespace(returncode=0, stdout='private exception text')):
            with mock.patch('durable_startup.subprocess.run', return_value=result), self.assertRaises(StartupError):
                private_storage(self.path)
        with mock.patch('durable_startup.subprocess.run', side_effect=OSError('secret')), self.assertRaises(StartupError):
            private_storage(self.path)
        with mock.patch('durable_startup.subprocess.run', return_value=types.SimpleNamespace(returncode=0, stdout='{"safe":true}')) as check:
            private_storage(self.path)
            command = check.call_args.args[0]
            self.assertIn('-NoProfile', command)
            self.assertNotIn(str(self.path), ' '.join(command))

    def test_unprovisioned_or_insecure_startup_never_polls_or_posts(self):
        engine = types.SimpleNamespace(config=types.SimpleNamespace(DURABLE_DB_PATH=str(self.path)),
            requests=mock.Mock(), get_all_attendance_from_device=mock.Mock())
        with mock.patch('durable_sync.private_storage', side_effect=StartupError('private_storage_failure')):
            with self.assertRaises(StartupError):
                run(engine)
        with mock.patch('durable_sync.private_storage'):
            with self.assertRaises(StartupError):
                run(engine)
        self.assertFalse(self.path.exists())
        engine.requests.request.assert_not_called()
        engine.get_all_attendance_from_device.assert_not_called()

    def test_historical_capture_is_retained_and_current_payload_is_unchanged(self):
        from durable_sync import capture
        self.provision()
        ledger = self.open_ledger()
        engine = types.SimpleNamespace(config=self.config, device_punch_values_IN=[0, 4], device_punch_values_OUT=[1, 5])
        self.config.IMPORT_START_DATE = '20261013'
        values = [record(timestamp=dt.datetime(2026, 10, 12, 8)), record(timestamp=self.boundary),
                  record(timestamp=self.boundary + dt.timedelta(seconds=1), uid=3)]
        report = capture(ledger, engine, DEVICE, records=values)
        self.assertEqual(report['imported'], 3)
        rows = ledger.db.execute('SELECT * FROM punches ORDER BY id').fetchall()
        self.assertEqual(rows[0]['error_category'], 'cutover_hold')
        self.assertEqual(rows[0]['state'], 'rejected')
        self.assertIsNotNone(rows[0]['payload'])
        self.assertEqual(json.loads(rows[1]['payload']), dict(employee_field_value='100',
            timestamp=str(self.boundary), device_id=DEVICE['device_id'], log_type=None, latitude=None, longitude=None))
        self.assertLess(ledger.safe_watermark([DEVICE['device_id']]), dt.datetime(2026, 10, 12, 8))
        capture(ledger, engine, DEVICE, records=values)
        self.assertEqual(ledger.diagnostics()['total'], 3)
        self.assertEqual(ledger.diagnostics()['held_historical'], 1)

    def test_delivery_boundary_also_holds_legacy_imported_pending_rows(self):
        ledger = self.open_ledger()
        ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
        client = mock.Mock()
        deliver_batch(ledger, client, not_before=self.boundary)
        client.send.assert_not_called()
        self.assertEqual(ledger.diagnostics()['held_historical'], 1)

    def test_timeout_restart_and_duplicate_phrase_never_blindly_repost(self):
        ledger = self.open_ledger()
        ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
        request = mock.Mock(side_effect=TimeoutError('SYNTH-SECRET'))
        client = FrappeClient(self.config, request)
        deliver_batch(ledger, client, clock=lambda: 1000)
        deliver_batch(ledger, client, clock=lambda: 5000)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(ledger.diagnostics()['uncertain_submissions'], 1)
        self.assertNotIn('SYNTH-SECRET', json.dumps(ledger.diagnostics()))
        client.confirm = mock.Mock(return_value='SYNTH-CHECKIN')
        deliver_batch(ledger, client, clock=lambda: 10000)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(ledger.diagnostics()['states']['duplicate'], 1)
        self.assertEqual(ledger.db.execute('SELECT checkin_name FROM punches').fetchone()[0], 'SYNTH-CHECKIN')

    def test_duplicate_response_requires_positive_confirmation(self):
        ledger = self.open_ledger()
        ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
        request = mock.Mock(return_value=response(417, 'This employee already has a log with the same timestamp'))
        deliver_batch(ledger, FrappeClient(self.config, request), clock=lambda: 1000)
        self.assertEqual(ledger.diagnostics()['states']['duplicate'], 0)
        self.assertEqual(ledger.diagnostics()['uncertain_submissions'], 1)

    def test_local_confirmation_hold_does_not_pause_unrelated_new_punches(self):
        ledger = self.open_ledger()
        ledger.ingest([normalize(record(), DEVICE), normalize(record(user_id='OTHER', uid=2,
            timestamp=dt.datetime(2026, 10, 13, 10)), DEVICE)], DEVICE['device_id'])
        request = mock.Mock(side_effect=[TimeoutError('synthetic'), response()])
        client = FrappeClient(self.config, request)
        deliver_batch(ledger, client, clock=lambda: 1000, sleep=lambda _: None)
        deliver_batch(ledger, client, clock=lambda: 5000, sleep=lambda _: None)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs['json']['employee_field_value'], 'OTHER')
        self.assertEqual(ledger.diagnostics()['uncertain_submissions'], 1)
        self.assertEqual(ledger.diagnostics()['states']['synced'], 1)
        self.assertLessEqual(ledger.destination_cooldown(), 5000)

    def test_uncertain_peer_blocks_same_biometric_second_across_devices_and_uids(self):
        ledger = self.open_ledger()
        ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
        second = dict(DEVICE, device_id='SYNTH-OTHER-DEVICE', punch_direction='OUT')
        ledger.ingest([normalize(record(uid=999, timestamp=dt.datetime(2026, 10, 13, 8, 0, 0, 123456)), second)],
                      second['device_id'])
        request = mock.Mock(side_effect=[TimeoutError('synthetic'), response()])
        client = FrappeClient(self.config, request)
        deliver_batch(ledger, client, clock=lambda: 1000, sleep=lambda _: None)
        deliver_batch(ledger, client, clock=lambda: 5000, sleep=lambda _: None)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(ledger.diagnostics()['uncertain_submissions'], 1)
        self.assertEqual(ledger.diagnostics()['held_related_records'], 1)
        self.assertEqual(ledger.diagnostics()['states']['synced'], 0)
        client.confirm = mock.Mock(return_value='SYNTH-CONFIRMED')
        deliver_batch(ledger, client, clock=lambda: 10000, sleep=lambda _: None)
        deliver_batch(ledger, client, clock=lambda: 20000, sleep=lambda _: None)
        self.assertEqual(request.call_count, 2)  # Only now is the held OUT record first submitted.
        self.assertEqual(request.call_args.kwargs['json']['log_type'], 'OUT')
        self.assertEqual(ledger.diagnostics()['held_related_records'], 0)

    def test_known_authentication_and_rate_refusals_retry_automatically(self):
        for status in (401, 403, 429):
            with Ledger(self.root / ('refusal-' + str(status) + '.sqlite3')) as ledger:
                ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
                request = mock.Mock(side_effect=[response(status, 'synthetic refusal'), response()])
                client = FrappeClient(self.config, request)
                client.confirm = mock.Mock(side_effect=AssertionError('not an uncertain submission'))
                deliver_batch(ledger, client, clock=lambda: 1000)
                deliver_batch(ledger, client, clock=lambda: 5000)
                self.assertEqual(ledger.diagnostics()['states']['synced'], 1)
                self.assertEqual([call.args[0] for call in request.call_args_list], ['POST', 'POST'])
                client.confirm.assert_not_called()

    def test_confirmation_read_pacing_and_stop_requests(self):
        ledger = self.open_ledger()
        ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
        self.config.DURABLE_CONFIRM_READS_VERIFIED = True
        self.config.DURABLE_SERVER_REVIEW_REFERENCE = 'SYNTHETIC ONLY'
        employee = types.SimpleNamespace(status_code=200, content=json.dumps({'data': [dict(
            name='SYNTH-EMP', status='Active', attendance_device_id='100')]}).encode())
        checkin = types.SimpleNamespace(status_code=200, content=json.dumps({'data': [dict(
            name='SYNTH-C', employee='SYNTH-EMP', time='2026-10-13 08:00:00', log_type='')]}).encode())
        request = mock.Mock(side_effect=[TimeoutError('synthetic'), employee, checkin])
        client = FrappeClient(self.config, request)
        deliver_batch(ledger, client, clock=lambda: 1000)
        sleep = mock.Mock()
        deliver_batch(ledger, client, clock=lambda: 5000, sleep=sleep, rate=2, batch_size=1)
        self.assertEqual(sleep.call_args_list, [mock.call(0.5), mock.call(0.5)])
        self.assertEqual([call.args[0] for call in request.call_args_list], ['POST', 'GET', 'GET'])
        self.assertEqual(ledger.diagnostics()['states']['duplicate'], 1)
        ledger.ingest([normalize(record(uid=2), DEVICE)], DEVICE['device_id'])
        row = ledger.claim(10000)
        ledger.finish(row, ('retry', 0, 'transport', 'synthetic', None), 10000)
        stopped = [False]
        def stop_after_employee(*args, **kwargs):
            stopped[0] = True
            return employee
        request.reset_mock(side_effect=True)
        request.side_effect = stop_after_employee
        deliver_batch(ledger, client, clock=lambda: 20000, sleep=lambda _: None, stop_requested=lambda: stopped[0])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], 'GET')
        self.assertEqual(ledger.diagnostics()['uncertain_submissions'], 1)

    def test_partial_provisioning_fails_closed_without_resetting_identity(self):
        with mock.patch('durable_startup.json.dump', side_effect=OSError('SYNTH-SECRET')):
            with self.assertRaises(OSError):
                self.provision()
        self.assertTrue(self.path.exists())
        with self.assertRaises(StartupError):
            validate_activation(self.path, self.config)
        before = self.path.read_bytes()
        with self.assertRaises(StartupError):
            self.provision()
        self.assertEqual(before, self.path.read_bytes())

    def test_expired_lease_requires_confirmation_even_after_authentication_failure(self):
        ledger = self.open_ledger()
        ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])
        row = ledger.claim(1000)
        ledger.finish(row, ('retry', 401, 'authentication', 'synthetic', None), 1000)
        ledger.claim(2000)
        request = mock.Mock()
        deliver_batch(ledger, FrappeClient(self.config, request), clock=lambda: 3000)
        request.assert_not_called()
        self.assertEqual(ledger.diagnostics()['uncertain_submissions'], 1)

    def test_confirmation_handles_null_ambiguity_permissions_and_cross_device_identity(self):
        self.config.DURABLE_CONFIRM_READS_VERIFIED = True
        self.config.DURABLE_SERVER_REVIEW_REFERENCE = 'SYNTHETIC ONLY'
        payload = json.loads(normalize(record(), DEVICE)['payload'])
        employee = dict(name='SYNTH-EMP', status='Active', attendance_device_id='100')
        checkin = dict(name='SYNTH-C', employee='SYNTH-EMP', time='2026-10-13 08:00:00', log_type='')
        def data(rows, status=200):
            return types.SimpleNamespace(status_code=status, content=json.dumps({'data': rows}).encode())
        for employees, rows, expected in (([employee], [checkin], 'SYNTH-C'),
                ([employee] * 2, [checkin], None), ([employee], [], None),
                ([employee], [checkin] * 2, None), ([employee], [dict(checkin, log_type=None)], None),
                ([dict(employee, status='Inactive')], [checkin], None),
                ([employee], [dict(checkin, time='2026-10-13 08:00:00.1')], None)):
            request = mock.Mock(side_effect=[data(employees), data(rows)])
            client = FrappeClient(self.config, request)
            self.assertEqual(client.confirm(payload), expected)
            for call in request.call_args_list:
                self.assertEqual(call.args[0], 'GET')
                self.assertFalse(call.kwargs['allow_redirects'])
                self.assertNotIn('device_id', [f[0] for f in json.loads(call.kwargs['params']['filters'])])
        for result in (data([], 403), data([], 500)):
            self.assertIsNone(FrappeClient(self.config, mock.Mock(return_value=result)).confirm(payload))
        self.config.DURABLE_CONFIRM_READS_VERIFIED = False
        request = mock.Mock()
        self.assertIsNone(FrappeClient(self.config, request).confirm(payload))
        request.assert_not_called()

    def test_readonly_diagnostics_show_failures_backlog_and_quarantine(self):
        self.provision()
        ledger = self.open_ledger()
        ledger.ingest([normalize(record(), DEVICE), normalize(record(uid=2, timestamp='bad'), DEVICE)], DEVICE['device_id'])
        ledger.begin_fetch(DEVICE['device_id'], '2026-10-13 10:00:00')
        ledger.db.execute('UPDATE punches SET first_captured=first_captured-100')
        ledger.db.commit()
        before = self.path.read_bytes()
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            self.assertEqual(cli(['--db', str(self.path), 'status']), 0)
        summary = json.loads(console.getvalue())
        self.assertGreaterEqual(summary['backlog_age_seconds'], 100)
        self.assertEqual(summary['failed_scans'], 1)
        self.assertEqual(summary['quarantined'], 1)
        self.assertTrue(summary['activation_present'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertNotIn('SYNTH-EMP', console.getvalue())


if __name__ == '__main__':
    unittest.main()
