"""Private diagnostic acceptance with synthetic files, mocked ACLs and real spawn."""
import json
from pathlib import Path
import unittest
from unittest import mock

import test_offline_comparison as fixtures
from historical_attendance import offline
from historical_attendance.diagnostics import CODES, EVENTS, STAGES, DiagnosticError, Trace
from manager_app.controller import ManagerError
from manager_app.profile import load_profile
from manager_app.web import create_app, ORIGIN
from scripts.capture_history_safety import CaptureError


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OfflineTests('test_existing_and_missing_are_read_only_and_never_recovery_reports')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown)
        self.directory = self.f.root / 'diagnostics'
        self.directory.mkdir()
        self.f.values['diagnostics_directory'] = str(self.directory)

    def run_comparison(self, **changes):
        return self.f.controller.compare_offline(dict(self.f.values, **changes), self.f.source.name,
            self.f.employee_path.name, self.f.checkin_path.name, ['FP1_DEVICE_01'])

    def records(self):
        records = [json.loads(line) for p in self.directory.glob('offline-*.jsonl') for line in p.read_text().splitlines()]
        for record in records:
            self.assertEqual(set(record), {'reference', 'stage', 'code', 'event'})
            self.assertIn(record['stage'], STAGES)
            self.assertIn(record['code'], CODES)
            self.assertIn(record['event'], EVENTS)
            self.assertEqual(len(record['reference']), 32)
            self.assertTrue(all(c in '0123456789abcdef' for c in record['reference']))
        text = '\n'.join(str(value) for record in records for value in record.values())
        for private in (str(self.f.root), str(self.f.root.resolve()), self.f.source.name, 'SYNTHETIC_EMP', 'CHECK-1',
                        '2026-10-11', 'PRIVATE', 'API_SECRET', 'source_evidence', '"100"'):
            self.assertNotIn(private, text)
        return records

    def assert_failure(self, stage, code, operation):
        with self.assertRaises(ManagerError) as caught:
            operation()
        failure = caught.exception.diagnostic
        self.assertIsInstance(failure, DiagnosticError)
        self.assertEqual((failure.stage, failure.code), (stage, code))
        self.assertNotIn(str(self.f.root), failure.message)
        self.assertIn(failure.reference, failure.message)
        records = self.records()
        self.assertTrue(any(r['stage'] == stage and r['code'] == code and r['event'] == 'failed' for r in records))

    def test_source_file_acl_failure_is_precise_without_weakening_policy(self):
        guard = self.f.controller.acl_check
        def deny_source(path):
            if Path(path) == self.f.source:
                raise CaptureError('output_acl_not_private')
            guard(path)
        self.f.controller.acl_check = deny_source
        self.assert_failure('source_path', 'output_acl_not_private', self.run_comparison)

    def test_output_failure_can_be_logged_independently_of_output_directory(self):
        self.assert_failure('output', 'output_must_be_existing_absolute_directory',
            lambda: self.run_comparison(output=str(self.f.root / 'PRIVATE_MISSING')))

    def test_evidence_hash_failure_survives_actual_child_process(self):
        self.assert_failure('evidence_hash', 'sha256_mismatch',
                            lambda: self.run_comparison(offline_evidence_hash='0' * 64))

    def test_source_hash_failure_survives_actual_child_process(self):
        self.f.evidence['files']['snapshot']['sha256'] = '0' * 64
        self.f.approve()
        self.assert_failure('source_hash', 'offline_source_changed_or_unapproved', self.run_comparison)

    def test_profile_and_missing_file_failures_are_separate(self):
        values = dict(self.f.values)
        del values['offline_evidence_hash']
        self.assert_failure('profile', 'missing_setting', lambda: self.f.controller.compare_offline(values,
            self.f.source.name, self.f.employee_path.name, self.f.checkin_path.name, ['FP1_DEVICE_01']))
        self.assert_failure('evidence_path', 'file_not_found',
            lambda: self.run_comparison(offline_evidence='PRIVATE_MISSING.json'))

    def test_sqlite_schema_and_unknown_exceptions_never_expose_raw_text(self):
        for error, code in ((RuntimeError('Unsupported SQLite schema version; database left unchanged'), 'source_schema_unsupported'),
                            (RuntimeError('PRIVATE EMPLOYEE API_SECRET 2026-10-11'), 'unexpected_error')):
            with mock.patch('manager_app.controller.run_bounded', side_effect=lambda task, options, **limits: task(**options)), \
                    mock.patch.object(offline.SQLiteRepository, 'read', side_effect=error):
                self.assert_failure('source_read', code, self.run_comparison)

    def test_csv_validation_remains_an_unverified_report_and_is_logged(self):
        self.f.checkin_path.write_text('PRIVATE BAD HEADER\nPRIVATE RECORD\n')
        self.f.evidence['files']['checkins']['sha256'] = offline.digest(self.f.checkin_path)
        self.f.approve()
        result = self.run_comparison()
        self.assertEqual(result['summary']['status'], 'unverified')
        self.assertEqual(result['summary']['totals']['potentially_missing'], 0)
        self.assertTrue(any(r['stage'] == 'checkins_csv' and r['code'] == 'offline_csv_headers_invalid'
                            and r['event'] == 'unverified' for r in self.records()))

    def test_missing_times_complete_as_unverified_and_are_not_worker_failures(self):
        self.f.evidence['files']['employees']['created_at'] = None
        del self.f.evidence['files']['checkins']['created_at']
        self.f.approve()
        result = self.run_comparison()
        self.assertEqual(result['summary']['status'], 'unverified')
        self.assertEqual(result['summary']['totals']['potentially_missing'], 0)
        self.assertFalse(result['summary']['recovery_authorized'])
        self.assertTrue(all(not r['eligible_for_recovery'] for r in result['rows']))
        records = self.records()
        for role in ('employees', 'checkins'):
            self.assertTrue(any(r['stage'] == 'export_times' and r['code'] == f'offline_{role}_creation_time_missing'
                                and r['event'] == 'unverified' for r in records))
        self.assertTrue(any(r['event'] == 'complete' and r['code'] == 'result_unverified' for r in records))
        self.assertFalse(any(r['event'] == 'failed' for r in records))

    def test_publication_failure_and_deadline_have_different_stages(self):
        with mock.patch('manager_app.controller.run_bounded', side_effect=lambda task, options, **limits: task(**options)), \
                mock.patch.object(offline, 'export', side_effect=PermissionError('PRIVATE API_SECRET')):
            self.assert_failure('publication', 'permission_denied', self.run_comparison)
        from historical_attendance.client import VerificationError
        with mock.patch('manager_app.controller.run_bounded', side_effect=VerificationError('investigation_deadline_exceeded')):
            self.assert_failure('worker', 'investigation_deadline_exceeded', self.run_comparison)

    def test_unsafe_or_unwritable_diagnostics_fail_closed_without_fallback(self):
        for directory, code in ((str(self.f.inputs), 'output_contains_production_state_or_logs'),
                                (str(self.f.root / 'PRIVATE_MISSING'), 'output_must_be_existing_absolute_directory')):
            with mock.patch('manager_app.controller.run_bounded') as runner:
                with self.assertRaises(ManagerError) as caught:
                    self.run_comparison(diagnostics_directory=directory)
                self.assertEqual(caught.exception.diagnostic.code, code)
                runner.assert_not_called()
        with mock.patch('pathlib.Path.open', side_effect=PermissionError('PRIVATE API_SECRET')):
            with self.assertRaises(ManagerError) as caught:
                self.run_comparison()
            self.assertEqual(caught.exception.diagnostic.stage, 'diagnostics')
            self.assertEqual(caught.exception.diagnostic.code, 'permission_denied')
        self.assertEqual(list(self.directory.glob('*.jsonl')), [])

    def test_worker_logging_failure_has_safe_receipt_even_when_it_cannot_be_logged(self):
        real = Trace.emit
        def emit(trace, event, code):
            if trace.stage == 'publication':
                raise DiagnosticError('diagnostics', 'diagnostics_unavailable', trace.reference)
            return real(trace, event, code)
        with mock.patch('manager_app.controller.run_bounded', side_effect=lambda task, options, **limits: task(**options)), \
                mock.patch.object(Trace, 'emit', emit):
            with self.assertRaises(ManagerError) as caught:
                self.run_comparison()
        self.assertEqual(caught.exception.diagnostic.code, 'diagnostics_unavailable')
        self.assertNotIn('PRIVATE', caught.exception.diagnostic.message)

    def test_request_job_polling_and_browser_show_same_safe_reference(self):
        self.f.values['offline_evidence_hash'] = '0' * 64
        app = create_app(self.f.controller, self.f.values, investigation_only=True)
        state, client = app.extensions['manager'], app.test_client()
        self.addCleanup(state.close)
        client.get('/', base_url=ORIGIN)
        with client.session_transaction(base_url=ORIGIN) as session:
            token = session['csrf']
        form = dict(csrf=token, start=self.f.values['start'], end=self.f.values['end'],
                    devices=['FP1_DEVICE_01'], snapshot=self.f.source.name, employees=self.f.employee_path.name,
                    checkins=self.f.checkin_path.name, backup_ack='1')
        response = client.post('/investigation/offline/run', base_url=ORIGIN, data=form)
        self.assertEqual(response.status_code, 303)
        state.thread.join(20)
        self.assertEqual(state.job['status'], 'blocked')
        self.assertIn('evidence_hash / sha256_mismatch', state.job['error'])
        records = self.records()
        reference = records[0]['reference']
        self.assertIn(reference, state.job['error'])
        for route in (response.headers['Location'], '/investigation/offline'):
            body = client.get(route, base_url=ORIGIN).data
            self.assertIn(reference.encode(), body)
            self.assertNotIn(str(self.f.root).encode(), body)
        status = client.get(response.headers['Location'] + '/status', base_url=ORIGIN).json
        self.assertEqual(status['error'], state.job['error'])
        bad = client.post('/investigation/offline/run', base_url=ORIGIN, data=dict(form, snapshot='../PRIVATE'))
        self.assertEqual(bad.status_code, 400)
        self.assertIn(b'request_rejected', bad.data)
        self.assertNotIn(b'../PRIVATE', bad.data)

    def test_blocked_modes_and_csrf_do_not_initialize_logging(self):
        for monitor in (False, True):
            app = create_app(self.f.controller, self.f.values, monitor_only=monitor, investigation_only=not monitor)
            client = app.test_client()
            with mock.patch.object(self.f.controller, 'offline_diagnostics') as initialize:
                response = client.post('/investigation/offline/run', base_url=ORIGIN)
                self.assertEqual(response.status_code, 403)
                if monitor:
                    self.assertEqual(client.get('/investigation/offline', base_url=ORIGIN).status_code, 403)
                initialize.assert_not_called()
        self.assertEqual(list(self.directory.glob('*.jsonl')), [])

    def test_profile_defaults_to_private_profile_storage_and_allows_explicit_directory(self):
        path = self.f.root / 'profile.json'
        values = dict(self.f.values)
        del values['diagnostics_directory']
        path.write_text(json.dumps(values))
        self.assertEqual(load_profile(str(path), acl_check=self.f.controller.acl_check)['diagnostics_directory'], str(path.parent.resolve()))
        values['diagnostics_directory'] = str(self.directory)
        path.write_text(json.dumps(values))
        self.assertEqual(load_profile(str(path), acl_check=self.f.controller.acl_check)['diagnostics_directory'], str(self.directory.resolve()))
