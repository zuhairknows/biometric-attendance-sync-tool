"""Offline-only tests. All attendance, mappings and Checkins are synthetic."""
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconciliation_fixtures import bundle, dump, sha, punch, START, END
from scripts import attendance_reconciliation as core
from scripts import reconcile_attendance as cli


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / 'inputs'
        self.output = self.root / 'reports'
        self.output.mkdir()
        self.cap, self.exp = bundle(self.inputs)

    def rebuild(self, **kwargs):
        # Each rebuild uses its own folder; fixture generation never overwrites evidence.
        self.inputs = self.root / ('inputs-' + str(len(list(self.root.iterdir()))))
        self.cap, self.exp = bundle(self.inputs, **kwargs)

    def mutate(self, path, update):
        value = json.loads(path.read_text(encoding='utf-8'))
        update(value)
        dump(path, value)

    def contract(self, **kwargs):
        self.mutate(self.exp, lambda value: value.update(kwargs))

    def load(self):
        captures, _, _ = core.load_captures(self.cap, sha(self.cap))
        contract, lookups, checkins, _, _ = core.load_exports(self.exp, sha(self.exp), START, END, 'Asia/Riyadh')
        return captures, contract, lookups, checkins

    def compare(self):
        captures, contract, lookups, checkins = self.load()
        return core.reconcile(captures, contract, lookups, checkins, START, END, 'Asia/Riyadh')

    def argv(self):
        return ['--capture-summary', str(self.cap), '--capture-summary-sha256', sha(self.cap),
                '--exports-manifest', str(self.exp), '--exports-manifest-sha256', sha(self.exp), '--output', str(self.output)]

    def run_cli(self, args=None):
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            result = cli.main(args or self.argv(), acl_check=lambda _: None)
        return result, console.getvalue()

    def test_actual_capture_writer_schema_and_cross_device_match(self):
        rows, summary = self.compare()
        self.assertEqual([r['classification'] for r in rows], ['matched', 'missing'])
        self.assertEqual(rows[0]['checkin_device_ids'], 'OTHER_DEVICE')
        self.assertEqual(summary['totals']['records'], 2)
        self.assertEqual(summary['per_day']['2026-10-14']['matched'], 1)
        self.assertEqual(summary['per_device']['FP2_DEVICE_11']['missing'], 1)

    def test_console_is_pii_free_and_input_bytes_are_unchanged(self):
        before = {p: p.read_bytes() for p in self.inputs.iterdir()}
        result, console = self.run_cli()
        self.assertEqual(result, 0, console)
        for private in ('SYNTH-EMP', 'SYNTH-CHECKIN', 'OTHER_DEVICE', str(self.inputs)):
            self.assertNotIn(private, console)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        report = json.loads(console)
        self.assertFalse(report['recovery_authorized'])
        self.assertTrue(report['all_missing_require_review'])
        for spec in report['reports'] + [report['summary_report']]:
            self.assertEqual(sha(self.output / spec['file']), spec['sha256'])
        with (self.output / report['reports'][0]['file']).open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]['employee'], 'SYNTH-EMP-001')

    def test_repeated_runs_have_independent_reports(self):
        self.assertEqual(self.run_cli()[0], 0)
        before = {p: p.read_bytes() for p in self.output.iterdir()}
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(len(list(self.output.iterdir())), 4)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_hash_failure_happens_before_any_comparison_or_output(self):
        args = self.argv()
        args[3] = '0' * 64
        with mock.patch.object(cli, 'reconcile') as compare:
            result, console = self.run_cli(args)
        self.assertEqual(result, 1)
        self.assertIn('sha256_mismatch', console)
        compare.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_capture_tampering_fails_even_when_manifest_hash_is_current(self):
        report = json.loads(self.cap.read_text())['devices'][0]
        self.mutate(self.inputs / report['evidence_file'], lambda c: c['records'].clear())
        self.assertEqual(self.run_cli()[0], 1)

    def test_export_hash_tampering(self):
        (self.inputs / 'synthetic-checkins.csv').write_text('private token', encoding='utf-8')
        result, console = self.run_cli()
        self.assertEqual(result, 1)
        self.assertIn('sha256_mismatch', console)
        self.assertNotIn('private token', console)

    def test_inconsistent_capture_counts_rejected_with_rehashed_snapshot(self):
        manifest = json.loads(self.cap.read_text())
        spec = manifest['devices'][0]
        path = self.inputs / spec['evidence_file']
        self.mutate(path, lambda c: c['summary'].update(total_fetched=999))
        spec['sha256'] = sha(path)
        dump(self.cap, manifest)
        self.assertEqual(self.run_cli()[0], 1)

    def test_duplicate_or_wrong_device_manifest_rejected(self):
        self.mutate(self.cap, lambda m: m['devices'].__setitem__(1, m['devices'][0]))
        self.assertEqual(self.run_cli()[0], 1)

    def test_incomplete_capture_manifest_rejected(self):
        self.mutate(self.cap, lambda m: m.update(all_selected_devices_captured=False))
        self.assertEqual(self.run_cli()[0], 1)

    def test_manifest_path_traversal_rejected(self):
        self.mutate(self.cap, lambda m: m['devices'][0].update(evidence_file='../private.json'))
        self.assertEqual(self.run_cli()[0], 1)

    def test_source_order_inconsistent_rejected(self):
        manifest = json.loads(self.cap.read_text())
        spec = manifest['devices'][0]
        path = self.inputs / spec['evidence_file']
        self.mutate(path, lambda c: c['records'][0].update(source_order_index=2))
        spec['sha256'] = sha(path)
        dump(self.cap, manifest)
        self.assertEqual(self.run_cli()[0], 1)

    def test_json_duplicate_keys_and_nonfinite_values_rejected(self):
        for contents in ('{"mode": "capture", "mode": "capture"}', '{"a": NaN}'):
            with self.subTest(contents=contents):
                path = self.root / 'bad.json'
                path.write_text(contents)
                with self.assertRaises(core.ReconciliationError):
                    core.verified_json(path, sha(path))

    def test_unverified_or_incomplete_export_attestations_rejected(self):
        original = self.exp.read_bytes()
        for field in ('exports_complete', 'consistent_snapshot', 'checkins_all_devices', 'checkins_all_employees',
                      'mapping_all_statuses', 'server_semantics_verified', 'no_identity_mutating_hooks'):
            with self.subTest(field=field):
                self.exp.write_bytes(original)
                self.contract(**{field: False})
                self.assertEqual(self.run_cli()[0], 1)

    def test_date_coverage_timezone_and_semantics_rejected(self):
        original = self.exp.read_bytes()
        for change in (dict(coverage_start='2026-10-12 00:00:00'), dict(site_timezone='UTC'),
                       dict(duplicate_key='uid'), dict(timestamp_precision='guess'),
                       dict(employee_field='name'), dict(mapping_resolution='first_match'),
                       dict(server_source_reference='')):
            with self.subTest(change=change):
                self.exp.write_bytes(original)
                self.contract(**change)
                self.assertEqual(self.run_cli()[0], 1)

    def test_exports_older_than_capture_rejected(self):
        self.contract(exported_at='2026-10-10T00:00:00+03:00')
        self.assertEqual(self.run_cli()[0], 1)

    def test_export_row_count_mismatch(self):
        self.mutate(self.exp, lambda c: c['employee_checkins'].update(rows=900))
        self.assertEqual(self.run_cli()[0], 1)

    def test_unmatched_employee_is_explicit_empty_lookup_only(self):
        self.rebuild(lookups=[dict(user_id='001', matches=[])])
        rows, _ = self.compare()
        self.assertEqual(rows[0]['classification'], 'unmatched_employee')
        self.assertEqual(rows[1]['classification'], 'ambiguous')
        self.assertEqual(rows[1]['reason'], 'employee_lookup_not_exported')

    def test_ambiguous_mapping_never_selects_first(self):
        self.rebuild(lookups=[dict(user_id='001', matches=[
            dict(employee='A', attendance_device_id='001', status='Active'),
            dict(employee='B', attendance_device_id='001', status='Active')])])
        rows, _ = self.compare()
        self.assertEqual(rows[0]['classification'], 'ambiguous')
        self.assertEqual(rows[0]['employee'], '')

    def test_inactive_employee_is_ambiguous(self):
        self.rebuild(lookups=[dict(user_id='001', matches=[dict(employee='A', attendance_device_id='001', status='Inactive')])])
        self.assertEqual(self.compare()[0][0]['reason'], 'employee_not_active')

    def test_leading_zeros_preserved_and_uid_is_not_employee(self):
        self.rebuild(records=[[punch(uid=999), punch('1', uid=999)], []], lookups=[
            dict(user_id='001', matches=[dict(employee='A', attendance_device_id='001', status='Active')]),
            dict(user_id='1', matches=[dict(employee='B', attendance_device_id='1', status='Active')])], checkins=[])
        rows, _ = self.compare()
        self.assertEqual([r['employee'] for r in rows], ['A', 'B'])
        self.assertTrue(all(r['classification'] == 'missing' for r in rows))

    def test_authoritative_collation_results_used_without_local_guess(self):
        self.rebuild(lookups=[dict(user_id='001', matches=[dict(employee='A', attendance_device_id='001 ', status='Active')])])
        self.assertEqual(self.compare()[0][0]['employee'], 'A')

    def test_repeated_source_records_all_marked_without_first_winner(self):
        self.rebuild(records=[[punch(), punch()], []])
        rows, summary = self.compare()
        self.assertTrue(all(r['classification'] == 'duplicate_source' for r in rows))
        self.assertEqual(summary['totals']['matched'], 0)
        self.assertTrue(all(r['destination_match'] == 'matched' for r in rows))

    def test_cross_device_duplicate_with_different_uid_and_status(self):
        self.rebuild(records=[[punch()], [punch(uid=456, status=3)]], checkins=[])
        rows, summary = self.compare()
        self.assertEqual(summary['totals']['duplicate_source'], 2)
        self.assertTrue(all(r['destination_match'] == 'missing' and r['duplicate_group_size'] == 2 for r in rows))

    def test_different_uid_does_not_hide_duplicate_source(self):
        self.rebuild(records=[[punch(), punch(uid=456)], []], checkins=[])
        self.assertEqual(self.compare()[1]['totals']['duplicate_source'], 2)

    def test_duplicate_destination_checkins_ambiguous(self):
        checks = [dict(name=str(i), employee='SYNTH-EMP-001', time='2026-10-14 08:00:00',
                       device_id='ANY', log_type='IN') for i in range(2)]
        self.rebuild(checkins=checks)
        self.assertEqual(self.compare()[0][0]['reason'], 'multiple_destination_checkins')

    def test_employee_time_policy_ignores_checkin_device_and_direction(self):
        self.rebuild(checkins=[dict(name='C', employee='SYNTH-EMP-001', time='2026-10-14 08:00:00', device_id='', log_type='OUT')])
        self.assertEqual(self.compare()[0][0]['classification'], 'matched')

    def test_log_type_policy_matches_same_direction(self):
        self.contract(duplicate_key='employee_time_log_type')
        self.assertEqual(self.compare()[0][0]['classification'], 'matched')

    def test_log_type_policy_opposite_direction_never_missing(self):
        self.rebuild(checkins=[dict(name='C', employee='SYNTH-EMP-001', time='2026-10-14 08:00:00', device_id='ANY', log_type='OUT')])
        self.contract(duplicate_key='employee_time_log_type')
        self.assertEqual(self.compare()[0][0]['reason'], 'same_time_different_log_type')

    def test_direction_auto_precedence_matches_production(self):
        normalized = {'punch': 4}
        self.assertEqual(core.direction(normalized, dict(punch_direction='AUTO', in_codes=[4], out_codes=[4])), 'OUT')
        self.assertEqual(core.direction(normalized, dict(punch_direction='AUTO', in_codes=[], out_codes=[])), '')
        self.assertEqual(core.direction(normalized, dict(punch_direction=None)), '')

    def test_seconds_policy_truncates_only_when_verified(self):
        self.rebuild(records=[[punch(stamp='2026-10-14 08:00:00.123456')], []])
        self.assertEqual(self.compare()[0][0]['classification'], 'missing')
        self.contract(timestamp_precision='seconds')
        self.assertEqual(self.compare()[0][0]['classification'], 'matched')

    def test_precision_collision_is_ambiguous(self):
        self.rebuild(records=[[punch(stamp='2026-10-14 08:00:00.100000'), punch(stamp='2026-10-14 08:00:00.200000')], []])
        self.contract(timestamp_precision='seconds')
        self.assertEqual(self.compare()[1]['totals']['ambiguous'], 2)

    def test_bad_existing_timestamp_blocks_only_that_employee(self):
        self.rebuild(checkins=[dict(name='C', employee='SYNTH-EMP-001', time='PRIVATE-TIMESTAMP', device_id='ANY', log_type='IN')])
        rows, _ = self.compare()
        self.assertEqual(rows[0]['reason'], 'existing_checkin_timestamp_uncertain')
        self.assertEqual(rows[1]['classification'], 'missing')

    def test_existing_timezone_timestamp_not_silently_converted(self):
        self.rebuild(checkins=[dict(name='C', employee='SYNTH-EMP-001', time='2026-10-14T08:00:00+03:00', device_id='ANY', log_type='IN')])
        self.assertEqual(self.compare()[0][0]['classification'], 'ambiguous')

    def test_malformed_tail_does_not_stop_later_records(self):
        self.rebuild(records=[[punch(user='', stamp='2099-01-01 00:00:00'), punch(timestamp='bad'), punch()], []])
        rows, summary = self.compare()
        self.assertEqual([r['classification'] for r in rows], ['ambiguous', 'ambiguous', 'matched'])
        self.assertEqual(summary['per_day']['unknown']['ambiguous'], 1)

    def test_date_filter_is_inclusive_and_source_counts_remain_full(self):
        self.rebuild(records=[[punch(stamp=str(START)), punch(stamp=str(END)), punch(stamp='2026-10-10 23:59:59')], []], checkins=[])
        rows, summary = self.compare()
        self.assertEqual(len(rows), 2)
        self.assertEqual(summary['source_records'], 3)
        self.assertEqual(summary['excluded_valid_out_of_range'], 1)

    def test_invalid_normalized_timestamp_is_not_used_to_discard_evidence(self):
        self.rebuild(records=[[punch(user='', stamp='2099-01-01 00:00:00')], []])
        rows, _ = self.compare()
        self.assertEqual(rows[0]['range_status'], 'uncertain')
        self.assertEqual(rows[0]['classification'], 'ambiguous')

    def test_raw_numeric_timestamp_remains_ambiguous(self):
        self.rebuild(records=[[punch(timestamp=dt.datetime(2026, 10, 14, 8).timestamp())], []])
        self.assertEqual(self.compare()[0][0]['reason'], 'source_timestamp_representation_uncertain')

    def test_raw_normalized_disagreement_ambiguous(self):
        captures, contract, mappings, checkins = self.load()
        captures[0][0]['records'][0]['raw']['user_id'] = 'OTHER'
        rows, _ = core.reconcile(captures, contract, mappings, checkins, START, END, 'Asia/Riyadh')
        self.assertEqual(rows[0]['reason'], 'source_normalization_disagrees')

    def test_fold_and_dst_timestamp_ambiguity(self):
        captures, contract, mappings, checkins = self.load()
        captures[0][0]['records'][0]['raw']['timestamp']['fold'] = 1
        rows, _ = core.reconcile(captures, contract, mappings, checkins, START, END, 'Asia/Riyadh')
        self.assertEqual(rows[0]['reason'], 'source_timestamp_fold_uncertain')
        class FoldZone(dt.tzinfo):
            def utcoffset(self, value):
                return dt.timedelta(hours=value.fold)
        self.assertTrue(core.uncertain_wall_time(START, FoldZone()))

    def test_csv_formula_injection_is_escaped(self):
        for value in ('=HYPERLINK("bad")', '+cmd', '-cmd', '@cmd', '\tcmd', '  =cmd'):
            self.assertTrue(core.csv_cell(value).startswith("'"))
        self.assertEqual(core.csv_cell('001'), '001')

    def test_input_directory_cannot_receive_reports(self):
        self.output = self.inputs
        self.assertEqual(self.run_cli()[0], 1)

    def test_production_checkout_output_rejected(self):
        self.output = Path(__file__).resolve().parents[1]
        self.assertEqual(self.run_cli()[0], 1)

    def test_acl_failure_blocks_reports(self):
        with contextlib.redirect_stdout(io.StringIO()):
            result = cli.main(self.argv(), acl_check=mock.Mock(side_effect=OSError('PRIVATE')))
        self.assertEqual(result, 1)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_file_persistence_failures_never_report_success(self):
        with mock.patch.object(cli.os, 'fsync', side_effect=OSError('PRIVATE')):
            result, console = self.run_cli()
        self.assertEqual(result, 1)
        self.assertNotIn('PRIVATE', console)
        self.assertEqual(len(list(self.output.glob('*.part'))), 1)
        self.assertEqual(list(self.output.glob('*.json')), [])

    def test_summary_failure_leaves_csv_without_success_manifest(self):
        original = cli.publish
        def publish(directory, name, write):
            if name.endswith('.json'):
                raise OSError('PRIVATE')
            return original(directory, name, write)
        with mock.patch.object(cli, 'publish', side_effect=publish):
            self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(len(list(self.output.glob('*.csv'))), 1)
        self.assertEqual(list(self.output.glob('*.json')), [])

    def test_atomic_publication_never_overwrites(self):
        target = self.output / 'existing.csv'
        target.write_text('original')
        with self.assertRaises(OSError):
            cli.publish(self.output, target.name, lambda stream: stream.write('replacement'))
        self.assertEqual(target.read_text(), 'original')

    def test_bad_cli_arguments_do_not_echo_secrets(self):
        for args in (['--password', 'PRIVATE'], self.argv() + ['--start', 'PRIVATE'], self.argv() + ['--force']):
            result, console = self.run_cli(args)
            self.assertEqual(result, 1)
            self.assertNotIn('PRIVATE', console)

    def test_fresh_process_has_no_engine_sdk_http_or_sqlite_imports(self):
        script = '''
import sys
class Guard:
    def find_spec(self, name, *args):
        # pathlib on Python 3.11 needs urllib.parse, which performs no network I/O.
        if name in {'urllib.request','urllib.error'} or name.split('.')[0] in {'local_config','erpnext_sync','erpnext_sync_win','SMWinservice','durable_sync','durable_delivery','sync_ledger','requests','zk','sqlite3','socket','http'}:
            raise RuntimeError('forbidden import')
sys.meta_path.insert(0, Guard())
from scripts.reconcile_attendance import main
assert main(sys.argv[1:], acl_check=lambda _: None) == 0
try:
    import urllib.request
except RuntimeError:
    pass
else:
    raise AssertionError('network import guard must remain active')
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, *self.argv()], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_subsecond_range_start_includes_truncated_destination_time(self):
        self.rebuild(records=[[punch(stamp='2026-10-14 08:00:00.123456')], []])
        self.contract(timestamp_precision='seconds')
        captures, contract, mappings, checkins = self.load()
        rows, _ = core.reconcile(captures, contract, mappings, checkins,
            dt.datetime(2026, 10, 14, 8, 0, 0, 100000), END, 'Asia/Riyadh')
        self.assertEqual(rows[0]['classification'], 'matched')

    def test_nearby_timestamp_is_never_fuzzy_matched(self):
        self.rebuild(records=[[punch(stamp='2026-10-14 08:00:01')], []])
        self.assertEqual(self.compare()[0][0]['classification'], 'missing')

    def test_reused_uid_at_different_times_does_not_merge(self):
        self.rebuild(records=[[punch(uid=7), punch(stamp='2026-10-14 08:01:00', uid=7)], []])
        rows, _ = self.compare()
        self.assertEqual([r['classification'] for r in rows], ['matched', 'missing'])

    def test_empty_snapshots_create_valid_zero_count_reports(self):
        self.rebuild(records=[[], []], lookups=[], checkins=[])
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(self.compare()[1]['totals']['records'], 0)

    def test_oversized_user_and_incomplete_raw_never_missing(self):
        self.rebuild(records=[[punch('x' * 141), punch(extra=object())], []])
        rows, _ = self.compare()
        self.assertEqual([r['classification'] for r in rows], ['ambiguous', 'ambiguous'])

    def test_bad_export_columns_and_duplicate_checkin_names_rejected(self):
        self.rebuild(checkins=[dict(name='DUPLICATE', employee='A', time=str(START), device_id='', log_type=''),
                               dict(name='DUPLICATE', employee='A', time=str(START), device_id='', log_type='')])
        self.assertEqual(self.run_cli()[0], 1)
        path = self.inputs / 'synthetic-checkins.csv'
        path.write_text('employee,time\nA,2026-10-14 08:00:00\n')
        self.mutate(self.exp, lambda c: c['employee_checkins'].update(sha256=sha(path)))
        self.assertEqual(self.run_cli()[0], 1)

    def test_duplicate_mapping_lookup_rejected(self):
        self.rebuild(lookups=[dict(user_id='001', matches=[]), dict(user_id='001', matches=[])])
        self.assertEqual(self.run_cli()[0], 1)

    def test_committed_synthetic_example_matches_expected_reports(self):
        example = Path(__file__).resolve().parents[1] / 'examples' / 'reconciliation'
        trusted = json.loads((example / 'synthetic-trusted-hashes.json').read_text())
        self.cap = example / 'synthetic-capture-manifest.json'
        self.exp = example / 'synthetic-export-manifest.json'
        self.assertEqual(sha(self.cap), trusted['capture_summary_sha256'])
        self.assertEqual(sha(self.exp), trusted['exports_manifest_sha256'])
        rows, summary = self.compare()
        expected = json.loads((example / 'expected-summary.json').read_text())
        for key in ('totals', 'per_day', 'per_device', 'source_records', 'excluded_valid_out_of_range'):
            self.assertEqual(summary[key], expected[key])
        contents = io.StringIO(newline='')
        core.write_details(contents, rows)
        self.assertEqual(contents.getvalue().encode('utf-8'), (example / 'expected-details.csv').read_bytes())
        self.assertEqual(sha(example / 'expected-details.csv'), expected['reports'][0]['sha256'])
        self.assertEqual(self.run_cli()[0], 0)


if __name__ == '__main__':
    unittest.main()
