"""Synthetic-only recovery preview tests; no production connections or credentials."""
import ast
import contextlib
import csv
import datetime as dt
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconciliation_fixtures import bundle, dump, sha, punch, START, END
from scripts import attendance_reconciliation as reconciliation
from scripts import attendance_recovery_preview as core
from scripts import preview_attendance_recovery as cli


def synthetic_review(root, **kwargs):
    """Create synthetic attestations ONLY. This is never an attestation generator for operators."""
    cap, exp = bundle(root, **kwargs)
    contract = json.loads(exp.read_text())
    contract.update(duplicate_key='employee_time_log_type', timestamp_precision='seconds')
    for policy in contract['device_policies'].values():
        policy['punch_direction'] = None
    dump(exp, contract)
    captures, _, _ = reconciliation.load_captures(cap, sha(cap))
    _, mappings, checkins, _, _ = reconciliation.load_exports(exp, sha(exp), START, END, 'Asia/Riyadh')
    # The historical baseline is prepared before the current destination comparison.
    rows, _ = reconciliation.reconcile(captures, contract, mappings, [], START, END, 'Asia/Riyadh')
    baseline = root / 'synthetic-baseline.csv'
    with baseline.open('w', encoding='utf-8', newline='') as stream:
        reconciliation.write_details(stream, rows)
    storage = root / 'synthetic-log-type-storage.json'
    dump(storage, dict(schema='employee-checkin-log-type-storage-v1', snapshot_id='SYNTHETIC-SNAPSHOT',
                      states=[dict(name=c['name'], state='nonblank' if c['log_type'] else 'empty') for c in checkins]))
    exported = dt.datetime.fromisoformat(contract['exported_at'])
    # Snapshot begins AFTER the fake captures finish, and completes at export time.
    now = exported + dt.timedelta(seconds=10)
    review = root / 'synthetic-review.json'
    dump(review, dict(schema='attendance-recovery-review-v1',
        capture_summary_sha256=sha(cap), exports_manifest_sha256=sha(exp),
        range_start=str(START), range_end=str(END), site_timezone='Asia/Riyadh',
        baseline_details=dict(file=baseline.name, sha256=sha(baseline)),
        approved_source_records=[{k: r[k] for k in ('device_id', 'source_sha256', 'source_order_index')}
                                 for r in rows if r['classification'] == 'missing'],
        log_type_storage=dict(file=storage.name, sha256=sha(storage)), verified_at=now.isoformat(),
        verification={claim: dict(verified=True, evidence_reference='SYNTHETIC ONLY - no deployed attestation')
                      for claim in core.CLAIMS},
        snapshot=dict(id='SYNTHETIC-SNAPSHOT', started_at=exported.isoformat(), completed_at=exported.isoformat(),
                      independent_counts=dict(checkins=len(checkins), lookup_keys=len(mappings),
                                              mapping_matches=sum(len(m) for m in mappings.values())))))
    return cap, exp, review, now


class RecoveryPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.cap, self.exp, self.review, self.now = synthetic_review(self.inputs, checkins=[])
        self.output = self.root / 'reports'
        self.output.mkdir()

    def change(self, path, **changes):
        data = json.loads(path.read_text())
        data.update(changes)
        dump(path, data)

    def compare(self, **kwargs):
        return core.build_preview(self.cap, sha(self.cap), self.exp, sha(self.exp),
                                  self.review, sha(self.review), now=kwargs.get('now', self.now))

    def rebuild(self, **kwargs):
        self.inputs = self.root / ('inputs-' + str(len(list(self.root.iterdir()))))
        self.cap, self.exp, self.review, self.now = synthetic_review(self.inputs, **kwargs)

    def args(self):
        return ['--capture-summary', str(self.cap), '--capture-summary-sha256', sha(self.cap),
                '--exports-manifest', str(self.exp), '--exports-manifest-sha256', sha(self.exp),
                '--review-manifest', str(self.review), '--review-manifest-sha256', sha(self.review),
                '--output', str(self.output)]

    def run_cli(self, args=None, acl=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = cli.main(args or self.args(), acl_check=acl or (lambda _: None), now=self.now)
        return result, json.loads(out.getvalue())

    def test_attested_preview_never_authorizes_and_has_stable_fingerprint(self):
        rows, summary, _ = self.compare()
        self.assertEqual(summary['counts'], {'eligible_for_review': 2})
        self.assertEqual(summary['per_day']['2026-10-14'], {'eligible_for_review': 1})
        self.assertFalse(summary['execution_supported'])
        self.assertFalse(summary['recovery_authorized'])
        self.assertTrue(summary['approval_required'])
        self.assertEqual(summary['review_fingerprint'], self.compare()[1]['review_fingerprint'])
        self.assertTrue(all(r['expected_log_type'] == '' for r in rows))

    def test_every_required_claim_missing_false_or_without_evidence_blocks(self):
        original = json.loads(self.review.read_text())
        for claim in core.CLAIMS:
            for entry in ({}, {'verified': False, 'evidence_reference': 'synthetic'}, {'verified': True}):
                with self.subTest(claim=claim, entry=entry):
                    data = json.loads(json.dumps(original))
                    data['verification'][claim] = entry
                    dump(self.review, data)
                    _, summary, _ = self.compare()
                    self.assertNotIn('eligible_for_review', summary['counts'])
                    self.assertIn('unverified_' + claim, summary['blockers'])

    def test_stale_and_future_snapshots_block(self):
        for now in (self.now + dt.timedelta(minutes=16), self.now - dt.timedelta(seconds=20)):
            with self.subTest(now=now):
                self.assertTrue(self.compare(now=now)[1]['blockers'])
                self.assertNotIn('eligible_for_review', self.compare(now=now)[1]['counts'])

    def test_independent_count_snapshot_and_timestamp_failures_block(self):
        original = json.loads(self.review.read_text())
        for field, value in [('id', ''), ('id', 'OTHER-SNAPSHOT'), ('started_at', 'invalid'),
                             ('independent_counts', {'checkins': 999, 'lookup_keys': 2})]:
            with self.subTest(field=field):
                data = json.loads(json.dumps(original))
                data['snapshot'][field] = value
                dump(self.review, data)
                self.assertTrue(self.compare()[1]['blockers'])

    def test_existing_export_attestation_checks_are_not_relaxed(self):
        for field in ('exports_complete', 'consistent_snapshot', 'server_semantics_verified',
                      'checkins_all_devices', 'checkins_all_employees', 'mapping_all_statuses',
                      'no_identity_mutating_hooks'):
            contract = json.loads(self.exp.read_text())
            contract[field] = False
            dump(self.exp, contract)
            self.change(self.review, exports_manifest_sha256=sha(self.exp))
            with self.subTest(field=field), self.assertRaises(reconciliation.ReconciliationError):
                self.compare()
            contract[field] = True
            dump(self.exp, contract)

    def test_cross_device_and_blank_device_matches_are_already_matched(self):
        for device in ('UNRELATED', ''):
            self.rebuild(checkins=[dict(name='SYNTH-C', employee='SYNTH-EMP-001', time='2026-10-14 08:00:00',
                                      device_id=device, log_type='')])
            rows, summary, _ = self.compare()
            self.assertEqual(rows[0]['review_status'], 'already_matched')
            self.assertEqual(summary['scoped_counts']['already_matched'], 1)

    def test_sql_null_unknown_and_empty_are_distinct(self):
        self.rebuild(checkins=[dict(name='SYNTH-C', employee='SYNTH-EMP-001', time='2026-10-14 08:00:00',
                                  device_id='', log_type='')])
        storage = self.inputs / 'synthetic-log-type-storage.json'
        for state, expected in [('null', 'ambiguous'), ('unknown', 'ambiguous'), ('empty', 'already_matched')]:
            self.change(storage, states=[dict(name='SYNTH-C', state=state)])
            self.change(self.review, log_type_storage=dict(file=storage.name, sha256=sha(storage)))
            self.assertEqual(self.compare()[0][0]['review_status'], expected)

    def test_null_sidecar_missing_duplicate_and_inconsistent_rows_fail(self):
        self.rebuild(checkins=[dict(name='SYNTH-C', employee='SYNTH-EMP-001', time='2026-10-14 08:00:00',
                                  device_id='', log_type='')])
        storage = self.inputs / 'synthetic-log-type-storage.json'
        for states in ([], [dict(name='SYNTH-C', state='nonblank')],
                       [dict(name='SYNTH-C', state='empty')] * 2):
            self.change(storage, states=states)
            self.change(self.review, log_type_storage=dict(file=storage.name, sha256=sha(storage)))
            with self.assertRaises(reconciliation.ReconciliationError):
                self.compare()

    def test_duplicate_source_and_unmatched_are_excluded_even_with_shared_uid(self):
        self.rebuild(records=[[punch(), punch()], [punch('002'), punch('003')]], lookups=[
            dict(user_id='001', matches=[dict(employee='SYNTH-EMP-001', attendance_device_id='001', status='Active')]),
            dict(user_id='002', matches=[dict(employee='SYNTH-EMP-002', attendance_device_id='002', status='Active')]),
            dict(user_id='003', matches=[])], checkins=[])
        rows, summary, _ = self.compare()
        self.assertEqual([r['review_status'] for r in rows], ['excluded', 'excluded', 'eligible_for_review', 'excluded'])
        self.assertEqual(summary['comparison']['totals']['duplicate_source'], 2)
        self.assertEqual(summary['comparison']['totals']['unmatched_employee'], 1)

    def test_duplicate_identity_across_devices_excludes_all_members(self):
        self.rebuild(records=[[punch(), punch('002')], [punch()]], checkins=[])
        self.assertEqual(self.compare()[1]['comparison']['totals']['duplicate_source'], 2)

    def test_inactive_and_multiple_mappings_remain_ambiguous(self):
        for matches in ([dict(employee='SYNTH-EMP-001', attendance_device_id='001', status='Inactive')],
                        [dict(employee='SYNTH-A', attendance_device_id='001', status='Active'),
                         dict(employee='SYNTH-B', attendance_device_id='001', status='Active')]):
            self.rebuild(lookups=[dict(user_id='001', matches=matches), dict(user_id='002', matches=[
                dict(employee='SYNTH-EMP-002', attendance_device_id='002', status='Active')])], checkins=[])
            self.assertEqual(self.compare()[0][0]['review_status'], 'ambiguous')

    def test_fractional_source_is_not_silently_truncated_into_eligibility(self):
        self.rebuild(records=[[punch(stamp='2026-10-14 08:00:00.123456')], [punch('002')]], checkins=[])
        self.assertEqual(self.compare()[0][0]['review_reason'], 'fractional_source_timestamp_requires_separate_review')

    def test_fractional_destination_and_truncation_collision_remain_ambiguous(self):
        self.rebuild(checkins=[dict(name='SYNTH-C', employee='SYNTH-EMP-001',
            time='2026-10-14 08:00:00.000001', device_id='', log_type='')])
        self.assertEqual(self.compare()[0][0]['review_status'], 'ambiguous')
        # A separate eligible employee keeps a nonempty review scope in this fixture.
        self.rebuild(records=[[punch(stamp='2026-10-14 08:00:00.1'),
                               punch(stamp='2026-10-14 08:00:00.2')], [punch('002')]], checkins=[])
        self.assertEqual(self.compare()[1]['counts'], {'ambiguous': 2, 'eligible_for_review': 1})

    def test_pinned_review_change_changes_fingerprint(self):
        before = self.compare()[1]['review_fingerprint']
        self.change(self.review, verification={})
        self.assertNotEqual(before, self.compare()[1]['review_fingerprint'])

    def test_no_connections_service_queries_or_subprocesses_in_planning(self):
        with mock.patch('socket.create_connection', side_effect=AssertionError('network forbidden')) as network, \
             mock.patch('subprocess.run', side_effect=AssertionError('subprocess forbidden')) as processes, \
             mock.patch('scripts.capture_history_safety.service_state', side_effect=AssertionError('service forbidden')) as service:
            self.assertEqual(self.run_cli()[0], 0)
            network.assert_not_called()
            processes.assert_not_called()
            service.assert_not_called()

    def test_leading_zero_employee_lookup_and_csv_formula_protection(self):
        self.rebuild(lookups=[dict(user_id='001', matches=[
            dict(employee='=SYNTHETIC()', attendance_device_id='001', status='Active')]),
            dict(user_id='002', matches=[dict(employee='OTHER', attendance_device_id='002', status='Active')])], checkins=[])
        rows, _, _ = self.compare()
        self.assertEqual(rows[0]['user_id'], '001')
        self.assertEqual(rows[0]['review_status'], 'eligible_for_review')
        stream = io.StringIO()
        core.write_preview(stream, rows)
        self.assertIn("'=SYNTHETIC()", stream.getvalue())

    def test_incomplete_pagination_row_count_and_path_escape_fail(self):
        original = json.loads(self.exp.read_text())
        data = json.loads(json.dumps(original))
        data['employee_checkins']['rows'] += 1
        dump(self.exp, data)
        self.change(self.review, exports_manifest_sha256=sha(self.exp))
        with self.assertRaises(reconciliation.ReconciliationError):
            self.compare()
        dump(self.exp, original)
        self.change(self.review, exports_manifest_sha256=sha(self.exp),
                    log_type_storage=dict(file='../private.json', sha256='0' * 64))
        with self.assertRaises(reconciliation.ReconciliationError):
            self.compare()

    def test_different_log_type_and_multiple_destination_matches_are_ambiguous(self):
        for types in (['IN'], ['', '']):
            self.rebuild(checkins=[dict(name='SYNTH-C-' + str(i), employee='SYNTH-EMP-001',
                time='2026-10-14 08:00:00', device_id='OTHER', log_type=t) for i, t in enumerate(types)])
            self.assertEqual(self.compare()[0][0]['review_status'], 'ambiguous')

    def test_unapproved_missing_records_are_not_eligible(self):
        review = json.loads(self.review.read_text())
        self.change(self.review, approved_source_records=review['approved_source_records'][:1])
        self.assertEqual(self.compare()[1]['counts'], {'eligible_for_review': 1, 'excluded': 1})

    def test_changed_inputs_fail_hash_or_binding_validation(self):
        old_args = self.args()
        self.exp.write_text(self.exp.read_text() + ' ')
        self.assertEqual(self.run_cli(old_args)[0], 1)
        with self.assertRaises(reconciliation.ReconciliationError):
            self.compare()

    def test_scope_must_be_unique_baseline_missing_and_capture_bound(self):
        review = json.loads(self.review.read_text())
        for scope in ([], review['approved_source_records'] * 2,
                      [dict(review['approved_source_records'][0], source_order_index=999)]):
            self.change(self.review, approved_source_records=scope)
            with self.assertRaises(reconciliation.ReconciliationError):
                self.compare()

    def test_baseline_cannot_approve_an_already_matched_source(self):
        baseline = self.inputs / 'synthetic-baseline.csv'
        baseline.write_text(baseline.read_text().replace(',missing,', ',matched,'))
        self.change(self.review, baseline_details=dict(file=baseline.name, sha256=sha(baseline)))
        with self.assertRaises(reconciliation.ReconciliationError):
            self.compare()

    def test_reports_hashes_private_console_and_input_preservation(self):
        before = {p: p.read_bytes() for p in self.inputs.iterdir()}
        result, console = self.run_cli()
        self.assertEqual(result, 0)
        for secret in ('SYNTH-EMP', 'SYNTH-C', str(self.inputs)):
            self.assertNotIn(secret, json.dumps(console))
        summary = json.loads((self.output / console['report']['file']).read_text())
        for spec in summary['reports'] + [console['report']]:
            self.assertEqual(sha(self.output / spec['file']), spec['sha256'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.inputs.iterdir()})
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(len(list(self.output.iterdir())), 4)

    def test_blocked_preview_reports_zero_eligible_and_exit_two(self):
        self.change(self.review, verification={})
        result, console = self.run_cli()
        self.assertEqual(result, 2)
        self.assertEqual(console['counts'], {'blocked': 2})

    def test_private_output_failure_and_exception_text_do_not_leak(self):
        def rejected(_):
            raise RuntimeError('PASSWORD=synthetic-secret')
        result, console = self.run_cli(acl=rejected)
        self.assertEqual(result, 1)
        self.assertNotIn('PASSWORD', json.dumps(console))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_output_inside_evidence_is_rejected(self):
        args = self.args()
        args[-1] = str(self.inputs)
        self.assertEqual(self.run_cli(args)[0], 1)

    def test_execution_options_are_rejected_before_inputs_are_read(self):
        for flag in ('--execute', '--submit', '--approve', '--endpoint', '--token'):
            with mock.patch.object(cli, 'build_preview') as build:
                self.assertEqual(self.run_cli(self.args() + [flag])[0], 1)
                build.assert_not_called()

    def test_new_modules_have_no_transport_service_or_production_imports(self):
        allowed = {'collections', 'csv', 'datetime', 'hashlib', 'io', 'json', 'pathlib', 'sys', 'uuid',
                   'scripts.attendance_reconciliation', 'scripts.attendance_recovery_preview',
                   'scripts.capture_history_safety', 'scripts.reconcile_attendance'}
        for module in (core, cli):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(a.name in allowed for a in node.names))
                elif isinstance(node, ast.ImportFrom):
                    self.assertIn(node.module, allowed)

    def test_entire_local_import_chain_is_offline_and_has_no_dynamic_imports(self):
        # Shared helpers matter too: checking just the entry point misses indirect clients.
        root = Path(__file__).resolve().parents[1]
        local_modules = {
            'scripts.preview_attendance_recovery', 'scripts.attendance_recovery_preview',
            'scripts.attendance_reconciliation', 'scripts.capture_history_safety',
            'scripts.reconcile_attendance',
        }
        standard_modules = {
            'argparse', 'collections', 'csv', 'ctypes', 'datetime', 'hashlib', 'io', 'json',
            'os', 'pathlib', 're', 'shutil', 'subprocess', 'sys', 'tempfile', 'uuid', 'zoneinfo',
        }
        for name in local_modules:
            tree = ast.parse((root / (name.replace('.', '/') + '.py')).read_text())
            for node in ast.walk(tree):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0)
                    imports = [node.module]
                for imported in imports:
                    self.assertIn(imported, standard_modules | local_modules)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ('eval', 'exec', '__import__'))

    def test_published_synthetic_examples_replay_and_detail_hashes_match(self):
        root = Path(__file__).resolve().parents[1] / 'examples' / 'historical-recovery-preview'
        inputs = root / 'inputs'
        for report_name, review_name in (
                ('synthetic-preview-summary.json', 'synthetic-review.json'),
                ('synthetic-blocked-summary.json', 'synthetic-unverified-review.json')):
            report = json.loads((root / report_name).read_text())
            _, actual, _ = core.build_preview(inputs / 'synthetic-capture-manifest.json',
                sha(inputs / 'synthetic-capture-manifest.json'), inputs / 'synthetic-export-manifest.json',
                sha(inputs / 'synthetic-export-manifest.json'), inputs / review_name, sha(inputs / review_name),
                now=dt.datetime.fromisoformat(report['created_at']))
            self.assertEqual(actual['review_fingerprint'], report['review_fingerprint'])
            self.assertEqual(actual['counts'], report['counts'])
            for spec in report['reports']:
                self.assertEqual(sha(root / spec['file']), spec['sha256'])


if __name__ == '__main__':
    unittest.main()
