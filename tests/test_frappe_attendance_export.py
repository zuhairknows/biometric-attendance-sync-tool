"""Synthetic export tests. No Frappe, devices, database sockets, or service access."""
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reconciliation_fixtures import bundle, dump, sha, punch, START, END
from scripts import export_frappe_attendance as cli
from scripts import frappe_export_review as review
from scripts import frappe_export_database as database
from scripts import attendance_reconciliation as reconciliation


CONTROLLER = '''
class EmployeeCheckin:
    def before_validate(self):
        self.time = get_datetime(self.time).replace(microsecond=0)
    def validate(self):
        validate_active_employee(self.employee)
        self.validate_duplicate_log()
    def validate_duplicate_log(self):
        doc = frappe.db.exists("Employee Checkin", {
            "employee": self.employee, "time": self.time,
            "name": ("!=", self.name), "log_type": self.log_type})
        if doc:
            frappe.throw("synthetic duplicate")
def add_log_based_on_employee_field(employee_field_value, timestamp,
        device_id=None, log_type=None, employee_fieldname="attendance_device_id"):
    employee = frappe.db.get_values("Employee",
        {employee_fieldname: employee_field_value},
        ["name", "employee_name", employee_fieldname], as_dict=True)
    return employee
'''


class Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, parameters):
        key = next(k for k, text in database.SQL.items() if text == sql)
        self.rows = self.connection.execute(key, parameters)

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.calls, self.rollback_count, self.closed = [], 0, False
        self.grants = ['GRANT USAGE ON *.* TO `synthetic`@`localhost`',
                       'GRANT SELECT ON `syntheticdb`.* TO `synthetic`@`localhost`']
        self.identity = dict(database_name='syntheticdb', server_hostname='synthetic-db', server_id=1)
        self.state = dict(isolation_level='REPEATABLE-READ', read_only=1, in_transaction=1)
        self.employees = [dict(employee='SYNTH-EMP-001', attendance_device_id='001', status='Active'),
                          dict(employee='SYNTH-EMP-002', attendance_device_id='002', status='Inactive')]
        self.live_checkins = [dict(name='SYNTH-C-1', employee='SYNTH-EMP-001', time=dt.datetime(2026, 10, 14, 8), device_id='OTHER_DEVICE', log_type='IN'),
                              dict(name='SYNTH-C-2', employee='SYNTH-EMP-002', time=dt.datetime(2026, 10, 15, 17, 0, 0, 123456), device_id=None, log_type=None)]
        self.active_checkins = []
        self.scripts = []
        self.apps = ['frappe', 'hrms']
        self.engine = 'InnoDB'
        self.table_type = 'BASE TABLE'
        self.timezone = 'Asia/Riyadh'
        self.tls = 'TLS_AES_256_GCM_SHA384'
        self.drop_page = False
        self.duplicate_page = False
        self.mapping_delta = 0
        self.after_delta = 0
        self.count_calls = 0
        self.fail = None
        self.concurrent_write = False

    def cursor(self):
        return Cursor(self)

    def execute(self, key, args):
        self.calls.append((key, args))
        if key == self.fail:
            raise RuntimeError('PRIVATE-password-token-employee')
        if key == 'grants':
            return [{'grant': g} for g in self.grants]
        if key == 'tls':
            return [{'Value': self.tls}]
        if key == 'identity':
            return [self.identity]
        if key in ('isolation', 'readonly'):
            return []
        if key == 'begin':
            self.active_checkins = copy.deepcopy(self.live_checkins)
            return []
        if key == 'state':
            return [self.state]
        if key == 'engines':
            return [dict(table_name=t, engine=self.engine, table_type=self.table_type) for t in database.TABLES]
        if key == 'columns':
            return [dict(table_name='tabEmployee', column_name='attendance_device_id', data_type='varchar', datetime_precision=None, collation_name='utf8mb4_unicode_ci'),
                    dict(table_name='tabEmployee Checkin', column_name='time', data_type='datetime', datetime_precision=6, collation_name=None)]
        if key == 'timezone':
            return [{'value': self.timezone}]
        if key == 'apps':
            return [{'defvalue': json.dumps(self.apps)}]
        if key == 'scripts':
            return self.scripts
        if key == 'clock':
            return [dict(snapshot_time=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=1))]
        if key.startswith('mapping'):
            matches = [e for e in self.employees if e['attendance_device_id'].casefold().rstrip() == args[0].casefold().rstrip()]
            return [dict(row_count=len(matches) + self.mapping_delta)] if key == 'mapping_count' else matches
        rows = [r for r in self.active_checkins if args[0] <= r['time'] <= args[1]]
        if key == 'checkin_count':
            self.count_calls += 1
            return [dict(row_count=len(rows) + (self.after_delta if self.count_calls > 1 else 0))]
        if key.startswith('checkins_'):
            if key == 'checkins_next':
                rows = [r for r in rows if r['name'] > args[2]]
            rows = sorted(rows, key=lambda r: r['name'])[:args[-1]]
            if self.concurrent_write:
                self.live_checkins.append(dict(name='LATER', employee='SYNTH-EMP-001', time=START, device_id='LATER', log_type='IN'))
                self.concurrent_write = False
            if self.drop_page and key == 'checkins_next':
                rows = []
            if self.duplicate_page and key == 'checkins_next':
                rows = [self.active_checkins[0]]
            return rows
        raise AssertionError(key)

    def rollback(self):
        self.rollback_count += 1
        if self.fail == 'rollback':
            raise RuntimeError('PRIVATE rollback')

    def close(self):
        self.closed = True
        if self.fail == 'close':
            raise RuntimeError('PRIVATE close')


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / 'inputs'
        self.cap, _ = bundle(self.inputs)
        self.evidence = self.root / 'review'
        self.evidence.mkdir()
        self.output = self.root / 'output'
        self.output.mkdir()
        self.connection = Connection()
        self.config = self.evidence / 'synthetic-integration.py'
        self.config.write_text("ERPNEXT_URL='https://erp.myfpf.com'\nERPNEXT_VERSION=15\n"
            "ERPNEXT_API_SECRET=must_never_execute()\n"
            "devices=[{'device_id':'FP2_DEVICE_08','punch_direction':'AUTO','password':must_never_execute()},"
            "{'device_id':'FP2_DEVICE_11','punch_direction':'OUT'}]\n")
        self.controller = self.evidence / 'synthetic-controller.py'
        self.controller.write_text(CONTROLLER)
        self.hooks = self.evidence / 'synthetic-hooks.json'
        dump(self.hooks, dict(schema='frappe-effective-hooks-v1', installed_apps=['frappe', 'hrms'],
            override_whitelisted_methods={}, override_doctype_class={}, extend_doctype_class={}, doc_events={}))
        self.review_path = self.evidence / 'synthetic-verification.json'
        files = {}
        for role, path in (('employee_checkin', self.controller), ('effective_hooks', self.hooks)):
            files[role] = dict(file=path.name, sha256=sha(path))
        for role in ('frappe_database', 'frappe_query', 'integration_engine'):
            path = self.evidence / ('synthetic-' + role + '.py')
            path.write_text('# Synthetic source evidence. Never a production attestation.\n')
            files[role] = dict(file=path.name, sha256=sha(path))
        now = dt.datetime.now(dt.timezone.utc)
        self.review = dict(schema='frappe-export-review-v1', authoritative_url=review.AUTHORITATIVE_URL,
            route_to_database_verified=True, deployment_sources_verified=True, deployment_frozen_for_export=True,
            production_policy_verified=True, lookup_sql_equivalence_reviewed=True, all_validation_paths_reviewed=True, no_identity_mutating_hooks=True,
            evidence_reference='SYNTHETIC TEST ONLY', reviewed_at=(now - dt.timedelta(minutes=1)).isoformat(),
            expires_at=(now + dt.timedelta(hours=1)).isoformat(), site_timezone='Asia/Riyadh',
            integration_policy_sha256=review.fingerprint(review.literal_policy(self.config)),
            endpoint_path='hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field',
            database_identity_sha256=review.fingerprint(self.connection.identity),
            server_scripts_sha256=review.fingerprint([]), files=files, semantics=review.infer_semantics(CONTROLLER))
        dump(self.review_path, self.review)
        self.ca = self.evidence / 'synthetic-ca.pem'
        self.ca.write_text('SYNTHETIC - never use as a real CA')
        self.profile = self.evidence / 'synthetic-db-profile.json'
        dump(self.profile, dict(host='synthetic-db.invalid', port=3306, database='syntheticdb', user='synthetic', tls_ca=str(self.ca)))
        self.review['database_profile_sha256'] = review.fingerprint(cli.database_profile(self.profile))
        self.review['tls_ca_sha256'] = sha(self.ca)
        self.save_review()
        self.connector = mock.Mock(return_value=self.connection)
        self.password = mock.Mock(return_value='PRIVATE-password')

    def args(self, live=True):
        args = ['--capture-summary', str(self.cap), '--capture-summary-sha256', sha(self.cap),
                '--integration-config', str(self.config), '--verification', str(self.review_path),
                '--verification-sha256', sha(self.review_path), '--output', str(self.output), '--page-size', '1']
        return args + ['--live-export', '--operator-authorized', '--db-profile', str(self.profile)] if live else args

    def run_export(self, args=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(self.args() if args is None else args, connector=self.connector,
                            password_reader=self.password, acl_check=lambda _: None)
        return code, output.getvalue()

    def save_review(self):
        dump(self.review_path, self.review)

    def change_source(self, text):
        self.controller.write_text(text)
        self.review['files']['employee_checkin']['sha256'] = sha(self.controller)
        self.save_review()

    def test_default_plan_no_connection_password_or_files(self):
        result, console = self.run_export(self.args(False))
        self.assertEqual(result, 0, console)
        self.connector.assert_not_called()
        self.password.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse(json.loads(console)['certification_issued'])

    def test_live_authorization_must_be_explicit(self):
        args = self.args()
        args.remove('--operator-authorized')
        self.assertEqual(self.run_export(args)[0], 1)
        self.connector.assert_not_called()

    def test_success_exact_names_complete_hashes_and_cli_compatibility(self):
        result, console = self.run_export()
        self.assertEqual(result, 0, console)
        self.assertEqual({p.name for p in self.output.iterdir()}, {'employee-mappings.json', 'employee-checkins.csv', 'export-manifest.json'})
        manifest = self.output / 'export-manifest.json'
        contract, lookups, checkins, _, _ = reconciliation.load_exports(manifest, sha(manifest), START, END, 'Asia/Riyadh')
        self.assertEqual(lookups['001'][0]['employee'], 'SYNTH-EMP-001')
        self.assertEqual(lookups['002'][0]['status'], 'Inactive')
        self.assertEqual(checkins[0]['device_id'], 'OTHER_DEVICE')
        self.assertEqual(checkins[1]['device_id'], '')
        self.assertEqual(checkins[1]['log_type'], '')
        self.assertEqual(checkins[1]['time'], '2026-10-15 17:00:00.123456')
        self.assertEqual(contract['completeness']['independent_count_before'], 2)
        self.assertEqual(contract['completeness']['independent_count_after'], 2)
        self.assertTrue(self.connection.closed)
        self.assertEqual(self.connection.rollback_count, 1)
        self.assertEqual(json.loads(console)['manifest_sha256'], sha(manifest))
        captures, _, _ = reconciliation.load_captures(self.cap, sha(self.cap))
        rows, _ = reconciliation.reconcile(captures, contract, lookups, checkins, START, END, 'Asia/Riyadh')
        self.assertEqual(rows[0]['classification'], 'matched')
        self.assertEqual(rows[1]['classification'], 'ambiguous')

    def test_no_identifiers_credentials_or_database_details_on_console(self):
        result, output = self.run_export()
        self.assertEqual(result, 0)
        for forbidden in ('SYNTH-EMP', 'SYNTH-C-', 'PRIVATE', 'syntheticdb', 'synthetic-db', 'OTHER_DEVICE', str(self.inputs)):
            self.assertNotIn(forbidden, output)
        for path in self.output.iterdir():
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('PRIVATE', text)
            self.assertNotIn('must_never_execute', text)

    def test_duplicate_biometric_mapping_preserves_every_employee(self):
        self.connection.employees.append(dict(employee='SYNTH-SECOND', attendance_device_id='001', status='Active'))
        self.assertEqual(self.run_export()[0], 0)
        mappings = json.loads((self.output / 'employee-mappings.json').read_text())
        self.assertEqual(len(mappings['lookups'][0]['matches']), 2)

    def test_leading_zero_and_empty_results_preserved(self):
        self.connection.employees[0]['attendance_device_id'] = '1'
        self.assertEqual(self.run_export()[0], 0)
        mappings = json.loads((self.output / 'employee-mappings.json').read_text())
        self.assertEqual(mappings['lookups'][0], dict(user_id='001', matches=[]))

    def test_lookup_uses_database_collation_without_python_filter(self):
        self.connection.employees[0]['attendance_device_id'] = '001 '
        self.assertEqual(self.run_export()[0], 0)
        mappings = json.loads((self.output / 'employee-mappings.json').read_text())
        self.assertEqual(mappings['lookups'][0]['matches'][0]['attendance_device_id'], '001 ')

    def test_database_identity_not_hostname_assumption(self):
        self.connection.identity['database_name'] = 'otherdb'
        code, console = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn('authoritative_database_mismatch', console)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_mcp_host_cannot_be_substituted_for_authoritative_url(self):
        self.review['authoritative_url'] = 'https://myfpf.v.frappe.cloud'
        self.save_review()
        self.assertEqual(self.run_export()[0], 1)
        self.connector.assert_not_called()

    def test_wrong_integration_url_rejected_without_execution(self):
        self.config.write_text(self.config.read_text().replace('erp.myfpf.com', 'myfpf.v.frappe.cloud'))
        self.assertEqual(self.run_export()[0], 1)
        self.connector.assert_not_called()

    def test_dynamic_policy_or_duplicate_device_id_blocks(self):
        original = self.config.read_text()
        for suffix in ("\ndevices.append({'device_id':'new'})", "\ndevices=[]"):
            with self.subTest(suffix=suffix):
                self.config.write_text(original + suffix)
                self.assertEqual(self.run_export()[0], 1)

    def test_actual_punch_policies_are_bound_to_review(self):
        self.config.write_text(self.config.read_text().replace("'punch_direction':'OUT'", "'punch_direction':'IN'"))
        self.assertEqual(self.run_export()[0], 1)
        self.connector.assert_not_called()

    def test_review_hash_and_source_hash_checked(self):
        self.controller.write_text(CONTROLLER + '\n# Changed deployment')
        self.assertEqual(self.run_export()[0], 1)
        self.connector.assert_not_called()

    def test_all_verification_attestations_required(self):
        for field in ('route_to_database_verified', 'deployment_sources_verified', 'deployment_frozen_for_export',
                      'production_policy_verified', 'lookup_sql_equivalence_reviewed', 'all_validation_paths_reviewed', 'no_identity_mutating_hooks'):
            with self.subTest(field=field):
                self.review[field] = False
                self.save_review()
                self.assertEqual(self.run_export()[0], 1)
                self.review[field] = True
        self.connector.assert_not_called()

    def test_expired_review_blocks(self):
        self.review['expires_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat()
        self.save_review()
        self.assertEqual(self.run_export()[0], 1)
        self.connector.assert_not_called()

    def test_unrecognized_lookup_or_duplicate_source_is_not_certified(self):
        for text in (CONTROLLER.replace('attendance_device_id', 'name'),
                     CONTROLLER.replace('as_dict=True', 'as_dict=True, limit=1'),
                     CONTROLLER.replace('"log_type": self.log_type', '"device_id": self.device_id'),
                     CONTROLLER.replace('self.validate_duplicate_log()', 'return'),
                     CONTROLLER.replace('.replace(microsecond=0)', '.replace(second=0)')):
            with self.subTest(text=text):
                self.change_source(text)
                self.assertEqual(self.run_export()[0], 1)
        self.connector.assert_not_called()

    def test_server_policy_is_derived_not_trusted_from_checkbox(self):
        self.review['semantics']['duplicate_key'] = 'employee_time'
        self.save_review()
        self.assertEqual(self.run_export()[0], 1)

    def test_supported_employee_time_and_microseconds_source(self):
        source = CONTROLLER.replace(', "log_type": self.log_type', '')
        source = source.replace('    def before_validate(self):\n        self.time = get_datetime(self.time).replace(microsecond=0)\n', '')
        self.change_source(source)
        self.review['semantics'] = review.infer_semantics(source)
        self.save_review()
        self.assertEqual(self.run_export()[0], 0)
        contract = json.loads((self.output / 'export-manifest.json').read_text())
        self.assertEqual(contract['duplicate_key'], 'employee_time')
        self.assertEqual(contract['timestamp_precision'], 'microseconds')

    def test_effective_overrides_or_hooks_block_even_with_review_flag(self):
        original = self.hooks.read_bytes()
        for field, key in (('override_whitelisted_methods', self.review['endpoint_path']),
                           ('override_doctype_class', 'Employee Checkin'), ('extend_doctype_class', 'Employee Checkin'),
                           ('doc_events', 'Employee Checkin'), ('doc_events', '*')):
            with self.subTest(field=field, key=key):
                self.hooks.write_bytes(original)
                hooks = json.loads(original)
                hooks[field][key] = ['custom.hook']
                dump(self.hooks, hooks)
                self.review['files']['effective_hooks']['sha256'] = sha(self.hooks)
                self.save_review()
                self.assertEqual(self.run_export()[0], 1)
        self.connector.assert_not_called()

    def test_live_server_scripts_inventory_change_blocks(self):
        self.connection.scripts = [dict(name='Private', script_type='DocType Event', reference_doctype='Employee Checkin',
            doctype_event='Before Validate', api_method=None, script='SECRET')]
        code, console = self.run_export()
        self.assertEqual(code, 1)
        self.assertNotIn('SECRET', console)

    def test_even_reviewed_checkin_server_script_is_unsupported(self):
        self.connection.scripts = [dict(name='Private', script_type='DocType Event', reference_doctype='Employee Checkin',
            doctype_event='Before Validate', api_method=None, script='pass')]
        self.review['server_scripts_sha256'] = review.fingerprint(self.connection.scripts)
        self.save_review()
        self.assertEqual(self.run_export()[0], 1)

    def test_installed_apps_or_timezone_change_blocks(self):
        self.connection.apps.append('custom')
        self.assertEqual(self.run_export()[0], 1)
        self.connection.apps.pop()
        self.connection.timezone = 'UTC'
        self.assertEqual(self.run_export()[0], 1)

    def test_permission_failure_never_falls_back_to_filtered_api(self):
        self.connection.fail = 'mapping'
        code, console = self.run_export()
        self.assertEqual(code, 1)
        self.assertNotIn('PRIVATE', console)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertTrue(self.connection.closed)

    def test_write_capable_role_and_global_grants_rejected(self):
        for grant in ('GRANT ALL PRIVILEGES ON `syntheticdb`.* TO `x`@`h`',
                      'GRANT SELECT, UPDATE ON `syntheticdb`.* TO `x`@`h`',
                      'GRANT SELECT ON *.* TO `x`@`h`', 'GRANT `role` TO `x`@`h`',
                      'GRANT SELECT ON `syntheticdb`.* TO `x`@`h` WITH GRANT OPTION'):
            with self.subTest(grant=grant):
                self.connection.grants = [grant]
                self.assertEqual(self.run_export()[0], 1)

    def test_missing_table_privileges_fail(self):
        self.connection.grants = ['GRANT SELECT ON `syntheticdb`.`tabEmployee` TO `x`@`h`']
        self.assertEqual(self.run_export()[0], 1)

    def test_explicit_minimal_table_grants_supported(self):
        self.connection.grants = ['GRANT SELECT ON `syntheticdb`.`' + t + '` TO `x`@`h`' for t in database.TABLES]
        self.assertEqual(self.run_export()[0], 0)

    def test_readonly_and_isolation_confirmation_required(self):
        for key, value in (('read_only', 0), ('in_transaction', 0), ('isolation_level', 'READ-COMMITTED')):
            with self.subTest(key=key):
                original = self.connection.state[key]
                self.connection.state[key] = value
                self.assertEqual(self.run_export()[0], 1)
                self.connection.state[key] = original

    def test_nontransactional_tables_or_views_block(self):
        self.connection.engine = 'MyISAM'
        self.assertEqual(self.run_export()[0], 1)
        self.connection.engine = 'InnoDB'
        self.connection.table_type = 'VIEW'
        self.assertEqual(self.run_export()[0], 1)

    def test_tls_required(self):
        self.connection.tls = ''
        self.assertEqual(self.run_export()[0], 1)

    def test_truncated_or_repeated_pages_do_not_certify(self):
        self.connection.drop_page = True
        self.assertEqual(self.run_export()[0], 1)
        self.assertFalse((self.output / 'export-manifest.json').exists())
        self.connection.drop_page = False
        self.connection.duplicate_page = True
        self.assertEqual(self.run_export()[0], 1)

    def test_independent_mapping_count_mismatch_blocks(self):
        self.connection.mapping_delta = 1
        self.assertEqual(self.run_export()[0], 1)

    def test_changed_independent_checkin_count_blocks(self):
        self.connection.after_delta = 1
        self.assertEqual(self.run_export()[0], 1)

    def test_concurrent_writer_not_visible_in_same_snapshot(self):
        self.connection.concurrent_write = True
        self.assertEqual(self.run_export()[0], 0)
        self.assertEqual(len(self.connection.live_checkins), 3)
        manifest = json.loads((self.output / 'export-manifest.json').read_text())
        self.assertEqual(manifest['employee_checkins']['rows'], 2)
        self.assertEqual(sum(k == 'begin' for k, _ in self.connection.calls), 1)

    def test_cleanup_failure_prevents_any_certificate(self):
        for failure in ('rollback', 'close'):
            with self.subTest(failure=failure):
                self.connection.fail = failure
                self.assertEqual(self.run_export()[0], 1)
                self.assertFalse((self.output / 'export-manifest.json').exists())

    def test_source_evidence_remains_unchanged(self):
        before = {p: p.read_bytes() for p in self.inputs.iterdir()}
        self.assertEqual(self.run_export()[0], 0)
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_no_database_mutation_queries_or_commits(self):
        self.assertEqual(self.run_export()[0], 0)
        queries = [database.SQL[k] for k, _ in self.connection.calls]
        for sql in queries:
            self.assertTrue(sql.startswith(('SELECT ', 'SHOW ', 'SET SESSION TRANSACTION ', 'START TRANSACTION ')), sql)
            self.assertNotIn('FOR UPDATE', sql)
        snapshot = database.Snapshot(self.connection, 'syntheticdb', self.review, self.review['semantics'], {})
        with self.assertRaises(reconciliation.ReconciliationError):
            snapshot.query('DELETE FROM tabEmployee')
        self.assertFalse(hasattr(self.connection, 'commit'))

    def test_output_acl_failure_prevents_connection(self):
        with contextlib.redirect_stdout(io.StringIO()):
            result = cli.main(self.args(), connector=self.connector, password_reader=self.password,
                acl_check=mock.Mock(side_effect=PermissionError('PRIVATE')))
        self.assertEqual(result, 1)
        self.connector.assert_not_called()

    def test_existing_output_not_overwritten(self):
        (self.output / 'employee-mappings.json').write_text('original')
        self.assertEqual(self.run_export()[0], 1)
        self.assertEqual((self.output / 'employee-mappings.json').read_text(), 'original')
        self.connector.assert_not_called()

    def test_manifest_publication_failure_does_not_claim_success(self):
        original = cli.publish
        def publish(directory, name, write):
            if name == 'export-manifest.json':
                raise OSError('PRIVATE')
            return original(directory, name, write)
        with mock.patch.object(cli, 'publish', side_effect=publish):
            self.assertEqual(self.run_export()[0], 1)
        self.assertFalse((self.output / 'export-manifest.json').exists())

    def test_connector_output_and_exception_do_not_echo_secrets(self):
        def connect(*args):
            print('PRIVATE')
            raise RuntimeError('PRIVATE')
        self.connector.side_effect = connect
        code, console = self.run_export()
        self.assertEqual(code, 1)
        self.assertNotIn('PRIVATE', console)

    def test_password_in_profile_or_cli_rejected(self):
        profile = json.loads(self.profile.read_text())
        profile['password'] = 'PRIVATE'
        dump(self.profile, profile)
        self.assertEqual(self.run_export()[0], 1)
        code, console = self.run_export(self.args() + ['--password', 'PRIVATE'])
        self.assertEqual(code, 1)
        self.assertNotIn('PRIVATE', console)

    def test_real_driver_factory_enforces_verified_tls_and_no_local_infile(self):
        factory = mock.Mock(return_value=self.connection)
        module = types.SimpleNamespace(connect=factory, cursors=types.SimpleNamespace(DictCursor=object()))
        with mock.patch.dict(sys.modules, pymysql=module), mock.patch('importlib.metadata.version', return_value='1.2.3'):
            database.connect(cli.database_profile(self.profile), 'PRIVATE')
        kwargs = factory.call_args.kwargs
        self.assertTrue(kwargs['ssl_verify_cert'])
        self.assertTrue(kwargs['ssl_verify_identity'])
        self.assertFalse(kwargs['local_infile'])
        self.assertNotIn('init_command', kwargs)
        self.assertNotIn('read_default_file', kwargs)

    def test_fresh_plan_process_blocks_sensitive_and_network_imports(self):
        script = '''
import sys
class Guard:
    def find_spec(self, name, *args):
        if name.split('.')[0] in {'frappe','pymysql','requests','socket','zk','local_config','erpnext_sync','erpnext_sync_win','SMWinservice','sqlite3','sync_ledger','durable_sync','durable_delivery'}:
            raise RuntimeError('forbidden import')
sys.meta_path.insert(0, Guard())
from scripts.export_frappe_attendance import main
assert main(sys.argv[1:], acl_check=lambda _: None) == 0
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, *self.args(False)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_changed_database_profile_or_ca_blocks_before_password_prompt(self):
        original = self.profile.read_bytes()
        profile = json.loads(original)
        profile['host'] = 'other-backend.invalid'
        dump(self.profile, profile)
        self.assertEqual(self.run_export()[0], 1)
        self.profile.write_bytes(original)
        self.ca.write_text('REPLACED CA')
        self.assertEqual(self.run_export()[0], 1)
        self.password.assert_not_called()
        self.connector.assert_not_called()

    def test_capture_and_review_anchor_mismatch_blocks_before_connection(self):
        for flag in ('--capture-summary-sha256', '--verification-sha256'):
            with self.subTest(flag=flag):
                args = self.args()
                args[args.index(flag) + 1] = '0' * 64
                self.assertEqual(self.run_export(args)[0], 1)
        self.connector.assert_not_called()

    def test_all_relevant_identifiers_including_uncertain_records_are_selected(self):
        records = [[punch('001'), punch('003', timestamp='bad'), punch('004', '2026-10-10 08:00:00')], []]
        path = self.root / 'more-inputs'
        self.cap, _ = bundle(path, records=records)
        self.assertEqual(self.run_export()[0], 0)
        mappings = json.loads((self.output / 'employee-mappings.json').read_text())
        self.assertEqual([row['user_id'] for row in mappings['lookups']], ['001', '003'])

    def test_aliased_integration_policy_is_unsupported(self):
        self.config.write_text(self.config.read_text().replace('devices=[', 'devices=alias=['))
        self.assertEqual(self.run_export()[0], 1)
        self.connector.assert_not_called()

    def test_export_compatibility_failure_never_publishes_final_manifest(self):
        with mock.patch.object(cli, 'load_exports', side_effect=ValueError('PRIVATE')):
            code, console = self.run_export()
        self.assertEqual(code, 1)
        self.assertNotIn('PRIVATE', console)
        self.assertFalse((self.output / 'export-manifest.json').exists())

    def test_fsync_failure_does_not_issue_certificate(self):
        with mock.patch('scripts.reconcile_attendance.os.fsync', side_effect=OSError('PRIVATE')):
            self.assertEqual(self.run_export()[0], 1)
        self.assertFalse((self.output / 'export-manifest.json').exists())

    def test_sql_like_employee_identifier_remains_bound_parameter(self):
        dangerous = "001' OR 1=1 --"
        self.cap, _ = bundle(self.root / 'sql-inputs', records=[[punch(dangerous)], []])
        self.assertEqual(self.run_export()[0], 0)
        mappings = [args for key, args in self.connection.calls if key == 'mapping']
        self.assertEqual(mappings, [(dangerous,)])
        self.assertTrue(all(dangerous not in statement for statement in database.SQL.values()))

    def test_snapshot_start_is_bounded_before_any_data_export(self):
        self.assertEqual(self.run_export()[0], 0)
        keys = [key for key, _ in self.connection.calls]
        clocks = [index for index, key in enumerate(keys) if key == 'clock']
        self.assertLess(clocks[0], keys.index('begin'))
        self.assertLess(keys.index('begin'), clocks[1])
        self.assertLess(clocks[1], keys.index('mapping'))
        manifest = json.loads((self.output / 'export-manifest.json').read_text())
        evidence = manifest['completeness']
        self.assertLessEqual(evidence['snapshot_started_not_before'], evidence['snapshot_started_not_after'])

    def test_invalid_timestamp_and_log_type_block_without_dropping_rows(self):
        self.connection.live_checkins[0]['log_type'] = 'UNSUPPORTED'
        self.assertEqual(self.run_export()[0], 1)
        self.assertFalse((self.output / 'export-manifest.json').exists())
        self.connection.live_checkins[0]['log_type'] = 'IN'
        self.connection.live_checkins[0]['time'] = START.replace(tzinfo=dt.timezone.utc)
        self.assertEqual(self.run_export()[0], 1)

    def test_plan_does_not_issue_certificate_even_with_authorization_flag(self):
        code, console = self.run_export(self.args(False) + ['--operator-authorized'])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(console)['certification_issued'])
        self.connector.assert_not_called()

    def test_unreviewed_driver_version_blocks_before_authentication(self):
        factory = mock.Mock()
        module = types.SimpleNamespace(connect=factory)
        with mock.patch.dict(sys.modules, pymysql=module), mock.patch('importlib.metadata.version', return_value='1.1.2'):
            with self.assertRaises(reconciliation.ReconciliationError):
                database.connect(cli.database_profile(self.profile), 'PRIVATE')
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
