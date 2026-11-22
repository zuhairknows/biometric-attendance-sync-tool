"""Phase 2B synthetic acceptance; no real ERPNext, device, service or configuration."""
import contextlib
import datetime as dt
import io
import json
import multiprocessing
from pathlib import Path
import unittest
from unittest import mock

import test_historical_investigation as fixtures
from historical_attendance import recovery as core
from historical_attendance.client import VerificationError
from historical_attendance.recovery_store import RecoveryStore
from historical_attendance.recovery_simulation import SimulationTarget
from historical_attendance import reporting
from scripts import recover_attendance as cli
from sync_ledger import Ledger
from durable_startup import worker_lock


def allow_acl(path):
    pass


def normal_worker(path, ready, release):
    with worker_lock(path):
        ready.set()
        release.wait(10)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InvestigationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.workspace = self.root / 'private-audit'
        self.workspace.mkdir()
        self.store = RecoveryStore(self.workspace, acl_check=allow_acl)
        self.addCleanup(self.store.close)
        self.guard = mock.patch.object(core, 'validate_output', side_effect=lambda output, **kw:
            reporting.validate_output(output, **kw, acl_check=allow_acl))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.fixture.policy['destination_environment'] = 'synthetic'
        self.contract = self.fixture.path.parent / 'contract.json'
        self.summary = self.fixture.path.parent / 'summary.json'
        self.details = self.fixture.path.parent / 'details.csv'
        self.scenario = self.root / 'synthetic-destination.json'
        self.normal_path = self.root / 'normal-worker.sqlite3'
        self.write_scenario()
        self.build_report()

    def write_scenario(self, **changes):
        data = dict(synthetic_only=True, site_url=self.fixture.policy['site_url'],
                    employees=self.fixture.employees, checkins=self.fixture.checkins)
        data.update(changes)
        self.scenario.write_text(json.dumps(data), encoding='utf-8')

    def build_report(self, mutate=None):
        self.contract.write_text(json.dumps(self.fixture.policy), encoding='utf-8')
        rows, summary = self.fixture.run_report()
        summary['contract_file_sha256'] = core.file_hash(self.contract)
        with self.details.open('w', encoding='utf-8', newline='') as stream:
            reporting.write_csv(stream, rows)
        summary['reports'] = [dict(file=self.details.name, sha256=core.file_hash(self.details))]
        if mutate:
            mutate(summary)
        self.summary.write_text(json.dumps(summary), encoding='utf-8')

    def prepare(self):
        return core.prepare(self.store, self.fixture.path, self.summary, core.file_hash(self.summary),
                            self.contract, core.file_hash(self.contract), receipt_review='SYNTHETIC COMPLETE RECEIPT REVIEW')

    def approved(self):
        batch, fingerprint = self.prepare()
        core.approve(self.store, batch, fingerprint, [1], 'SYNTHETIC EXPLICIT APPROVAL')
        return batch, fingerprint

    def execute(self, batch, fingerprint, target=None, **kwargs):
        before = self.fixture.path.read_bytes()
        target = target or SimulationTarget(self.scenario)
        try:
            core.execute(self.store, batch, fingerprint, target, self.normal_path, **kwargs)
        finally:
            self.assertEqual(before, self.fixture.path.read_bytes())
        return target

    def status(self, batch):
        return self.store.items(batch)[0]['status']

    def test_preparation_and_explicit_selected_approval(self):
        batch, fingerprint = self.prepare()
        self.assertEqual(self.status(batch), 'Pending Approval')
        target = self.execute(batch, fingerprint)
        self.assertFalse(target.calls)
        core.approve(self.store, batch, fingerprint, [1], 'REVIEWED')
        core.approve(self.store, batch, fingerprint, [1], 'REPEATED APPROVAL')
        self.assertEqual(self.status(batch), 'Approved')

    def test_normal_recovery_verified_and_repeated_execution(self):
        batch, fingerprint = self.approved()
        target = self.execute(batch, fingerprint)
        self.assertEqual(self.status(batch), 'Recovered')
        self.assertEqual(target.calls.count('POST'), 1)
        self.assertTrue(all(method == 'GET' for method in target.calls[:target.calls.index('POST')]))
        self.assertTrue(all(method == 'GET' for method in target.calls[target.calls.index('POST') + 1:]))
        self.assertTrue(self.store.db.execute('SELECT * FROM events WHERE batch=?', (batch,)).fetchall())
        again = self.execute(batch, fingerprint)
        self.assertFalse(again.calls)

    def test_independently_existing_record_is_skipped_without_post(self):
        batch, fingerprint = self.approved()
        self.fixture.checkin()
        self.write_scenario()
        target = self.execute(batch, fingerprint)
        self.assertEqual(self.status(batch), 'Already Exists')
        self.assertNotIn('POST', target.calls)

    def test_compatibility_or_unverified_reports_never_seed_batch(self):
        for changes in (dict(status='unverified'), dict(blockers=['unknown']),
                        dict(purpose='production_read_only_compatibility', compatibility_status='reads_complete')):
            self.build_report(lambda report: report.update(changes))
            with self.assertRaises(VerificationError):
                self.prepare()

    def test_changed_contract_report_source_or_csv_blocks_execution(self):
        for attribute in ('contract', 'summary', 'details'):
            batch, fingerprint = self.approved()
            path = getattr(self, attribute)
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.assertRaisesRegex(VerificationError, 'input_changed'):
                self.execute(batch, fingerprint)
            path.write_bytes(original)
        batch, fingerprint = self.approved()
        self.fixture.add(uid=2)
        with self.assertRaisesRegex(VerificationError, 'input_changed'):
            self.execute(batch, fingerprint)

    def test_changed_snapshot_before_prepare_rejected(self):
        self.fixture.add(uid=2)
        with self.assertRaisesRegex(VerificationError, 'snapshot_mismatch'):
            self.prepare()

    def test_hold_is_visible_but_never_approvable_or_modified(self):
        with Ledger(self.fixture.path) as ledger:
            ledger.db.execute("UPDATE punches SET state='rejected', error_category='cutover_hold'")
            ledger.db.commit()
        self.build_report()
        batch, fingerprint = self.prepare()
        self.assertEqual(self.status(batch), 'Skipped')
        with self.assertRaises(VerificationError):
            core.approve(self.store, batch, fingerprint, [1], 'CANNOT RELEASE HOLD')
        target = self.execute(batch, fingerprint)
        self.assertFalse(target.calls)

    def test_mapping_changed_to_ambiguous_or_inactive_before_send(self):
        for changes in ('duplicate', 'inactive'):
            batch, fingerprint = self.approved()
            employees = json.loads(json.dumps(self.fixture.employees))
            if changes == 'duplicate':
                employees.append(dict(employees[0], name='SYNTHETIC_ALIAS', attendance_device_id='100 '))
            else:
                employees[0]['status'] = 'Inactive'
            self.write_scenario(employees=employees)
            target = self.execute(batch, fingerprint)
            self.assertEqual(self.status(batch), 'Needs Review')
            self.assertNotIn('POST', target.calls)

    def test_fractional_timestamp_and_duplicate_source_not_candidates(self):
        self.fixture.add(timestamp=fixtures.STAMP.replace(microsecond=1), uid=2)
        self.fixture.add('TEST_B', uid=3)
        self.build_report()
        batch, _ = self.prepare()
        self.assertEqual(self.store.items(batch), [])

    def test_timeout_after_remote_commit_is_confirmed_without_repost(self):
        batch, fingerprint = self.approved()
        self.write_scenario(next_send='timeout_after')
        target = self.execute(batch, fingerprint)
        self.assertEqual(self.status(batch), 'Already Exists')
        self.assertEqual(target.calls.count('POST'), 1)

    def test_timeout_before_commit_never_blindly_retried_across_batches(self):
        batch, fingerprint = self.approved()
        self.write_scenario(next_send='timeout_before')
        target = self.execute(batch, fingerprint)
        self.assertEqual(self.status(batch), 'Needs Review')
        self.assertEqual(target.calls.count('POST'), 1)
        again = self.execute(batch, fingerprint)
        self.assertNotIn('POST', again.calls)
        new_batch, new_fingerprint = self.approved()
        another = self.execute(new_batch, new_fingerprint)
        self.assertNotIn('POST', another.calls)
        self.assertEqual(self.status(new_batch), 'Needs Review')

    def test_crash_after_remote_commit_reconciles_after_reopening_audit(self):
        batch, fingerprint = self.approved()
        self.write_scenario(next_send='crash_after')
        with self.assertRaises(KeyboardInterrupt):
            self.execute(batch, fingerprint)
        self.assertEqual(self.status(batch), 'Needs Review')
        reopened = RecoveryStore(self.workspace, acl_check=allow_acl)
        try:
            target = SimulationTarget(self.scenario)
            core.execute(reopened, batch, fingerprint, target, self.normal_path)
            self.assertNotIn('POST', target.calls)
            self.assertEqual(reopened.items(batch)[0]['status'], 'Already Exists')
        finally:
            reopened.close()

    def test_crash_after_intent_before_request_burns_submission_permission(self):
        batch, fingerprint = self.approved()
        with mock.patch('historical_attendance.recovery.FrappeClient.send', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.execute(batch, fingerprint)
        again = self.execute(batch, fingerprint)
        self.assertNotIn('POST', again.calls)
        self.assertEqual(self.status(batch), 'Needs Review')

    def test_partial_failure_does_not_reset_or_lose_other_items(self):
        self.fixture.add(uid=2, timestamp=fixtures.STAMP + dt.timedelta(seconds=1))
        self.build_report()
        batch, fingerprint = self.prepare()
        core.approve(self.store, batch, fingerprint, [1, 2], 'TWO EXPLICIT RECORDS')
        self.write_scenario(next_send='reject')
        self.execute(batch, fingerprint)
        self.assertEqual([r['status'] for r in self.store.items(batch)], ['Failed', 'Recovered'])

    def test_get_timeout_and_incomplete_pagination_prevent_submission(self):
        batch, fingerprint = self.approved()
        target = SimulationTarget(self.scenario)
        target.request = mock.Mock(side_effect=TimeoutError('TEST_SECRET'))
        self.execute(batch, fingerprint, target)
        self.assertEqual(self.status(batch), 'Needs Review')
        self.assertTrue(all(call.args[0] == 'GET' for call in target.request.call_args_list))
        from historical_attendance.recovery_simulation import response
        batch, fingerprint = self.approved()
        employees = [dict(self.fixture.employees[0]), dict(self.fixture.employees[0], name='SYNTHETIC_ALIAS')]
        self.write_scenario(employees=employees)
        target = SimulationTarget(self.scenario)
        original = target.request
        def truncated(method, url, **options):
            result = original(method, url, **options)
            if url.endswith('/Employee'):
                return response(200, {'data': result.json()['data'][:1] if options['params']['limit_start'] == 0 else []})
            return result
        target.request = truncated
        self.execute(batch, fingerprint, target)
        self.assertEqual(self.status(batch), 'Needs Review')
        self.assertNotIn('POST', target.calls)

    def test_normal_service_lock_conflict_is_rejected_without_requests(self):
        batch, fingerprint = self.approved()
        context = multiprocessing.get_context('spawn')
        ready, release = context.Event(), context.Event()
        process = context.Process(target=normal_worker, args=(self.normal_path, ready, release))
        process.start()
        try:
            self.assertTrue(ready.wait(5))
            target = SimulationTarget(self.scenario)
            with self.assertRaises(Exception):
                self.execute(batch, fingerprint, target)
            self.assertFalse(target.calls)
            self.assertEqual(self.status(batch), 'Approved')
        finally:
            release.set()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()

    def test_two_recovery_writers_are_excluded(self):
        batch, fingerprint = self.approved()
        other = RecoveryStore(self.workspace, acl_check=allow_acl)
        try:
            with self.store.exclusive():
                with self.assertRaises(VerificationError):
                    core.approve(other, batch, fingerprint, [1], 'CONCURRENT')
        finally:
            other.close()

    def test_production_and_arbitrary_live_transports_are_disabled(self):
        batch, fingerprint = self.approved()
        with self.assertRaisesRegex(VerificationError, 'production_execution_disabled'):
            self.execute(batch, fingerprint, mock.Mock())
        self.fixture.policy['destination_environment'] = 'production'
        self.build_report()
        batch, fingerprint = self.approved()
        with self.assertRaisesRegex(VerificationError, 'production_execution_disabled'):
            self.execute(batch, fingerprint)

    def test_approval_rejects_wrong_fingerprint_missing_ids_or_no_reference(self):
        batch, fingerprint = self.prepare()
        for fp, ids, reference in (('wrong', [1], 'REF'), (fingerprint, [999], 'REF'), (fingerprint, [1], '')):
            with self.assertRaises(VerificationError):
                core.approve(self.store, batch, fp, ids, reference)

    def test_result_export_hash_and_private_source_preservation(self):
        batch, fingerprint = self.approved()
        self.execute(batch, fingerprint)
        output = self.root / 'results'
        output.mkdir()
        marker = core.export_results(self.store, batch, output)
        report = json.loads((output / marker['file']).read_text())
        self.assertEqual(core.file_hash(output / report['reports'][0]['file']), report['reports'][0]['sha256'])
        self.assertEqual(report['counts'], {'Recovered': 1})
        self.assertFalse(report['production_execution_supported'])

    def test_cli_prepare_approve_simulate_show_and_export(self):
        # Defaults are bound at definition time: patch guards at the CLI/store seam.
        with mock.patch.object(cli, 'preflight_output', side_effect=lambda p: Path(p)):
            with mock.patch.object(cli, 'RecoveryStore', side_effect=lambda p: RecoveryStore(p, acl_check=allow_acl)):
                stream = io.StringIO()
                base = ['--workspace', str(self.workspace)]
                with contextlib.redirect_stdout(stream):
                    self.assertEqual(cli.main(base + ['prepare', '--ledger-copy', str(self.fixture.path),
                        '--summary', str(self.summary), '--summary-sha256', core.file_hash(self.summary),
                        '--contract', str(self.contract), '--contract-sha256', core.file_hash(self.contract),
                        '--receipt-review', 'REVIEWED']), 0)
                data = json.loads(stream.getvalue())
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(base + ['approve', '--batch', data['batch'], '--fingerprint', data['fingerprint'],
                        '--record-id', '1', '--approval-reference', 'APPROVED']), 0)
                    self.assertEqual(cli.main(base + ['execute-simulation', '--batch', data['batch'], '--fingerprint', data['fingerprint'],
                        '--simulation', str(self.scenario), '--normal-worker-path', str(self.normal_path)]), 0)
                    self.assertEqual(cli.main(base + ['show', '--batch', data['batch']]), 0)
                    output = self.root / 'cli-results'
                    output.mkdir()
                    self.assertEqual(cli.main(base + ['export', '--batch', data['batch'], '--output', str(output)]), 0)

    def test_changed_audit_snapshot_cannot_bypass_approved_fingerprint(self):
        batch, fingerprint = self.approved()
        with self.store.db:
            self.store.db.execute("UPDATE items SET employee='CHANGED' WHERE batch=?", (batch,))
        with self.assertRaisesRegex(VerificationError, 'batch_contents_changed'):
            self.execute(batch, fingerprint)

    def test_hot_backup_sidecar_is_rejected_without_opening_source(self):
        sidecar = Path(str(self.fixture.path) + '-wal')
        sidecar.write_bytes(b'SYNTHETIC UNAPPROVED SIDECAR')
        before = self.fixture.path.read_bytes()
        with self.assertRaisesRegex(VerificationError, 'self_contained'):
            self.prepare()
        self.assertEqual(self.fixture.path.read_bytes(), before)
        self.assertEqual(sidecar.read_bytes(), b'SYNTHETIC UNAPPROVED SIDECAR')

    def test_phase1_uncertain_peer_blocks_an_otherwise_missing_source(self):
        self.fixture.add('UNSELECTED_DEVICE', uid=2)
        with Ledger(self.fixture.path) as ledger:
            ledger.db.execute("UPDATE punches SET state='retry', error_category='uncertain_submission' WHERE device_id='UNSELECTED_DEVICE'")
            ledger.db.commit()
        self.build_report()
        batch, fingerprint = self.approved()
        target = self.execute(batch, fingerprint)
        self.assertFalse(target.calls)
        self.assertEqual(self.status(batch), 'Needs Review')

    def test_interrupted_before_next_item_leaves_approval_intact(self):
        batch, fingerprint = self.approved()
        target = self.execute(batch, fingerprint, stop_requested=lambda: True)
        self.assertFalse(target.calls)
        self.assertEqual(self.status(batch), 'Approved')

    def test_only_explicitly_selected_record_is_submitted(self):
        self.fixture.add(uid=2, timestamp=fixtures.STAMP + dt.timedelta(seconds=1))
        self.build_report()
        batch, fingerprint = self.approved()
        target = self.execute(batch, fingerprint)
        self.assertEqual(target.calls.count('POST'), 1)
        self.assertEqual([r['status'] for r in self.store.items(batch)], ['Recovered', 'Pending Approval'])

    def test_audit_schema_corruption_is_rejected_without_reinitialization(self):
        with self.store.db:
            self.store.db.execute('DROP TABLE events')
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(VerificationError, 'schema_unverified'):
            RecoveryStore(self.workspace, acl_check=allow_acl)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_unprotected_workspace_is_rejected_before_creating_database(self):
        from scripts.capture_history_safety import CaptureError
        workspace = self.root / 'unprotected'
        workspace.mkdir()
        with self.assertRaises(CaptureError):
            RecoveryStore(workspace, acl_check=mock.Mock(side_effect=CaptureError('private_acl_required')))
        self.assertEqual(list(workspace.iterdir()), [])

    def test_input_alias_cannot_be_used_as_writable_simulation(self):
        batch, fingerprint = self.approved()
        target = SimulationTarget(self.scenario)
        target.path = self.contract  # Test the accidental alias guard before any request.
        before = self.contract.read_bytes()
        with self.assertRaisesRegex(VerificationError, 'pinned_inputs'):
            self.execute(batch, fingerprint, target)
        self.assertEqual(self.contract.read_bytes(), before)
        self.assertFalse(target.calls)

    def test_documented_synthetic_input_generator_produces_preparable_report(self):
        from generate_recovery_example import generate
        inputs = self.root / 'generated-inputs'
        with contextlib.redirect_stdout(io.StringIO()):
            generate(inputs)
        batch, fingerprint = core.prepare(self.store, inputs / 'synthetic-ledger.sqlite3',
            inputs / 'synthetic-summary.json', core.file_hash(inputs / 'synthetic-summary.json'),
            inputs / 'synthetic-contract.json', core.file_hash(inputs / 'synthetic-contract.json'),
            receipt_review='SYNTHETIC GENERATOR REVIEW')
        core.approve(self.store, batch, fingerprint, [1], 'GENERATOR REVIEW APPROVAL')
        target = SimulationTarget(inputs / 'synthetic-destination.json')
        core.execute(self.store, batch, fingerprint, target, self.normal_path)
        self.assertEqual(self.status(batch), 'Recovered')


if __name__ == '__main__':
    unittest.main()
