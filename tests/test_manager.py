"""Synthetic manager acceptance. No SCM, network, device or deployed configuration."""
import csv
from contextlib import closing
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from unittest import mock

from manager_app.controller import Controller, ManagerError, Settings
from manager_app.demo import create_demo
from historical_attendance.bounded import run_bounded
from historical_attendance.client import VerificationError
from historical_attendance.recovery_store import RecoveryStore


def stalled_task(marker):
    Path(marker).write_text('started')
    time.sleep(30)
    Path(marker).write_text('unsafe continuation')
    return [], {}


class ManagerFixture(unittest.TestCase):
    def setUp(self):
        self.temporary, self.controller, self.values = create_demo()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = Path(self.values['backup'])
        self.original = self.source.read_bytes()
        self.batch = self.controller.batches(self.values['workspace'])[0]

    def tearDown(self):
        self.assertEqual(self.source.read_bytes(), self.original)

    def approve(self, ids=(1,)):
        return self.controller.approve(self.values['workspace'], self.batch['id'], self.batch['fingerprint'],
                                       list(ids), 'SYNTHETIC selected review')

    def simulate(self):
        return self.controller.simulate(self.values['workspace'], self.batch['id'], self.batch['fingerprint'],
                                        self.values['simulation'], self.values['normal_lock'])


class ManagerTests(ManagerFixture):
    def test_dashboard_is_read_only_and_service_states_independent(self):
        with mock.patch('durable_startup.worker_lock', side_effect=AssertionError('no mutex')):
            for state in ('RUNNING', 'STOPPED', 'START_PENDING', 'PAUSED'):
                self.controller.service_query = mock.Mock(return_value=state)
                result = self.controller.dashboard(str(self.source))
                self.assertEqual((result['service'], result['integrity'], result['total'], result['pending']),
                                 (state, 'OK', 3, 3))
                self.assertEqual(len(result['devices']), 3)
                self.controller.service_query.assert_called_once_with()

    def test_missing_service_does_not_hide_ledger(self):
        self.controller.service_query = mock.Mock(side_effect=RuntimeError('PRIVATE SECRET'))
        result = self.controller.dashboard(str(self.source))
        self.assertEqual((result['service'], result['integrity']), ('Unavailable', 'OK'))
        self.assertNotIn('PRIVATE', str(result))

    def test_dashboard_counts_retry_failure_delivery_and_historical_hold(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE punches SET state='retry' WHERE id=1")
            db.execute("UPDATE punches SET state='rejected',error_category='cutover_hold' WHERE id=2")
            db.execute("UPDATE punches SET state='synced' WHERE id=3")
            db.execute("UPDATE device_state SET last_successful_delivery=1700000000 WHERE device_id='FP2_DEVICE_11'")
        # These are fixture changes, never manager operations.
        self.original = self.source.read_bytes()
        data = self.controller.dashboard(str(self.source))
        self.assertEqual((data['pending'], data['retry'], data['failed'], data['holds']), (0, 1, 1, 1))
        self.assertNotEqual(data['last_delivery'], 'Not recorded')

    def test_missing_or_corrupt_ledger_degrades_without_creation(self):
        missing = self.root / 'missing.sqlite3'
        bad = self.root / 'corrupt.sqlite3'
        bad.write_bytes(b'corrupt')
        for path in (missing, bad):
            result = self.controller.dashboard(str(path))
            self.assertEqual(result['integrity'], 'Unavailable')
            self.assertEqual(result['service'], 'RUNNING (demo)')
            self.assertTrue(result['errors'])
        self.assertFalse(missing.exists())
        self.assertEqual(bad.read_bytes(), b'corrupt')

    def test_browsing_audit_is_read_only_without_writer_lock(self):
        workspace = Path(self.values['workspace'])
        before = {p.name: p.read_bytes() for p in workspace.iterdir()}
        with mock.patch.object(RecoveryStore, 'update', side_effect=AssertionError('no writes')):
            data = self.controller.batch(str(workspace), self.batch['id'])
        self.assertEqual(data['counts'], {'Pending Approval': 3})
        self.assertFalse(data['production_execution_supported'])
        self.assertEqual({p.name: p.read_bytes() for p in workspace.iterdir()}, before)
        store = RecoveryStore(workspace, acl_check=self.controller.acl_check, readonly=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                store.db.execute('DELETE FROM items')
        finally:
            store.close()

    def test_missing_audit_is_not_created(self):
        directory = self.root / 'empty-audit'
        directory.mkdir()
        with self.assertRaises(ManagerError):
            self.controller.batches(str(directory))
        self.assertEqual(list(directory.iterdir()), [])

    def test_explicit_selection_approval_and_idempotent_simulation(self):
        result = self.approve()
        self.assertEqual(result['counts'], {'Approved': 1, 'Pending Approval': 2})
        with mock.patch('socket.socket', side_effect=AssertionError('simulation must never connect')):
            result = self.simulate()
            self.assertEqual(result['counts'], {'Recovered': 1, 'Pending Approval': 2})
            repeated = self.simulate()
        self.assertEqual(repeated['counts'], result['counts'])
        target = json.loads(Path(self.values['simulation']).read_text())
        self.assertEqual(len(target['checkins']), 1)
        self.assertTrue(any(e['status'] == 'Recovered' for e in result['events']))

    def test_unapproved_records_never_simulated(self):
        before = Path(self.values['simulation']).read_bytes()
        self.assertEqual(self.simulate()['counts'], {'Pending Approval': 3})
        self.assertEqual(Path(self.values['simulation']).read_bytes(), before)

    def test_invalid_approval_fingerprint_or_reference_is_blocked(self):
        for fingerprint, reference in (('changed', 'review'), (self.batch['fingerprint'], '')):
            with self.assertRaises(ManagerError):
                self.controller.approve(self.values['workspace'], self.batch['id'], fingerprint, [1], reference)
        self.assertEqual(self.controller.batch(self.values['workspace'], self.batch['id'])['counts'], {'Pending Approval': 3})

    def test_changed_pinned_contract_blocks_approval(self):
        contract = Path(self.values['contract'])
        original = contract.read_bytes()
        contract.write_bytes(original + b' ')
        try:
            with self.assertRaises(ManagerError):
                self.approve()
        finally:
            contract.write_bytes(original)

    def test_changed_source_blocks_approval(self):
        self.source.write_bytes(self.original + b'changed')
        try:
            with self.assertRaises(ManagerError):
                self.approve()
        finally:
            self.source.write_bytes(self.original)

    def test_malformed_audit_metadata_fails_closed(self):
        audit = Path(self.values['workspace']) / 'recovery-state.recovery-audit'
        with closing(sqlite3.connect(audit)) as db, db:
            db.execute("UPDATE batches SET metadata='PRIVATE malformed'")
        with self.assertRaisesRegex(ManagerError, 'existing_recovery_workspace_unavailable'):
            self.controller.batches(self.values['workspace'])

    def test_production_fixture_cannot_be_used_as_transport(self):
        self.approve()
        scenario = Path(self.values['simulation'])
        data = json.loads(scenario.read_text())
        data['site_url'] = 'https://erp.myfpf.com'
        scenario.write_text(json.dumps(data))
        with mock.patch('socket.socket', side_effect=AssertionError('no network')):
            with self.assertRaises(ManagerError):
                self.simulate()

    def test_normal_writer_contention_never_stops_or_reconfigures_service(self):
        self.approve()
        self.controller.service_query = mock.Mock()
        before = Path(self.values['simulation']).read_bytes()
        with mock.patch('historical_attendance.recovery.worker_lock', side_effect=RuntimeError('another writer')):
            with self.assertRaises(ManagerError):
                self.simulate()
        self.controller.service_query.assert_not_called()
        self.assertEqual(Path(self.values['simulation']).read_bytes(), before)

    def test_protected_audit_exports_are_consistent_and_not_overwritten(self):
        directory = self.root / 'exports'
        self.controller.export_recovery(self.values['workspace'], self.batch['id'], str(directory))
        self.controller.export_recovery(self.values['workspace'], self.batch['id'], str(directory))
        summaries = list(directory.glob('*.json'))
        self.assertEqual(len(summaries), 2)
        data = json.loads(summaries[0].read_text())
        with next(directory.glob('*.csv')).open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), len(data['items']))
        self.assertNotIn('DEMO', summaries[0].read_text())
        self.assertFalse(data['production_execution_supported'])

    def test_source_and_workspace_directories_cannot_receive_reports(self):
        for path in (self.source.parent, Path(self.values['workspace']), Path.cwd()):
            with self.assertRaises(ManagerError):
                self.controller.export_recovery(self.values['workspace'], self.batch['id'], str(path))

    def test_unprotected_output_fails_closed_for_direct_app_calls(self):
        self.controller.acl_check = mock.Mock(side_effect=ValueError('unprotected'))
        with self.assertRaises(ManagerError):
            self.controller.export_recovery(self.values['workspace'], self.batch['id'], str(self.root / 'exports'))

    def test_investigation_uses_real_bounded_cli_and_preserves_credentials(self):
        with mock.patch.dict(os.environ, INVESTIGATION_API_KEY='previous-key', INVESTIGATION_API_SECRET='previous-secret'):
            result = self.controller.investigate(self.values, ['FP1_DEVICE_01'], 'DEMO', 'DEMO', False, True)
            self.assertEqual(os.environ['INVESTIGATION_API_KEY'], 'previous-key')
            self.assertEqual(os.environ['INVESTIGATION_API_SECRET'], 'previous-secret')
        self.assertEqual(result['summary']['status'], 'complete')
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['summary']['execution_limits'], dict(max_get_requests=32, deadline_seconds=120, request_timeout_seconds=10))
        self.assertTrue(Path(result['csv_path']).is_file())
        self.assertTrue(all(row['eligible_for_recovery'] == 'False' for row in result['rows']))

    def test_production_compatibility_is_never_recovery_evidence(self):
        contract_path = Path(self.values['contract'])
        contract = json.loads(contract_path.read_text())
        contract['destination_environment'] = 'production'
        contract['verification']['read_only_api_verified'] = True  # Synthetic endpoint only.
        contract_path.write_text(json.dumps(contract))
        from historical_attendance.recovery import file_hash
        values = dict(self.values, contract_hash=file_hash(contract_path))
        result = self.controller.investigate(values, ['FP1_DEVICE_01'], 'DEMO', 'DEMO', True, True)
        self.assertEqual(result['summary']['status'], 'unverified')
        self.assertEqual(result['summary']['totals']['potentially_missing'], 0)
        self.assertFalse(result['summary']['recovery_authorized'])

    def test_missing_prerequisites_clear_stale_result_without_api(self):
        self.controller.cli = mock.Mock(side_effect=AssertionError('no API'))
        self.controller.last_investigation = {'stale': True}
        with self.assertRaises(ManagerError):
            self.controller.investigate(self.values, [], 'key', 'secret', False, False)
        self.assertIsNone(self.controller.last_investigation)
        self.controller.cli.assert_not_called()

    def test_failures_restore_environment_and_hide_private_details(self):
        self.controller.cli = mock.Mock(side_effect=RuntimeError('PRIVATE employee TEST_SECRET'))
        with mock.patch.dict(os.environ, INVESTIGATION_API_KEY='before', INVESTIGATION_API_SECRET='before'):
            with self.assertRaisesRegex(ManagerError, 'investigation_blocked_or_cancelled'):
                self.controller.investigate(self.values, ['FP1_DEVICE_01'], 'key', 'secret', False, True)
            self.assertEqual(os.environ['INVESTIGATION_API_SECRET'], 'before')

    def test_settings_only_persist_non_sensitive_whitelist(self):
        settings = Settings(self.root / 'app-settings' / 'settings.json')
        settings.save(dict(self.values, api_key='TEST_KEY', api_secret='TEST_SECRET', production_ack='true', employee='PRIVATE'))
        data = settings.path.read_text()
        for secret in ('TEST_KEY', 'TEST_SECRET', 'PRIVATE', 'production_ack', 'site'):
            self.assertNotIn(secret, data)
        self.assertEqual(settings.load()['backup'], self.values['backup'])
        settings.path.write_text('invalid')
        self.assertEqual(settings.load(), {})

    def test_close_cancels_manager_only(self):
        query = self.controller.service_query = mock.Mock()
        self.controller.close()
        query.assert_not_called()
        self.assertTrue(self.controller.cancelled.is_set())
        with self.assertRaises(ManagerError):
            self.approve()

    def test_cancel_before_worker_start(self):
        with self.assertRaisesRegex(VerificationError, 'cancelled'):
            run_bounded(stalled_task, {}, seconds=120, directory=self.root, cancelled=lambda: True)
        self.assertFalse(list(self.root.glob('.investigation-worker-*')))

    def test_cancel_terminates_active_worker_without_continuation(self):
        marker = self.root / 'started.txt'
        active_before = {p.pid for p in multiprocessing.active_children()}
        began = time.monotonic()
        with self.assertRaisesRegex(VerificationError, 'cancelled'):
            run_bounded(stalled_task, dict(marker=str(marker)), seconds=120, directory=self.root,
                        cancelled=lambda: marker.exists())
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(marker.read_text(), 'started')
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, active_before)
        self.assertFalse(list(self.root.glob('.investigation-worker-*')))
