"""Diagnostic-only capture tests: fake terminals/SCM/ACLs, temporary evidence, no live calls."""
import contextlib
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from scripts import capture_device_history_readonly as capture
from scripts import capture_history_safety as safety


class FakeTerminal:
    def __init__(self, records=None, fail=None):
        self.records = [] if records is None else records
        self.fail = fail
        self.calls = []
        self.is_connect = False
        self._ZK__sock = mock.Mock()

    def connect(self):
        self.calls.append('connect')
        if self.fail == 'connect':
            raise RuntimeError('private device password and employee identifier')
        self.is_connect = True
        return self

    def get_attendance(self):
        self.calls.append('get_attendance')
        print('SDK output: employee-private device-private API-private')
        if self.fail == 'read':
            raise ValueError('employee-private API-private')
        return self.records

    def disable_device(self):
        self.calls.append('disable_device')
        if self.fail == 'disable':
            raise TimeoutError('disable response was lost')
        return True

    def enable_device(self):
        self.calls.append('enable_device')
        if self.fail == 'enable':
            raise TimeoutError('cannot enable')
        return True

    def disconnect(self):
        self.calls.append('disconnect')
        if self.fail == 'disconnect':
            raise TimeoutError('cannot disconnect')
        self.is_connect = False
        return True


def punch(user='employee-private', timestamp=None, uid=1):
    return dict(user_id=user, timestamp=timestamp or dt.datetime.now() - dt.timedelta(days=2),
                uid=uid, punch=0, status=1)


class CaptureUtilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='phase1-capture-tests-')
        self.root = Path(self.temp.name)
        self.output = self.root / 'evidence'
        self.output.mkdir()
        self.config = self.root / 'approved.py'
        self.device_rows = [dict(device_id='FP2_DEVICE_08', ip='10.0.1.23', port=4378, password=123456),
                            dict(device_id='FP2_DEVICE_11', ip='10.0.1.26', port=4381, password=654321),
                            dict(device_id='FP1_DEVICE_01', ip='10.0.0.20', port=4371, password=0)]
        self.write_config()
        self.clients = {device_id: FakeTerminal([punch()]) for device_id in capture.ALLOWED_DEVICES}
        self.factory = mock.Mock(side_effect=lambda device, timeout: self.clients[device.device_id])
        self.check_service = mock.Mock(return_value='STOPPED')

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, prefix=''):
        self.config.write_text(prefix + '\nERPNEXT_API_KEY="API-private"\nERPNEXT_API_SECRET="API-private"\ndevices=' + repr(self.device_rows), encoding='utf-8')

    def run_cli(self, *extra):
        args = ['--config', str(self.config), '--output', str(self.output), *extra]
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            result = capture.main(args, factory=self.factory, check_service=self.check_service, acl_check=lambda _: None)
        self.last_output = output.getvalue() + errors.getvalue()
        return result, json.loads(output.getvalue())

    def live(self, *extra):
        return self.run_cli('--capture', '--maintenance-window-confirmed', *extra)

    def evidence(self, report, index=0):
        return json.loads((self.output / report['devices'][index]['evidence_file']).read_text())

    def test_default_dry_run_does_not_import_sdk_connect_or_write(self):
        result, report = self.run_cli()
        self.assertEqual(result, 0)
        self.assertEqual(report['mode'], 'dry-run')
        self.assertFalse(report['writes_performed'])
        self.factory.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_explicit_dry_run_does_not_capture_even_when_window_confirmed(self):
        self.assertEqual(self.run_cli('--dry-run', '--maintenance-window-confirmed')[0], 0)
        self.factory.assert_not_called()

    def test_unknown_and_unapproved_device_ids_are_rejected(self):
        for device_id, category in (('UNKNOWN', 'unknown_device_id'), ('FP1_DEVICE_01', 'unapproved_device_id')):
            with self.subTest(device_id=device_id):
                result, report = self.live('--devices', device_id)
                self.assertEqual(result, 1)
                self.assertEqual(report['error_category'], category)
        self.factory.assert_not_called()

    def test_running_pending_paused_unknown_and_missing_service_block_live(self):
        for state in ('RUNNING', 'START_PENDING', 'STOP_PENDING', 'PAUSED', 'UNKNOWN'):
            with self.subTest(state=state):
                self.check_service.return_value = state
                self.assertEqual(self.live()[0], 1)
        self.check_service.side_effect = safety.CaptureError('service_check_unavailable')
        self.assertEqual(self.live()[0], 1)
        self.factory.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_capture_requires_maintenance_confirmation(self):
        self.assertEqual(self.run_cli('--capture')[0], 1)
        self.factory.assert_not_called()

    def test_no_ip_port_password_clear_force_or_combined_mode_arguments(self):
        for arguments in (('--ip', '1.2.3.4'), ('--port', '1234'), ('--password', 'private-token'),
                          ('--clear',), ('--force',), ('--capture', '--dry-run')):
            self.assertEqual(self.run_cli(*arguments)[0], 1)
            self.assertNotIn('private-token', self.last_output)
        self.factory.assert_not_called()

    def test_only_literal_selected_connection_properties_are_evaluated(self):
        self.device_rows[0]['not_a_connection_property'] = 'secret-extra'
        self.write_config('raise RuntimeError("config must never execute")')
        result, report = self.run_cli('--devices', 'FP2_DEVICE_08')
        self.assertEqual(result, 0)
        self.assertNotIn('API-private', self.last_output)
        self.assertNotIn('secret-extra', self.last_output)
        selected = capture.approved_devices(self.config, ['FP2_DEVICE_08'])
        self.assertNotIn('123456', repr(selected[0]))

    def test_dynamic_and_duplicate_device_configuration_fail_closed(self):
        for content in ('devices=get_devices()', 'devices=[]\ndevices.append({})',
                        'devices=' + repr([self.device_rows[0], self.device_rows[0]])):
            self.config.write_text(content)
            self.assertEqual(self.run_cli()[0], 1)
        self.factory.assert_not_called()

    def test_unselected_connection_properties_and_api_expressions_are_not_evaluated(self):
        text = 'devices=' + repr(self.device_rows)
        text = text.replace("'password': 0", "'password': must_not_be_evaluated()")
        text += '\nERPNEXT_API_KEY=must_not_be_evaluated()\nERPNEXT_API_SECRET=must_not_be_evaluated()\n'
        self.config.write_text(text)
        self.assertEqual(self.run_cli('--devices', 'FP2_DEVICE_08')[0], 0)
        self.factory.assert_not_called()

    def test_config_syntax_error_does_not_echo_credentials(self):
        self.config.write_text('ERPNEXT_API_SECRET="private-syntax-token')
        self.assertEqual(self.run_cli()[0], 1)
        self.assertNotIn('private-syntax-token', self.last_output)

    def test_two_devices_produce_independent_full_snapshots_and_hashes(self):
        result, report = self.live()
        self.assertEqual(result, 0)
        self.assertEqual(len(report['devices']), 2)
        self.assertEqual(len(list(self.output.glob('FP2_DEVICE_*.json'))), 2)
        for item in report['devices']:
            path = self.output / item['evidence_file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item['sha256'])
            self.assertEqual(item['total_fetched'], 1)
            self.assertEqual(item['valid'], 1)
        self.assertEqual(hashlib.sha256((self.output / report['summary_file']).read_bytes()).hexdigest(), report['summary_sha256'])
        self.assertNotIn('employee-private', self.last_output)
        self.assertNotIn('123456', self.last_output)
        self.assertNotIn('654321', self.last_output)
        self.assertNotIn('API-private', self.last_output)

    def test_valid_raw_datetime_uid_codes_and_order_are_preserved(self):
        time = dt.datetime(2026, 10, 13, 1, 13, 43, 123456)
        self.clients['FP2_DEVICE_08'].records = [punch(timestamp=time), punch(timestamp=time, uid=1)]
        _, report = self.live('--devices', 'FP2_DEVICE_08')
        data = self.evidence(report)
        self.assertEqual(len(data['records']), 2)  # No UID-based deduplication.
        self.assertEqual(data['records'][0]['raw']['timestamp']['iso'], time.isoformat())
        self.assertEqual(data['records'][0]['raw']['timestamp']['__type__'], 'datetime')
        self.assertEqual(data['records'][0]['raw']['uid'], 1)
        self.assertEqual([row['source_order_index'] for row in data['records']], [0, 1])

    def test_synthetic_incident_tails_are_retained_and_future_empty_id_flagged(self):
        for device_id, last_valid in (('FP2_DEVICE_08', dt.datetime(2026, 10, 13, 1, 13, 43)),
                                      ('FP2_DEVICE_11', dt.datetime(2026, 10, 11, 1, 15, 55))):
            self.clients[device_id].records = [punch(timestamp=last_valid), punch(user='', timestamp=dt.datetime(2099, 1, 1))]
        _, report = self.live()
        for index, item in enumerate(report['devices']):
            self.assertEqual((item['total_fetched'], item['valid'], item['invalid']), (2, 1, 1))
            tail = self.evidence(report, index)['records'][-1]
            self.assertIn('empty_user_id', tail['validation_categories'])
            self.assertIn('future_timestamp', tail['validation_categories'])
            self.assertEqual(tail['raw']['user_id'], '')

    def test_malformed_timestamp_shape_and_unexpected_types_do_not_stop_later_rows(self):
        strange = punch(timestamp='bad')
        strange['bytes_extra'] = b'\x00\xff'
        strange['tuple_extra'] = (1, 2)
        strange['mapping_extra'] = {1: 'a', '1': 'b'}
        strange['float_extra'] = float('nan')
        strange['object_extra'] = object()
        cyclic = []
        cyclic.append(cyclic)
        strange['cycle_extra'] = cyclic
        self.clients['FP2_DEVICE_08'].records = [strange, None, punch()]
        result, report = self.live('--devices', 'FP2_DEVICE_08')
        self.assertEqual(result, 0)
        self.assertEqual(report['devices'][0]['invalid'], 2)
        rows = self.evidence(report)['records']
        self.assertIn('invalid_timestamp', rows[0]['validation_categories'])
        self.assertEqual(rows[0]['raw']['bytes_extra']['hex'], '00ff')
        self.assertEqual(rows[0]['raw']['mapping_extra']['__type__'], 'mapping')
        self.assertTrue(rows[2]['valid'])

    def test_sensitive_extra_fields_are_redacted_in_protected_evidence(self):
        row = punch()
        row['password'] = 'private-device-token'
        row['nested'] = {'Authorization': 'private-auth-token'}
        self.clients['FP2_DEVICE_08'].records = [row]
        _, report = self.live('--devices', 'FP2_DEVICE_08')
        text = (self.output / report['devices'][0]['evidence_file']).read_text()
        self.assertNotIn('private-device-token', text)
        self.assertNotIn('private-auth-token', text)
        self.assertIn('sensitive_field_redacted', text)

    def test_range_is_summary_only_and_preserves_old_and_invalid_records(self):
        self.clients['FP2_DEVICE_08'].records = [punch(timestamp=dt.datetime(2026, 10, 11)),
            punch(timestamp=dt.datetime(2026, 10, 14)), punch(timestamp='broken')]
        _, report = self.live('--devices', 'FP2_DEVICE_08', '--start', '2026-10-14T00:00:00', '--end', '2026-10-15T00:00:00')
        self.assertEqual(report['devices'][0]['total_fetched'], 3)
        self.assertEqual(report['devices'][0]['valid_in_requested_range'], 1)
        self.assertEqual(len(self.evidence(report)['records']), 3)

    def test_invalid_range_and_timeout_fail_before_device_connection(self):
        for args in (('--timeout', '0'), ('--timeout', '31'),
                     ('--start', '2026-10-15T00:00:00', '--end', '2026-10-14T00:00:00'),
                     ('--start', '2026-10-15T00:00:00+03:00')):
            self.assertEqual(self.live(*args)[0], 1)
        self.factory.assert_not_called()

    def test_connection_and_read_failures_are_isolated_and_disconnected(self):
        for fail in ('connect', 'read'):
            with self.subTest(fail=fail):
                self.clients['FP2_DEVICE_08'] = FakeTerminal(fail=fail)
                result, report = self.live()
                self.assertEqual(result, 1)
                self.assertEqual(report['devices'][0]['status'], 'failed')
                self.assertEqual(report['devices'][1]['status'], 'captured')
                if fail == 'read':
                    self.assertIn('disconnect', self.clients['FP2_DEVICE_08'].calls)
                else:
                    self.assertNotIn('disconnect', self.clients['FP2_DEVICE_08'].calls)
                    self.assertEqual(report['devices'][0]['cleanup']['disconnect'], 'connect_unconfirmed_local_socket_closed')
                self.clients['FP2_DEVICE_08']._ZK__sock.close.assert_called_once()
                self.assertNotIn('employee-private', self.last_output)

    def test_default_read_never_disables_or_clears_a_device(self):
        result, _ = self.live()
        self.assertEqual(result, 0)
        for terminal in self.clients.values():
            self.assertEqual(terminal.calls, ['connect', 'get_attendance', 'disconnect'])

    def test_optional_pause_reenables_after_read_and_ambiguous_disable_failure(self):
        for fail in ('read', 'disable'):
            self.clients['FP2_DEVICE_08'] = FakeTerminal(fail=fail)
            self.assertEqual(self.live('--devices', 'FP2_DEVICE_08', '--pause-device')[0], 1)
            self.assertIn('enable_device', self.clients['FP2_DEVICE_08'].calls)
            self.assertEqual(self.clients['FP2_DEVICE_08'].calls[-1], 'disconnect')

    def test_cleanup_failure_requires_manual_action_and_blocks_remaining_devices(self):
        for fail in ('enable', 'disconnect'):
            self.clients['FP2_DEVICE_08'] = FakeTerminal([punch()], fail=fail)
            self.clients['FP2_DEVICE_11'] = FakeTerminal([punch()])
            result, report = self.live('--pause-device')
            self.assertEqual(result, 1)
            self.assertEqual(len(report['devices']), 1)
            self.assertEqual(report['devices'][0]['error_category'], 'device_cleanup_failed')
            self.assertIn('manual_action', report['devices'][0])
            self.assertEqual(self.clients['FP2_DEVICE_11'].calls, [])

    def test_serialization_or_disk_failure_occurs_after_terminal_restoration(self):
        with mock.patch.object(capture, 'atomic_json', side_effect=OSError('disk private-token')), contextlib.redirect_stderr(io.StringIO()):
            # Fail persistence after retrieval, while keeping safe progress formatting operational.
            args = types.SimpleNamespace(timeout=10, pause_device=True, acl_check=lambda _: None, start=None, end=None)
            device = capture.approved_devices(self.config, ['FP2_DEVICE_08'])[0]
            report = capture.capture_one(device, self.output, args, self.factory, self.check_service)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(self.clients['FP2_DEVICE_08'].calls[-2:], ['enable_device', 'disconnect'])
        self.assertEqual(list(self.output.glob('FP2_DEVICE_*.json')), [])

    def test_failed_flush_never_publishes_a_successful_capture(self):
        actual = capture.atomic_json
        def failing(directory, name, writer):
            if name.startswith('FP2_DEVICE'):
                with mock.patch.object(capture.os, 'fsync', side_effect=OSError('disk full')):
                    return actual(directory, name, writer)
            return actual(directory, name, writer)
        with mock.patch.object(capture, 'atomic_json', side_effect=failing):
            result, report = self.live('--devices', 'FP2_DEVICE_08')
        self.assertEqual(result, 1)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(list(self.output.glob('FP2_DEVICE_*.json')), [])
        self.assertTrue(list(self.output.glob('.capture-*.part')))

    def test_existing_file_is_never_overwritten_by_atomic_publication(self):
        existing = self.output / 'capture.json'
        existing.write_bytes(b'original evidence')
        with self.assertRaises(OSError):
            capture.atomic_json(self.output, existing.name, lambda stream: stream.write('new evidence'))
        self.assertEqual(existing.read_bytes(), b'original evidence')

    def test_unwritable_or_low_capacity_output_blocks_all_connections(self):
        with mock.patch.object(safety.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=0)):
            self.assertEqual(self.live()[0], 1)
        with mock.patch.object(safety.os, 'access', return_value=False):
            self.assertEqual(self.live()[0], 1)
        self.factory.assert_not_called()

    def test_output_state_markers_are_preserved_and_refused(self):
        for name in ('status.json', 'FP2_DEVICE_08_last_fetch_dump.json', 'sync.sqlite3', 'error.log'):
            marker = self.output / name
            marker.write_bytes(b'production sentinel')
            self.assertEqual(self.live()[0], 1)
            self.assertEqual(marker.read_bytes(), b'production sentinel')
            marker.unlink()
        self.factory.assert_not_called()

    def test_service_restart_between_checks_blocks_connection(self):
        self.check_service.side_effect = ['STOPPED', 'RUNNING']
        self.assertEqual(self.live('--devices', 'FP2_DEVICE_08')[0], 1)
        self.factory.assert_not_called()

    def test_native_scm_query_uses_query_only_access_and_closes_handles(self):
        api = types.SimpleNamespace(OpenSCManagerW=mock.Mock(return_value=100),
            OpenServiceW=mock.Mock(return_value=200), QueryServiceStatusEx=mock.Mock(),
            CloseServiceHandle=mock.Mock(return_value=True))
        def status(handle, level, buffer, size, needed):
            buffer[1] = 4
            return True
        api.QueryServiceStatusEx.side_effect = status
        with mock.patch.object(safety.sys, 'platform', 'win32'), mock.patch.object(safety.ctypes, 'WinDLL', return_value=api, create=True):
            self.assertEqual(safety.service_state(), 'RUNNING')
        api.OpenSCManagerW.assert_called_once_with(None, None, 1)
        api.OpenServiceW.assert_called_once_with(100, safety.SERVICE_NAME, 4)
        self.assertEqual(api.CloseServiceHandle.call_args_list, [mock.call(200), mock.call(100)])

    def test_service_restarts_during_sdk_preflight_no_connect_occurs(self):
        terminal = self.clients['FP2_DEVICE_08']
        terminal._ZK__sock = mock.Mock()
        def factory(device, timeout):
            self.check_service.return_value = 'RUNNING'
            return terminal
        self.factory.side_effect = factory
        self.assertEqual(self.live('--devices', 'FP2_DEVICE_08')[0], 1)
        self.assertEqual(terminal.calls, [])
        terminal._ZK__sock.close.assert_called_once()

    def test_acl_guard_refuses_broad_or_uninspectable_permissions(self):
        for stdout, code in (('{"safe":false}', 0), ('bad JSON', 0), ('', 1)):
            with mock.patch.object(safety.sys, 'platform', 'win32'), mock.patch.object(safety.subprocess, 'run',
                    return_value=types.SimpleNamespace(stdout=stdout, returncode=code)) as inspect:
                with self.assertRaises(safety.CaptureError):
                    safety.check_directory_acl(self.output)
                self.assertEqual(inspect.call_args.kwargs['env']['BIOMETRIC_CAPTURE_OUTPUT_DIR'], str(self.output))

    def test_known_production_root_is_rejected_even_if_tool_is_copied_elsewhere(self):
        with mock.patch.object(safety, 'PROTECTED_ROOTS', (self.root,)):
            self.assertEqual(self.live()[0], 1)
        self.factory.assert_not_called()

    def test_summary_persistence_failure_is_never_success(self):
        actual = capture.atomic_json
        def failing(directory, name, writer):
            if name.startswith('capture_summary'):
                raise OSError('disk full')
            return actual(directory, name, writer)
        with mock.patch.object(capture, 'atomic_json', side_effect=failing):
            result, report = self.live('--devices', 'FP2_DEVICE_08')
        self.assertEqual(result, 1)
        self.assertEqual(report['status'], 'failed')

    def test_silent_serialization_failure_is_flagged_and_later_records_survive(self):
        original = capture.record_evidence
        self.clients['FP2_DEVICE_08'].records = [punch(), punch(user='second-private')]
        def bad_record(row, index, reference):
            if index == 0:
                raise ValueError('private employee data')
            return original(row, index, reference)
        with mock.patch.object(capture, 'record_evidence', side_effect=bad_record):
            result, report = self.live('--devices', 'FP2_DEVICE_08')
        self.assertEqual(result, 0)
        self.assertEqual(report['devices'][0]['raw_incomplete'], 1)
        self.assertEqual(report['devices'][0]['total_fetched'], 2)
        self.assertEqual(report['devices'][0]['validation_counts']['record_serialization_failed'], 1)
        self.assertNotIn('second-private', self.last_output)

    def test_import_graph_is_sync_and_api_free_even_in_default_dry_run(self):
        script = """import builtins,sys
original=builtins.__import__
forbidden={'erpnext_sync','erpnext_sync_win','local_config','durable_sync','durable_delivery','sync_ledger','requests','sqlite3','zk'}
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in forbidden: raise AssertionError('forbidden import')
    return original(name,*args,**kwargs)
builtins.__import__=guarded
from scripts.capture_device_history_readonly import main
raise SystemExit(main(sys.argv[1:],check_service=lambda:'STOPPED',acl_check=lambda p:None))
"""
        result = subprocess.run([sys.executable, '-c', script, '--config', str(self.config), '--output', str(self.output)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['mode'], 'dry-run')
        self.assertEqual(list(self.output.iterdir()), [])

    def test_no_mutation_method_or_service_control_call_is_in_capture_source(self):
        import ast
        for module in (capture, safety):
            tree = ast.parse(Path(module.__file__).read_text())
            names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            self.assertFalse(names.intersection({'clear_attendance', 'clear_data', 'delete_user', 'set_user',
                'set_time', 'ControlService', 'StartServiceW', 'ChangeServiceConfigW'}))

    def test_sdk_factory_refuses_unpatched_source_and_never_applies_patch(self):
        from scripts import apply_pyzk_speedface_patch as checker
        device = capture.approved_devices(self.config, ['FP2_DEVICE_08'])[0]
        with mock.patch.object(checker, 'validate_environment', return_value=('0.9', self.root / 'base.py', checker.OLD_BLOCK)), \
                mock.patch.object(checker, 'apply_patch') as apply:
            with self.assertRaises(safety.CaptureError):
                capture.safe_sdk_factory(device, 10)
            apply.assert_not_called()

    def test_sdk_factory_uses_existing_connector_without_connecting_or_verbose_output(self):
        from scripts import apply_pyzk_speedface_patch as checker
        device = capture.approved_devices(self.config, ['FP2_DEVICE_08'])[0]
        factory = mock.Mock()
        fake_sdk = types.ModuleType('zk')
        fake_sdk.ZK = factory
        with mock.patch.object(checker, 'validate_environment', return_value=('0.9', self.root / 'base.py', checker.NEW_BLOCK)), \
                mock.patch.dict(sys.modules, {'zk': fake_sdk}):
            client = capture.safe_sdk_factory(device, 10)
        factory.assert_called_once_with('10.0.1.23', port=4378, timeout=10, password=123456, verbose=False, ommit_ping=True)
        client.connect.assert_not_called()
