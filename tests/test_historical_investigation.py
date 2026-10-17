"""Phase 2A acceptance: synthetic SQLite and mocked HTTP, never real configuration."""
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from historical_attendance.client import ReadOnlyFrappeClient, VerificationError
from historical_attendance.engine import CLAIMS, investigate
from historical_attendance.repository import SQLiteRepository
from historical_attendance.reporting import export
from punch_normalization import normalize
from sync_ledger import Ledger
from scripts import investigate_attendance as cli

START = dt.datetime(2026, 10, 1)
END = dt.datetime(2026, 10, 17, 23, 59, 59)
STAMP = dt.datetime(2026, 10, 11, 8)


def contract():
    return dict(schema='attendance-investigation-contract-v1', site_url='https://synthetic.example.test',
        site_timezone='Asia/Riyadh', timestamp_precision='seconds', duplicate_key='employee_time_log_type',
        coverage_start=str(START), coverage_end=str(END), device_ids=['TEST_A', 'TEST_B'],
        evidence_reference='SYNTHETIC TEST ONLY', verification={c: True for c in CLAIMS})


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'input' / 'synthetic.sqlite3'
        self.employees = [dict(name='SYNTHETIC_EMP_01', attendance_device_id='100', status='Active', modified=str(START))]
        self.checkins = []
        self.calls = []
        self.policy = contract()
        with Ledger(self.path):
            pass
        self.add()
        self.client = ReadOnlyFrappeClient(self.policy['site_url'], 'TEST_KEY', 'TEST_SECRET', self.request, page_size=1)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        self.assertEqual(method, 'GET')
        self.assertTrue('/api/resource/' in url or url.endswith('/api/method/frappe.client.get_count'))
        self.assertFalse(kwargs['allow_redirects'])
        if url.endswith('/api/method/frappe.client.get_count'):
            data = self.employees if kwargs['params']['doctype'] == 'Employee' else self.checkins
            return mock.Mock(status_code=200, json=lambda: {'message': len(data)})
        data = self.employees if url.endswith('/Employee') else self.checkins
        data = sorted(data, key=lambda row: row['name'])
        offset = kwargs['params']['limit_start']
        size = kwargs['params']['limit_page_length']
        return mock.Mock(status_code=200, json=lambda: {'data': data[offset:offset + size]})

    def add(self, device='TEST_A', **changes):
        raw = dict(user_id='100', timestamp=STAMP, punch=0, status=1, uid=1)
        raw.update(changes)
        with Ledger(self.path) as db:
            db.ingest([normalize(raw, dict(device_id=device, punch_direction=None))], device)

    def checkin(self, **changes):
        row = dict(name='SYNTHETIC_CHECKIN_01', employee='SYNTHETIC_EMP_01', time=str(STAMP),
                   log_type='', device_id='OTHER_DEVICE', modified=str(START))
        row.update(changes)
        self.checkins.append(row)

    def run_report(self, **kwargs):
        before = self.path.read_bytes()
        try:
            return investigate(SQLiteRepository(self.path), self.client, START, END,
                               ['TEST_A', 'TEST_B'], self.policy, **kwargs)
        finally:
            self.assertEqual(before, self.path.read_bytes())
            self.assertTrue(all(c[0] == 'GET' for c in self.calls))

    def category(self):
        return self.run_report()[0][0]['classification']

    def test_already_existing_all_destination_devices(self):
        self.checkin()
        self.assertEqual(self.category(), 'already_existing')

    def test_genuinely_missing_is_only_a_potential_candidate(self):
        rows, summary = self.run_report()
        self.assertEqual(rows[0]['classification'], 'potentially_missing')
        self.assertFalse(rows[0]['eligible_for_recovery'])
        self.assertFalse(summary['recovery_authorized'])
        self.assertEqual(summary['per_device']['TEST_B']['records'], 0)

    def test_cross_device_duplicate_marks_every_member(self):
        self.add('TEST_B', uid=2)
        rows, _ = self.run_report()
        self.assertEqual([r['classification'] for r in rows], ['duplicate_source'] * 2)

    def test_missing_mapping(self):
        self.employees = []
        self.assertEqual(self.category(), 'unmatched_employee')

    def test_duplicate_mapping_including_inactive_is_ambiguous(self):
        self.employees.append(dict(self.employees[0], name='SYNTHETIC_EMP_02', status='Inactive'))
        self.assertEqual(self.category(), 'ambiguous')

    def test_inactive_employee(self):
        self.employees[0]['status'] = 'Inactive'
        self.assertEqual(self.category(), 'ambiguous')

    def test_historical_hold_is_preserved(self):
        with Ledger(self.path) as db:
            db.db.execute("UPDATE punches SET state='rejected', error_category='cutover_hold'")
            db.db.commit()
        rows, summary = self.run_report()
        self.assertEqual(rows[0]['state'], 'rejected')
        self.assertEqual(rows[0]['error_category'], 'cutover_hold')
        self.assertTrue(rows[0]['historical_hold'])
        self.assertEqual(summary['historical_holds'], 1)
        self.assertFalse(rows[0]['eligible_for_recovery'])

    def test_timeout_fails_closed(self):
        self.client._request = mock.Mock(side_effect=TimeoutError('SECRET EMPLOYEE DATA'))
        rows, summary = self.run_report()
        self.assertEqual(rows[0]['classification'], 'unable_to_verify')
        self.assertEqual(summary['blockers'], ['destination_count_unverified'])
        self.assertNotIn('SECRET', json.dumps(summary))

    def test_incomplete_pagination_and_ignored_offset(self):
        original = self.client._request
        def ignored_offset(method, url, **kwargs):
            if '/api/resource/Employee' in url:
                return mock.Mock(status_code=200, json=lambda: {'data': self.employees})
            return original(method, url, **kwargs)
        self.client._request = ignored_offset
        self.assertEqual(self.category(), 'unable_to_verify')

    def test_microseconds_and_employee_time_only_rule(self):
        self.policy['timestamp_precision'] = 'microseconds'
        self.add(timestamp=STAMP.replace(microsecond=1), uid=2)
        rows, _ = self.run_report()
        self.assertEqual([r['classification'] for r in rows], ['potentially_missing'] * 2)
        self.policy['duplicate_key'] = 'employee_time'
        self.checkin(log_type=None)
        self.assertEqual(self.category(), 'already_existing')

    def test_range_endpoints_before_and_after_phase1_activation(self):
        self.add(timestamp=START, uid=2)
        self.add(timestamp=END, uid=3)
        self.add(timestamp=END + dt.timedelta(seconds=1), uid=4)
        rows, _ = self.run_report()
        self.assertEqual(len(rows), 3)

    def test_missing_schema_is_not_initialized(self):
        import sqlite3
        path = self.root / 'empty.sqlite3'
        sqlite3.connect(path).close()
        before = path.read_bytes()
        with self.assertRaises(RuntimeError):
            SQLiteRepository(path).read(START, END, ['TEST_A'])
        self.assertEqual(path.read_bytes(), before)

    def test_http_cancellation_callback(self):
        self.client.cancelled = lambda: True
        with self.assertRaises(KeyboardInterrupt):
            self.run_report()
        self.assertFalse(self.calls)

    def test_pagination_page_limit(self):
        self.client.max_pages = 1
        self.assertEqual(self.category(), 'unable_to_verify')

    def test_malformed_response_or_http_error(self):
        for response in (mock.Mock(status_code=403), mock.Mock(status_code=200, json=lambda: {'message': []}),
                         mock.Mock(status_code=200, json=lambda: {'data': [{'name': 'PRIVATE'}]})):
            with self.subTest(response=response):
                self.client._request = mock.Mock(return_value=response)
                self.assertEqual(self.category(), 'unable_to_verify')

    def test_timestamp_fraction_and_dst_ambiguity(self):
        self.add(timestamp=STAMP.replace(microsecond=1), uid=2)
        self.assertEqual(self.run_report()[0][1]['classification'], 'ambiguous')
        self.policy['site_timezone'] = 'America/New_York'
        self.policy['coverage_start'] = '2026-01-01 00:00:00'
        self.policy['coverage_end'] = '2026-12-31 23:59:59'
        # Deterministic timezone fixture: Windows may not have the IANA database.
        class RepeatedHour(dt.tzinfo):
            def utcoffset(self, value):
                return dt.timedelta(hours=-5 if value.fold else -4)
        with mock.patch('historical_attendance.engine.site_zone', return_value=RepeatedHour()):
            self.assertEqual(self.category(), 'ambiguous')

    def test_unknown_timezone_is_unverified(self):
        self.policy['site_timezone'] = 'Not/AZone'
        self.assertEqual(self.category(), 'unable_to_verify')

    def test_premature_empty_page_detected_by_count(self):
        original = self.client._request
        def truncated(method, url, **kwargs):
            if '/api/resource/' in url:
                return mock.Mock(status_code=200, json=lambda: {'data': []})
            return original(method, url, **kwargs)
        self.client._request = truncated
        rows, summary = self.run_report()
        self.assertEqual(rows[0]['classification'], 'unable_to_verify')
        self.assertIn('pagination_count_mismatch', summary['blockers'])

    def test_unverified_out_of_range_evidence_is_not_discarded(self):
        with Ledger(self.path) as db:
            db.db.execute("UPDATE punches SET punch_timestamp='2099-01-01 00:00:00'")
            db.db.commit()
        self.assertEqual(self.category(), 'invalid')

    def test_uncertain_destination_timestamp(self):
        self.checkin(time='unparseable')
        self.assertEqual(self.category(), 'unable_to_verify')

    def test_null_and_conflicting_direction_are_not_empty(self):
        for value in (None, 'IN'):
            self.checkins = []
            self.checkin(log_type=value)
            self.assertEqual(self.category(), 'ambiguous')

    def test_multiple_destination_identities(self):
        self.checkin()
        self.checkin(name='SYNTHETIC_CHECKIN_02')
        self.assertEqual(self.category(), 'ambiguous')

    def test_ledger_success_without_destination_needs_review(self):
        with Ledger(self.path) as db:
            db.db.execute("UPDATE punches SET state='synced', checkin_name='SYNTHETIC_OLD'")
            db.db.commit()
        self.assertEqual(self.category(), 'unable_to_verify')

    def test_source_identity_disagreement(self):
        with Ledger(self.path) as db:
            db.db.execute("UPDATE punches SET user_id='DIFFERENT'")
            db.db.commit()
        self.assertEqual(self.category(), 'invalid')

    def test_invalid_unknown_timestamp_stays_visible(self):
        self.add(timestamp='bad', uid=2)
        rows, _ = self.run_report()
        self.assertEqual(rows[1]['classification'], 'invalid')
        self.assertEqual(rows[1]['range_status'], 'unknown')

    def test_empty_range_and_device_no_api(self):
        self.assertEqual(investigate(SQLiteRepository(self.path), self.client, END, END,
            ['TEST_A'], self.policy)[0], [])
        self.assertFalse(self.calls)
        with self.assertRaises(VerificationError):
            investigate(SQLiteRepository(self.path), self.client, END, START, ['TEST_A'], self.policy)

    def test_unverified_contract_blocks_absence_claims(self):
        for change in ({'timestamp_precision': 'guess'}, {'verification': {}}, {'site_url': 'https://wrong.test'}):
            with self.subTest(change=change):
                self.policy = dict(contract(), **change)
                self.assertEqual(self.category(), 'unable_to_verify')
        self.assertFalse(self.calls)

    def test_changed_repeat_scan(self):
        original = self.client.scan
        calls = []
        def scan(doctype, filters):
            calls.append(doctype)
            rows = original(doctype, filters)
            return [] if len(calls) == 3 else rows
        self.client.scan = scan
        self.assertEqual(self.category(), 'unable_to_verify')

    def test_interrupted_investigation_leaves_no_changes(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_report(cancelled=lambda: True)
        self.client._request = mock.Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.run_report()

    def test_readonly_connection_refuses_updates(self):
        import sqlite3
        with Ledger(self.path, readonly=True) as ledger:
            with self.assertRaises(sqlite3.OperationalError):
                ledger.db.execute("UPDATE punches SET state='synced'")

    def test_report_has_original_audit_and_safe_cells(self):
        rows, summary = self.run_report()
        rows[0]['device_id'] = '=FORMULA'
        output = self.root / 'report'
        output.mkdir()
        marker = export(output, rows, summary)
        manifest = json.loads((output / marker['file']).read_text())
        details = output / manifest['reports'][0]['file']
        self.assertEqual(hashlib.sha256(details.read_bytes()).hexdigest(), manifest['reports'][0]['sha256'])
        with details.open(newline='', encoding='utf-8') as stream:
            record = next(csv.DictReader(stream))
        self.assertEqual(record['device_id'], "'=FORMULA")
        for field in ('identity_key', 'source_evidence', 'payload', 'first_captured', 'state'):
            self.assertIn(field, record)

    def cli_args(self):
        config = self.root / 'input' / 'contract.json'
        config.write_text(json.dumps(self.policy), encoding='utf-8')
        output = self.root / 'output'
        output.mkdir(exist_ok=True)
        return ['--ledger-copy', str(self.path), '--start', str(START), '--end', str(END),
                '--device', 'TEST_A', '--site-url', self.policy['site_url'], '--contract', str(config),
                '--contract-sha256', hashlib.sha256(config.read_bytes()).hexdigest(), '--output', str(output)]

    def test_cli_and_interruption_no_employee_or_credential_logs(self):
        args = self.cli_args()
        before = self.path.read_bytes()
        for failure, code in ((None, 0), (TimeoutError('TEST_SECRET'), 2), (KeyboardInterrupt(), 130)):
            transport = self.request if failure is None else mock.Mock(side_effect=failure)
            stream = io.StringIO()
            with mock.patch.dict('os.environ', INVESTIGATION_API_KEY='TEST_KEY', INVESTIGATION_API_SECRET='TEST_SECRET'):
                with contextlib.redirect_stdout(stream):
                    result = cli.main(args, request=transport, acl_check=lambda _: None)
            self.assertEqual(result, code)
            self.assertNotIn('TEST_SECRET', stream.getvalue())
            self.assertNotIn('SYNTHETIC_EMP', stream.getvalue())
            self.assertEqual(before, self.path.read_bytes())

    def test_client_rejects_unsafe_origins(self):
        for url in ('http://example.test', 'https://user:pass@example.test', 'https://example.test?secret=x'):
            with self.assertRaises(VerificationError):
                ReadOnlyFrappeClient(url, 'key', 'secret', self.request)


if __name__ == '__main__':
    unittest.main()
