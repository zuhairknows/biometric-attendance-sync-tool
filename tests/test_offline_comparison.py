"""Synthetic file-only acceptance; no production credentials, APIs or services."""
import csv
import datetime as dt
import json
from contextlib import closing
from pathlib import Path
from unittest import mock

from historical_attendance import offline, recovery
from historical_attendance.engine import CLAIMS
from historical_attendance.client import VerificationError
from manager_app.controller import ManagerError
from manager_app.profile import load_profile
from manager_app.web import create_app, ORIGIN
from test_manager import ManagerFixture


class OfflineTests(ManagerFixture):
    def setUp(self):
        super().setUp()
        self.inputs = self.source.parent
        self.employee_path, self.checkin_path = self.inputs / 'employees.csv', self.inputs / 'checkins.csv'
        self.evidence_path = self.inputs / 'offline-evidence.json'
        self.employee_rows = [['SYNTHETIC_EMP', '100', 'Active']]
        self.checkin_rows = [['CHECK-1', 'SYNTHETIC_EMP', '2026-10-11 08:00:00', '', 'FP1_DEVICE_01']]
        self.write_csvs()
        self.evidence = dict(schema='attendance-offline-evidence-v1', review_reference='SYNTHETIC independent review',
            export_set_consistent=True, log_type_encoding_verified=True, null_log_type_token='__SQL_NULL__',
            comparison_contract=json.loads(Path(self.values['contract']).read_text()),
            employee_lookup_results={'100': dict(verified=True, evidence_reference='SYNTHETIC database equality', employee_names=['SYNTHETIC_EMP'])},
            files=dict(snapshot=dict(file=self.source.name, sha256=offline.digest(self.source))))
        self.refresh()
        self.values.update(offline_directory=str(self.inputs), offline_evidence=self.evidence_path.name,
                           offline_evidence_hash=offline.digest(self.evidence_path))
        self.args = dict(directory=str(self.inputs), evidence_name=self.evidence_path.name,
            evidence_hash=self.values['offline_evidence_hash'], snapshot=self.source.name,
            employees=self.employee_path.name, checkins=self.checkin_path.name,
            start=dt.datetime(2026, 10, 11), end=dt.datetime(2026, 10, 11, 23, 59, 59),
            devices=['FP1_DEVICE_01'], output=self.values['output'], acl_check=self.controller.acl_check)

    def write_csvs(self):
        for path, headers, rows in (
            (self.employee_path, ['ID', 'Attendance Device ID', 'Status'], self.employee_rows),
            (self.checkin_path, ['ID', 'Employee', 'Time', 'Log Type', 'Device ID'], self.checkin_rows)):
            with path.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                writer.writerows(rows)

    def refresh(self):
        for role, path, rows in (('employees', self.employee_path, self.employee_rows), ('checkins', self.checkin_path, self.checkin_rows)):
            self.evidence['files'][role] = dict(file=path.name, sha256=offline.digest(path),
                independent_count=len(rows), count_verified=True, count_reference='SYNTHETIC independent database count',
                origin='https://manager.example.test', created_at='2026-10-12T00:00:00+03:00', filters=[])
        self.evidence['files']['employees']['includes_inactive'] = True
        self.evidence['files']['checkins'].update(coverage_start=self.values['start'], coverage_end=self.values['end'],
            filters=[['time', '>=', self.values['start']], ['time', '<=', self.values['end']]])
        self.approve()

    def approve(self):
        self.evidence_path.write_text(json.dumps(self.evidence), encoding='utf-8')
        if hasattr(self, 'args'):
            self.args['evidence_hash'] = offline.digest(self.evidence_path)
            self.values['offline_evidence_hash'] = self.args['evidence_hash']

    def compare(self, **changes):
        with mock.patch('socket.socket', side_effect=AssertionError('offline must not connect')), \
                mock.patch('historical_attendance.client.ReadOnlyFrappeClient', side_effect=AssertionError('no API')):
            return offline.compare(**dict(self.args, **changes))

    def test_existing_and_missing_are_read_only_and_never_recovery_reports(self):
        result = self.compare()
        self.assertEqual(result['rows'][0]['classification'], 'already_existing')
        self.checkin_rows = []
        self.write_csvs(); self.refresh()
        result = self.compare()
        self.assertEqual(result['summary']['status'], 'complete')
        self.assertEqual(result['rows'][0]['classification'], 'potentially_missing')
        self.assertFalse(result['rows'][0]['eligible_for_recovery'])
        self.assertEqual(result['summary']['api_requests_made'], 0)
        self.assertEqual(result['summary']['schema'], 'attendance-offline-comparison-v1')
        self.assertIn(offline.LABEL, result['summary']['limitations'])
        with self.assertRaisesRegex(VerificationError, 'complete_reconciliation_required'):
            # The existing recovery boundary rejects this distinct schema/purpose.
            from historical_attendance.recovery_store import RecoveryStore
            store = RecoveryStore(self.values['workspace'], acl_check=self.controller.acl_check)
            try:
                recovery.prepare(store, self.source, Path(result['json_path']), offline.digest(result['json_path']),
                    Path(self.values['contract']), offline.digest(self.values['contract']),
                    receipt_review='SYNTHETIC', acl_check=self.controller.acl_check)
            finally:
                store.close()

    def test_authoritative_equivalent_ids_do_not_use_python_equality(self):
        for value in ('100 ', '100'):
            self.employee_rows = [['SYNTHETIC_EMP', value, 'Active']]
            self.write_csvs(); self.refresh()
            self.assertEqual(self.compare()['rows'][0]['classification'], 'already_existing')
        self.evidence['employee_lookup_results'] = {}
        self.approve()
        result = self.compare()
        self.assertEqual(result['summary']['status'], 'unverified')
        self.assertEqual(result['summary']['totals']['potentially_missing'], 0)

    def test_case_equivalent_lookup_uses_original_source_id(self):
        from punch_normalization import normalize
        from sync_ledger import Ledger
        source = self.inputs / 'case.sqlite3'
        with Ledger(source) as ledger:
            ledger.ingest([normalize(dict(user_id='AbC', timestamp=dt.datetime(2026,10,11,8), punch=0, status=1, uid=1),
                dict(device_id='FP1_DEVICE_01', punch_direction=None))], 'FP1_DEVICE_01', fetched_at=self.values['end'])
        self.employee_rows = [['SYNTHETIC_EMP', 'abc ', 'Active']]
        self.write_csvs(); self.refresh()
        self.evidence['files']['snapshot'] = dict(file=source.name, sha256=offline.digest(source))
        self.evidence['employee_lookup_results'] = {'AbC': dict(verified=True, employee_names=['SYNTHETIC_EMP'], evidence_reference='SYNTHETIC DB lookup')}
        self.approve()
        self.assertEqual(self.compare(snapshot=source.name)['rows'][0]['classification'], 'already_existing')
        self.evidence['employee_lookup_results']['abc'] = self.evidence['employee_lookup_results'].pop('AbC')
        self.approve()
        self.assertEqual(self.compare(snapshot=source.name)['summary']['status'], 'unverified')

    def test_inactive_ambiguous_and_unmatched_mappings(self):
        self.checkin_rows = []
        for rows, names, classification in (
            ([['SYNTHETIC_EMP', '100 ', 'Inactive']], ['SYNTHETIC_EMP'], 'ambiguous'),
            ([['SYNTHETIC_EMP', '100', 'Active'], ['OTHER', '100 ', 'Inactive']], ['SYNTHETIC_EMP', 'OTHER'], 'ambiguous'),
            ([['SYNTHETIC_EMP', '100', 'Active']], [], 'unmatched_employee')):
            self.employee_rows = rows
            self.write_csvs(); self.refresh()
            self.evidence['employee_lookup_results']['100']['employee_names'] = names
            self.approve()
            self.assertEqual(self.compare()['rows'][0]['classification'], classification)

    def test_cross_device_duplicate_uses_phase1_identity_and_holds_are_preserved(self):
        self.checkin_rows = []
        self.write_csvs(); self.refresh()
        result = self.compare(devices=['FP1_DEVICE_01', 'FP2_DEVICE_08'])
        self.assertEqual(result['summary']['totals']['potentially_missing'], 2)
        # Same canonical time across two existing synthetic punches; preserve evidence.
        from punch_normalization import normalize
        from sync_ledger import Ledger
        with Ledger(self.source) as ledger:
            ledger.ingest([normalize(dict(user_id='100', timestamp=dt.datetime(2026, 10, 11, 8), punch=0, status=1, uid=80),
                dict(device_id='FP2_DEVICE_08', punch_direction=None))], 'FP2_DEVICE_08', fetched_at=self.values['end'])
            ledger.db.execute("UPDATE punches SET state='rejected',error_category='cutover_hold' WHERE id=1")
            ledger.db.commit()
        self.original = self.source.read_bytes()
        self.evidence['files']['snapshot']['sha256'] = offline.digest(self.source)
        self.approve()
        result = self.compare(devices=['FP1_DEVICE_01', 'FP2_DEVICE_08'])
        self.assertEqual(result['summary']['totals']['duplicate_source'], 2)
        self.assertEqual(result['summary']['historical_holds'], 1)
        self.assertTrue(all(not r['eligible_for_recovery'] for r in result['rows']))

    def test_null_is_not_blank_and_encoding_requires_evidence(self):
        self.checkin_rows[0][3] = '__SQL_NULL__'
        self.write_csvs(); self.refresh()
        self.assertEqual(self.compare()['rows'][0]['classification'], 'ambiguous')
        self.evidence['log_type_encoding_verified'] = False
        self.approve()
        self.assertEqual(self.compare()['summary']['status'], 'unverified')

    def test_incomplete_evidence_never_labels_missing(self):
        changes = [lambda e: e['files']['employees'].update(includes_inactive=False),
            lambda e: e['files']['employees'].update(independent_count=50),
            lambda e: e['files']['employees'].update(filters=[['status', '=', 'Active']]),
            lambda e: e['files']['checkins'].update(coverage_end='2026-10-11 00:01:00'),
            lambda e: e['files']['checkins'].update(filters=[]),
            lambda e: e['files']['checkins'].update(created_at='unknown'),
            lambda e: e['comparison_contract']['verification'].update(source_and_site_timezone_match=False),
            lambda e: e.update(export_set_consistent=False)]
        original = json.loads(json.dumps(self.evidence))
        for change in changes:
            self.evidence = json.loads(json.dumps(original))
            change(self.evidence); self.approve()
            result = self.compare()
            self.assertEqual(result['summary']['status'], 'unverified')
            self.assertEqual(result['summary']['totals']['potentially_missing'], 0)

    def test_duplicate_and_malformed_csv_rows_are_not_silently_removed(self):
        for content in ('ID,Employee,Time,Log Type,Device ID\nx,E,2026-10-11 08:00:00,,D\nx,E,2026-10-11 09:00:00,,D\n',
                        'ID,Employee,Time,Log Type,Device ID\nx,E\n',
                        'ID,ID,Time,Log Type,Device ID\nx,E,2026-10-11 08:00:00,,D\n'):
            self.checkin_path.write_text(content)
            self.evidence['files']['checkins']['sha256'] = offline.digest(self.checkin_path)
            self.approve()
            self.assertEqual(self.compare()['summary']['status'], 'unverified')
        self.checkin_path.unlink()
        missing = self.compare()['summary']
        self.assertEqual(missing['status'], 'unverified')
        self.assertIsNone(missing['input_hashes']['checkins'])

    def test_timestamp_precision_and_dst_uncertainty_fail_closed(self):
        for stamp in ('2026-10-11 08:00:00.001', 'not-a-time', '2026-10-11T08:00:00+03:00'):
            self.checkin_rows[0][2] = stamp
            self.write_csvs(); self.refresh()
            self.assertEqual(self.compare()['summary']['status'], 'unverified')
        self.checkin_rows[0][2] = '2026-11-01 01:30:00'
        self.write_csvs(); self.refresh()
        self.evidence['comparison_contract'].update(site_timezone='America/New_York',
            coverage_start='2026-11-01 00:00:00', coverage_end='2026-11-01 03:00:00')
        self.evidence['files']['checkins'].update(coverage_start='2026-11-01 00:00:00', coverage_end='2026-11-01 03:00:00',
            filters=[['time', '>=', '2026-11-01 00:00:00'], ['time', '<=', '2026-11-01 03:00:00']])
        self.approve()
        self.assertEqual(self.compare(start=dt.datetime(2026,11,1), end=dt.datetime(2026,11,1,3))['summary']['status'], 'unverified')

    def test_changed_source_and_csv_or_manifest_after_approval_are_rejected(self):
        for path in (self.source, self.employee_path, self.evidence_path):
            before = path.read_bytes()
            try:
                path.write_bytes(before + b' ')
                with self.assertRaises(Exception):
                    self.compare()
            finally:
                path.write_bytes(before)

    def test_formula_payloads_are_escaped_only_in_reports(self):
        self.employee_rows[0][0] = '=EMPLOYEE'
        self.checkin_rows[0][0:2] = ['@CHECK', '=EMPLOYEE']
        self.write_csvs(); self.refresh()
        self.evidence['employee_lookup_results']['100']['employee_names'] = ['=EMPLOYEE']
        self.approve()
        result = self.compare()
        with Path(result['csv_path']).open(encoding='utf-8', newline='') as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['employee'], "'=EMPLOYEE")
        self.assertEqual(row['checkin_names'], "'@CHECK")
        self.assertEqual(row['report_notice'], offline.LABEL)
        self.assertEqual(result['rows'][0]['employee'], '=EMPLOYEE')

    def test_invalid_source_evidence_is_reported_without_dropping_the_record(self):
        import sqlite3
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE punches SET punch_timestamp='invalid' WHERE id=1")
        self.original = self.source.read_bytes()
        self.evidence['files']['snapshot']['sha256'] = offline.digest(self.source)
        self.approve()
        result = self.compare()
        self.assertEqual(result['summary']['totals']['records'], 1)
        self.assertEqual(result['rows'][0]['classification'], 'invalid')

    def test_changes_during_comparison_prevent_report_publication(self):
        original = self.evidence_path.read_bytes()
        real = offline.investigate
        def change(*args, **kwargs):
            result = real(*args, **kwargs)
            self.evidence_path.write_bytes(original + b' ')
            return result
        try:
            with mock.patch.object(offline, 'investigate', side_effect=change):
                with self.assertRaisesRegex(VerificationError, 'offline_inputs_changed'):
                    self.compare()
        finally:
            self.evidence_path.write_bytes(original)

    def test_empty_range_and_uppercase_approved_manifest_hash(self):
        result = self.compare(start=dt.datetime(2026,10,11,20), end=dt.datetime(2026,10,11,21),
                              evidence_hash=self.args['evidence_hash'].upper())
        self.assertEqual(result['summary']['totals']['records'], 0)
        self.assertFalse(result['summary']['recovery_authorized'])

    def test_paths_permissions_and_output_separation_fail_closed(self):
        for changes in (dict(snapshot='../synthetic.sqlite3'), dict(evidence_name=str(self.evidence_path)),
                        dict(output=str(self.inputs)), dict(directory=str(self.root / 'missing')),
                        dict(employees=self.checkin_path.name)):
            with self.assertRaises(Exception):
                self.compare(**changes)
        with self.assertRaises(Exception):
            self.compare(acl_check=mock.Mock(side_effect=PermissionError('PRIVATE PATH SECRET')))

    def test_snapshot_sidecars_are_rejected_before_opening_sqlite(self):
        marker = Path(str(self.source) + '-wal')
        marker.write_bytes(b'SYNTHETIC')
        try:
            with mock.patch.object(offline.SQLiteRepository, 'read', side_effect=AssertionError('must not open')):
                with self.assertRaisesRegex(VerificationError, 'self_contained_rollback_journal_backup_required'):
                    self.compare()
        finally:
            marker.unlink()

    def test_flask_workflow_uses_bounded_process_and_no_credentials(self):
        app = create_app(self.controller, self.values, investigation_only=True)
        state, client = app.extensions['manager'], app.test_client()
        self.addCleanup(state.close)
        self.assertEqual(client.get('/investigation/offline', base_url=ORIGIN).status_code, 200)
        with client.session_transaction(base_url=ORIGIN) as session:
            token = session['csrf']
        form = dict(csrf=token, start=self.values['start'], end=self.values['end'], devices=['FP1_DEVICE_01'],
            snapshot=self.source.name, employees=self.employee_path.name, checkins=self.checkin_path.name, backup_ack='1')
        response = client.post('/investigation/offline/run', base_url=ORIGIN, data=form)
        self.assertEqual(response.status_code, 303)
        state.thread.join(20)
        self.assertEqual(state.job['status'], 'complete', state.job)
        self.assertIn(offline.LABEL.encode(), client.get('/investigation/offline', base_url=ORIGIN).data)
        self.assertEqual(state.offline['summary']['totals']['already_existing'], 1)
        response = client.post('/investigation/offline/export', base_url=ORIGIN, data=dict(csrf=token))
        self.assertEqual(response.status_code, 303)
        state.thread.join(10)
        self.assertEqual(state.job['status'], 'complete', state.job)
        self.assertEqual(client.get('/recovery', base_url=ORIGIN).status_code, 403)
        for changes in (dict(snapshot='../private.sqlite3'), dict(employees='C:/secret.csv'), dict(devices=['UNKNOWN']),
                        dict(end='2000-01-01 00:00:00'), dict(api_key='not-allowed')):
            self.assertEqual(client.post('/investigation/offline/run', base_url=ORIGIN,
                             data=dict(form, **changes)).status_code, 400)
        self.assertEqual(client.post('/investigation/offline/run', base_url=ORIGIN).status_code, 403)
        monitor = create_app(self.controller, self.values, monitor_only=True).test_client()
        for route in ('/investigation/offline', '/investigation/offline/run', '/investigation/offline/export'):
            self.assertEqual(monitor.get(route, base_url=ORIGIN).status_code, 403)
            self.assertEqual(monitor.post(route, base_url=ORIGIN, data=form).status_code, 403)

    def test_profile_accepts_only_explicit_offline_directory_and_evidence(self):
        path = self.root / 'profile.json'
        path.write_text(json.dumps(self.values))
        self.assertEqual(load_profile(str(path), acl_check=self.controller.acl_check)['offline_evidence'], self.evidence_path.name)
