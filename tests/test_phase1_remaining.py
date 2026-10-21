"""Consolidated remaining checks. No network, real SCM, devices or local_config.

SQLite capacity is limited per connection; the host disk is never filled.
Windows APIs are adapters here, so this is not SCM/service-token certification.
"""
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

# unittest discovery supplies the tests directory even with -I -S. Add only this
# reviewed repository to resolve the engine and existing synthetic fixtures.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durable_delivery import FrappeClient, deliver_batch
from durable_startup import activation_path, provision, validate_activation, worker_lock
from punch_normalization import normalize
from sync_cli import main as cli
from sync_ledger import Ledger
from scripts.phase1_staging_engine_https import HASHES
import test_durable_sync as fixtures


class RemainingChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-final-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_sources_remain_pinned(self):
        hashes = dict(HASHES, **{
            'erpnext_sync_win.py': '121a6cf92b53cbd45e24b3f0411fb97ae2e669dc168638a094574e0cfe6d41ad',
            'SMWinservice.py': 'c0f20a35cec74705d4875da51ac5e09597a376433c056c07e61055c022d3c006'})
        # Stage 2 integration plus the reviewed monitoring-only startup policy.
        # Keep archived Phase 1 harness hashes intact; this overlay is development
        # regression evidence and does not certify the new source for production.
        overlay = json.loads((ROOT / 'tests/fixtures/phase2_service_source_hashes.json').read_text(encoding='utf-8'))
        self.assertEqual(set(overlay), {'durable_sync.py', 'durable_startup.py', 'durable_delivery.py', 'sync_ledger.py'})
        hashes.update(overlay)
        for name, digest in hashes.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)

    def test_real_sqlite_full_rolls_back_rows_and_fetch_success(self):
        path = self.root / 'capacity.sqlite3'
        with Ledger(path) as db:
            db.begin_fetch(fixtures.DEVICE['device_id'], '2026-10-16 09:00:00')
            pages = db.db.execute('PRAGMA page_count').fetchone()[0]
            db.db.execute('PRAGMA max_page_count=' + str(pages))
            rows = (normalize(fixtures.record(uid=i, user_id='STAGE-' + str(i)), fixtures.DEVICE)
                    for i in range(1000))
            with self.assertRaises(sqlite3.OperationalError) as caught:
                db.ingest(rows, fixtures.DEVICE['device_id'], fetched_at='2026-10-16 09:01:00')
            self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_FULL)
            self.assertEqual(db.diagnostics()['total'], 0)
            self.assertEqual(db.diagnostics()['failed_scans'], 1)
            self.assertIsNone(db.safe_watermark([fixtures.DEVICE['device_id']]))
            self.assertEqual(db.verify_integrity()['integrity'], 'ok')
        with Ledger(path, readonly=True) as reopened:
            self.assertEqual(reopened.diagnostics()['total'], 0)
            self.assertEqual(reopened.verify_integrity()['integrity'], 'ok')

    def test_backup_marker_restore_pending_recovery_and_interval_accounting(self):
        source, backup = self.root / 'source.sqlite3', self.root / 'backup.sqlite3'
        boundary = dt.datetime(2026, 10, 13)
        config = types.SimpleNamespace(ERPNEXT_URL='https://127.0.0.1:443', ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY='SYNTHETIC', ERPNEXT_API_SECRET='SYNTHETIC')
        provision(source, config.ERPNEXT_URL, 15, str(boundary), 'SYNTHETIC BACKUP TEST',
                  storage_check=lambda _: None, now=boundary - dt.timedelta(days=1))
        with Ledger(source) as db:
            db.ingest([normalize(fixtures.record(), fixtures.DEVICE)], fixtures.DEVICE['device_id'])
        marker = activation_path(source).read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli(['--db', str(source), 'backup', '--output', str(backup)]), 0)
        # A later capture deliberately demonstrates why an old backup's matching
        # cookie is not proof that the backup contains the latest queue.
        with Ledger(source) as db:
            db.ingest([normalize(fixtures.record(uid=2, timestamp=fixtures.PUNCH_TIME + dt.timedelta(seconds=1)),
                                 fixtures.DEVICE)], fixtures.DEVICE['device_id'])
        source_hash, backup_hash = source.read_bytes(), backup.read_bytes()
        restored = self.root / 'restored.sqlite3'
        with restored.open('xb') as stream:
            stream.write(backup_hash)
        with activation_path(restored).open('xb') as stream:
            stream.write(marker)
        validate_activation(restored, config)
        request = mock.Mock(return_value=fixtures.response())
        with Ledger(restored) as db:
            self.assertEqual(db.diagnostics()['states']['pending'], 1)
            deliver_batch(db, FrappeClient(config, request), batch_size=1)
            self.assertEqual(db.diagnostics()['states']['synced'], 1)
            self.assertEqual(db.verify_integrity()['integrity'], 'ok')
        request.assert_called_once()
        with Ledger(source, readonly=True) as original:
            self.assertEqual(original.diagnostics()['states']['pending'], 2)
        self.assertEqual(source.read_bytes(), source_hash)
        self.assertEqual(backup.read_bytes(), backup_hash)
        self.assertEqual(activation_path(source).read_bytes(), marker)

    def test_windows_wrapper_stop_inflight_idle_restart_and_worker_release(self):
        # Execute the actual wrapper/base handlers with fake Win32 APIs and a fake
        # integration module. Never import the production configuration or SDK.
        statuses, waits = [], []
        framework = type('SyntheticFramework', (), {
            '__init__': lambda self, args: None,
            'ReportServiceStatus': lambda self, status: statuses.append(status)})
        event = types.ModuleType('win32event')
        event.CreateEvent = lambda *args: threading.Event()
        event.SetEvent = lambda handle: handle.set()
        event.WaitForSingleObject = mock.Mock()
        manager = types.ModuleType('servicemanager')
        manager.LogMsg = mock.Mock()
        manager.EVENTLOG_INFORMATION_TYPE, manager.PYS_SERVICE_STARTED = 1, 2
        winservice = types.ModuleType('win32service')
        winservice.SERVICE_STOP_PENDING = 3
        util = types.ModuleType('win32serviceutil')
        util.ServiceFramework = framework
        integration = types.ModuleType('erpnext_sync')
        integration.config = types.SimpleNamespace(DURABLE_SYNC_ENABLED=True,
                                                  DURABLE_DB_PATH=str(self.root / 'never-created.sqlite3'))
        integration.main = mock.Mock()
        old_timeout = socket.getdefaulttimeout()
        self.addCleanup(socket.setdefaulttimeout, old_timeout)
        modules = dict(win32event=event, win32service=winservice, win32serviceutil=util,
                       servicemanager=manager, erpnext_sync=integration)
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('SMWinservice', ROOT / 'SMWinservice.py')
            base = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(base)
            with mock.patch.dict(sys.modules, {'SMWinservice': base}):
                spec = importlib.util.spec_from_file_location('synthetic_final_wrapper', ROOT / 'erpnext_sync_win.py')
                wrapper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(wrapper)
            entered, finish = threading.Event(), threading.Event()
            callbacks, errors = [], []
            def inflight(**kwargs):
                callbacks.append(kwargs['stop_requested'])
                entered.set()
                if not finish.wait(3):
                    raise AssertionError('Synthetic inflight work did not finish')
            wrapper.main = inflight
            service = wrapper.PythonCornerExample(['SYNTHETIC'])
            def execute():
                try:
                    service.SvcDoRun()
                except BaseException as error:
                    errors.append(type(error).__name__)
            thread = threading.Thread(target=execute)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                service.SvcStop()
                self.assertTrue(callbacks[0]())
                self.assertTrue(service.hWaitStop.is_set())
                self.assertTrue(thread.is_alive())  # Stop does not kill inflight work.
            finally:
                finish.set()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            event.WaitForSingleObject.assert_not_called()
            with worker_lock(Path(integration.config.DURABLE_DB_PATH)):
                pass
            # A fresh wrapper instance starts normally and stops from its idle wait.
            restarted = wrapper.PythonCornerExample(['SYNTHETIC'])
            wrapper.main = mock.Mock()
            def idle(handle, milliseconds):
                waits.append(milliseconds)
                restarted.SvcStop()
                self.assertTrue(handle.is_set())
                return 0
            event.WaitForSingleObject = idle
            restarted.SvcDoRun()
            wrapper.main.assert_called_once()
            self.assertEqual(waits, [15000])
            self.assertEqual(statuses, [3, 3])
            self.assertFalse(restarted.isrunning)
        self.assertFalse(Path(integration.config.DURABLE_DB_PATH).exists())


class NoNetworkSuite(unittest.TestSuite):
    def run(self, result, debug=False):
        # All destination/SDK adapters in this focused suite are in-memory fakes.
        # No connection, DNS query or datagram is needed, including localhost.
        with contextlib.ExitStack() as stack:
            for target in ('socket.create_connection', 'socket.getaddrinfo',
                           'socket.socket.connect', 'socket.socket.connect_ex', 'socket.socket.sendto'):
                stack.enter_context(mock.patch(target, side_effect=AssertionError('Network forbidden in final offline checks')))
            return super().run(result, debug)


def load_tests(loader, standard_tests, pattern):
    # Deliberately select remaining gates, not previously passed staging scenarios.
    suite = NoNetworkSuite(loader.loadTestsFromTestCase(RemainingChecks))
    for name in (
        'test_service_hardening.HardeningTests.test_missing_ledger_marker_and_replaced_database_fail_closed',
        'test_service_hardening.HardeningTests.test_destination_and_endpoint_family_binding',
        'test_service_hardening.HardeningTests.test_nonempty_ledger_cannot_be_silently_adopted',
        'test_service_hardening.HardeningTests.test_critical_storage_fault_and_failed_device_are_structured_and_sanitized',
        'test_service_hardening.HardeningTests.test_historical_capture_is_retained_and_current_payload_is_unchanged',
        'test_service_hardening.HardeningTests.test_delivery_boundary_also_holds_legacy_imported_pending_rows',
        'test_phase1_release.ReleaseReviewTests.test_corrupted_file_is_not_initialized_or_overwritten',
        'test_phase1_release.ReleaseReviewTests.test_missing_table_index_and_changed_definition_fail_before_writes',
        'test_phase1_release.ReleaseReviewTests.test_service_stop_after_fetch_preserves_committed_pending_punch',
        'test_phase1_release.ReleaseReviewTests.test_stop_during_rate_wait_does_not_claim_another_punch',
    ):
        suite.addTests(loader.loadTestsFromName(name))
    return suite
