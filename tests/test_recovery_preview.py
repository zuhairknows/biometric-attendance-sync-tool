"""Stage 1 uses only disposable snapshots; no API, delivery or service operations."""
from contextlib import closing, ExitStack
import csv
import datetime as dt
from pathlib import Path
import sqlite3
from unittest import mock

from historical_attendance import recovery_preview as preview
from historical_attendance.recovery import file_hash
from historical_attendance.recovery_store import RecoveryStore
from historical_attendance import recovery
from historical_attendance.client import VerificationError
from manager_app.controller import ManagerError
from manager_app.web import ORIGIN, create_app
from punch_normalization import normalize
from sync_ledger import Ledger
import test_manager as fixtures


class PreviewTests(fixtures.ManagerFixture):
    def options(self, **changes):
        options = dict(backup=str(self.source), backup_hash=self.values['backup_hash'],
            directory=self.values['output'], start=self.values['start'], end=self.values['end'],
            devices=['FP1_DEVICE_01', 'FP2_DEVICE_08', 'FP2_DEVICE_11'], acl_check=self.controller.acl_check)
        return dict(options, **changes)

    def change(self, sql, params=()):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute(sql, params)
        self.original = self.source.read_bytes()
        self.values['backup_hash'] = file_hash(self.source)

    def verify(self, result):
        options = self.options()
        return preview.verify_export(result, **{k: options[k] for k in
            ('backup', 'backup_hash', 'directory', 'acl_check')})

    def test_candidates_counts_private_reports_and_no_external_operations(self):
        with ExitStack() as stack:
            for name in ('requests.sessions.Session.request', 'durable_delivery.FrappeClient.send',
                         'historical_attendance.recovery.approve', 'historical_attendance.recovery.execute',
                         'manager_app.controller.service_state'):
                stack.enter_context(mock.patch(name, side_effect=AssertionError('forbidden')))
            result = preview.preview(**self.options())
        self.assertEqual(len(result['rows']), 3)
        self.assertTrue(all(r['classification'] == preview.CATEGORIES[0] for r in result['rows']))
        self.assertTrue(all(not r['eligible_for_recovery'] for r in result['rows']))
        summary = result['summary']
        self.assertEqual(summary['total'], 3)
        self.assertEqual(summary['per_device']['FP1_DEVICE_01'][preview.CATEGORIES[0]], 1)
        self.assertFalse(summary['destination_verified'])
        self.assertFalse(summary['recovery_authorized'])
        self.assertEqual(summary['schema'], 'local-recovery-preview-v1')
        self.verify(result)
        with Path(result['csv_path']).open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['report_notice'], preview.NOTICE)
        self.assertEqual(rows[0]['eligible_for_recovery'], 'False')
        second = preview.preview(**self.options())
        self.assertNotEqual(second['csv_path'], result['csv_path'])
        self.verify(result)

    def test_every_local_state_and_hold_precedence(self):
        row = preview.SQLiteRepository(self.source).read(dt.datetime(2026, 10, 11),
            dt.datetime(2026, 10, 12), ['FP1_DEVICE_01'])[0]
        cases = [({}, 0), ({'state': 'synced'}, 1), ({'state': 'duplicate'}, 1),
            ({'state': 'rejected', 'error_category': 'cutover_hold'}, 2),
            ({'state': 'retry', 'attempt_count': 1}, 3), ({'state': 'delivering'}, 3),
            ({'state': 'pending', 'attempt_count': 1}, 3), ({'checkin_name': 'PRIVATE_CHECKIN'}, 3),
            ({'last_attempt': 1}, 3), ({'next_retry': 1}, 3), ({'lease_until': 1}, 3),
            ({'last_http_status': 200}, 3), ({'error_message': 'PRIVATE_ERROR'}, 3),
            ({'state': 'unmatched_employee'}, 4), ({'state': 'invalid_record'}, 5),
            ({'payload': '{}'}, 5), ({'source_evidence': 'bad'}, 5),
            ({'punch_timestamp': 'uncertain'}, 5)]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                result = preview.classify(dict(row, **changes))
                self.assertEqual(result['classification'], preview.CATEGORIES[expected])
                self.assertFalse(result['eligible_for_recovery'])
                for key in changes:
                    self.assertEqual(result[key], changes[key])

    def test_real_held_and_attempted_rows_are_unchanged(self):
        self.change("UPDATE punches SET state='rejected',error_category='cutover_hold' WHERE id=1")
        self.change("UPDATE punches SET state='retry',attempt_count=2,last_attempt=1 WHERE id=2")
        result = preview.preview(**self.options())
        self.assertEqual([r['classification'] for r in result['rows']],
                         [preview.CATEGORIES[2], preview.CATEGORIES[3], preview.CATEGORIES[0]])
        self.assertTrue(result['rows'][0]['historical_hold'])

    def test_range_device_selection_empty_and_invalid_timestamp_retained(self):
        self.assertEqual(preview.preview(**self.options(devices=['FP2_DEVICE_08']))['summary']['total'], 1)
        self.assertEqual(preview.preview(**self.options(end='2026-10-11 07:59:59'))['summary']['total'], 0)
        self.change("UPDATE punches SET punch_timestamp='invalid' WHERE id=1")
        result = preview.preview(**self.options(end='2026-10-11 07:59:59'))
        self.assertEqual(result['summary']['total'], 1)
        self.assertEqual(result['rows'][0]['range_status'], 'unknown')
        self.assertEqual(result['rows'][0]['classification'], preview.CATEGORIES[5])

    def test_snapshot_approval_privacy_sidecars_and_output_fail_closed(self):
        for changes in ({'backup_hash': ''}, {'backup_hash': '0'*64}, {'backup': 'relative.sqlite3'},
                        {'acl_check': mock.Mock(side_effect=PermissionError('PRIVATE_PATH'))},
                        {'directory': str(self.source.parent)}):
            with self.subTest(changes=changes), self.assertRaises(preview.PreviewError):
                preview.preview(**self.options(**changes))
        self.assertEqual(list(Path(self.values['output']).iterdir()), [])
        for suffix in ('-wal', '-shm', '-journal'):
            path = Path(str(self.source) + suffix)
            path.write_bytes(b'')
            try:
                with self.assertRaises(preview.PreviewError):
                    preview.preview(**self.options())
            finally:
                path.unlink()

    def test_changed_snapshot_or_report_cannot_be_reused(self):
        result = preview.preview(**self.options())
        path = Path(result['csv_path'])
        path.write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(preview.PreviewError, 'preview_inputs_changed'):
            self.verify(result)
        result = preview.preview(**self.options())
        self.change('UPDATE punches SET attempt_count=1 WHERE id=1')
        with self.assertRaisesRegex(preview.PreviewError, 'preview_inputs_changed'):
            self.verify(result)

    def test_change_during_read_or_cancellation_publishes_no_receipt(self):
        read = preview.SQLiteRepository.read
        def changed(repository, *args):
            rows = read(repository, *args)
            self.change('UPDATE punches SET attempt_count=1 WHERE id=1')
            return rows
        with mock.patch.object(preview.SQLiteRepository, 'read', changed):
            with self.assertRaises(preview.PreviewError):
                preview.preview(**self.options())
        with self.assertRaises(KeyboardInterrupt):
            preview.preview(**self.options(cancelled=lambda: True))
        self.assertEqual(list(Path(self.values['output']).iterdir()), [])

    def test_formula_injection_is_escaped_in_private_csv(self):
        with Ledger(self.source) as ledger:
            ledger.ingest([normalize(dict(user_id='=PRIVATE_FORMULA', timestamp=dt.datetime(2026, 10, 11, 12),
                punch=0, status=1, uid=20), dict(device_id='FP1_DEVICE_01', punch_direction=None))], 'FP1_DEVICE_01')
        self.original = self.source.read_bytes()
        self.values['backup_hash'] = file_hash(self.source)
        result = preview.preview(**self.options())
        with Path(result['csv_path']).open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(any(r['user_id'] == "'=PRIVATE_FORMULA" for r in rows))

    def test_local_preview_cannot_prepare_recovery_batch(self):
        result = preview.preview(**self.options())
        store = RecoveryStore(self.values['workspace'], acl_check=self.controller.acl_check, readonly=True)
        try:
            with self.assertRaisesRegex(VerificationError, 'complete_reconciliation_required'):
                recovery.prepare(store, self.source, result['json_path'], file_hash(result['json_path']),
                    self.values['contract'], self.values['contract_hash'],
                    receipt_review='Synthetic schema rejection test', acl_check=self.controller.acl_check)
        finally:
            store.close()

    def test_controller_cancellation_deadline_and_private_worker_errors(self):
        self.controller.cancelled.set()
        with self.assertRaises(ManagerError):
            self.controller.preview_recovery(self.values, ['FP1_DEVICE_01'])
        self.controller.cancelled.clear()
        for error in (VerificationError('investigation_deadline_exceeded'), RuntimeError('PRIVATE_ERROR')):
            with mock.patch('manager_app.controller.run_bounded', side_effect=error) as run:
                with self.assertRaisesRegex(ManagerError, '^preview_failed$'):
                    self.controller.preview_recovery(self.values, ['FP1_DEVICE_01'])
                self.assertEqual(run.call_args.kwargs['seconds'], 120)
        self.assertEqual(list(Path(self.values['output']).iterdir()), [])


class PreviewWebTests(fixtures.ManagerFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(self.controller, self.values, investigation_only=True)
        self.state = self.app.extensions['manager']
        self.addCleanup(self.state.close)
        self.client = self.app.test_client()
        self.client.get('/', base_url=ORIGIN)

    def post(self, path='/investigation/recovery-preview/run', **changes):
        with self.client.session_transaction(base_url=ORIGIN) as session:
            token = session['csrf']
        data = dict(csrf=token, start=self.values['start'], end=self.values['end'],
                    devices=['FP1_DEVICE_01'], backup_ack='1') if path.endswith('/run') else dict(csrf=token)
        data.update(changes)
        return self.client.post(path, base_url=ORIGIN, data=data)

    def finish(self, response):
        self.assertEqual(response.status_code, 303)
        self.state.thread.join(15)
        self.assertFalse(self.state.thread.is_alive())

    def test_investigation_mode_preview_real_worker_and_export(self):
        self.assertIn(b'Historical Recovery Preview', self.client.get('/investigation', base_url=ORIGIN).data)
        self.finish(self.post())
        self.assertEqual(self.state.job['status'], 'complete', self.state.job)
        page = self.client.get('/investigation/recovery-preview', base_url=ORIGIN)
        self.assertIn(b'Candidate for destination verification', page.data)
        self.assertIn(b'ERPNext destination unverified', page.data)
        self.assertNotIn(str(self.source).encode(), page.data)
        self.assertNotIn(b'SYNTHETIC_EMP', page.data)
        filtered = self.client.get('/investigation/recovery-preview?classification=Already+synchronized', base_url=ORIGIN)
        self.assertNotIn(b'<td>FP1_DEVICE_01</td>', filtered.data)
        self.finish(self.post('/investigation/recovery-preview/export'))
        self.assertEqual(self.state.job['status'], 'complete')
        self.state.values['backup_hash'] = ''
        self.finish(self.post())
        self.assertEqual(self.state.job['status'], 'blocked')
        self.assertIsNone(self.state.recovery_preview)
        self.assertIn(b'approved backup_hash', self.client.get('/investigation/recovery-preview', base_url=ORIGIN).data)
        self.assertEqual(self.post('/investigation/recovery-preview/export').status_code, 409)

    def test_invalid_scope_csrf_and_paths_never_invoke_preview(self):
        with mock.patch.object(self.controller, 'preview_recovery') as task:
            for changes in ({'start': 'invalid'}, {'end': '2026-10-10T00:00'}, {'devices': []},
                            {'devices': ['FP1_DEVICE_99']}, {'devices': ['FP1_DEVICE_01']*2},
                            {'floor': 'FP2'}, {'backup_ack': ''}, {'backup': 'C:/private'},
                            {'csrf': 'wrong'}):
                self.assertIn(self.post(**changes).status_code, (400, 403))
            task.assert_not_called()
        for route in ('/', '/devices', '/investigation/recovery-preview'):
            self.assertEqual(self.client.get(route, base_url=ORIGIN).status_code, 200)
        self.assertEqual(self.client.get('/investigation/recovery-preview?classification=bad', base_url=ORIGIN).status_code, 400)
        self.assertEqual(self.client.get('/investigation/recovery-preview', base_url=ORIGIN,
            headers={'Host': 'evil.test'}).status_code, 400)

    def test_monitor_gate_and_existing_recovery_gate(self):
        app = create_app(self.controller, self.values, monitor_only=True)
        self.addCleanup(app.extensions['manager'].close)
        before = list(Path(self.values['output']).iterdir())
        with mock.patch.object(self.controller, 'preview_recovery') as task:
            for suffix in ('', '/run', '/export'):
                for method in ('GET', 'HEAD', 'POST'):
                    self.assertEqual(app.test_client().open('/investigation/recovery-preview'+suffix,
                        base_url=ORIGIN, method=method).status_code, 403)
            task.assert_not_called()
        for route in ('/recovery', '/recovery/refresh', f"/recovery/{self.batch['id']}/approve",
                      f"/recovery/{self.batch['id']}/simulate", f"/recovery/{self.batch['id']}/export"):
            for method in ('GET', 'POST'):
                self.assertEqual(self.client.open(route, base_url=ORIGIN, method=method).status_code, 403)
        self.assertEqual(list(Path(self.values['output']).iterdir()), before)
