"""Synthetic files and mocked Windows observations only; never query a service."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import types
import unittest
from unittest import mock

from scripts import diagnose_windows_integration as diagnostic


CONFIG = """
ERPNEXT_API_KEY = 'SECRET_KEY_SENTINEL'
ERPNEXT_API_SECRET = 'SECRET_TOKEN_SENTINEL'
ERPNEXT_URL = 'https://erp.myfpf.com'
ERPNEXT_VERSION = 15
DURABLE_SYNC_ENABLED = False
devices = [
 {'device_id':'FP2_DEVICE_08', 'password':'SECRET_DEVICE_SENTINEL', 'punch_direction':'AUTO'},
 {'device_id':'FP2_DEVICE_11', 'punch_direction':None}
]
"""
REPO = Path(__file__).resolve().parents[1]
REVIEWED_REVISION = '478be326ff35fbc8a62a150866eb976b6a658544'


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        for name in diagnostic.REVIEWED:
            # The fixture represents a reviewed deployment, never whichever branch is open.
            data = diagnostic.subprocess.run(['git', 'show', REVIEWED_REVISION + ':' + name],
                cwd=REPO, capture_output=True, check=True).stdout
            self.assertEqual(diagnostic.digest(data.replace(b'\r\n', b'\n')), diagnostic.REVIEWED[name])
            (self.root / name).write_bytes(data)
        (self.root / 'local_config.py').write_text(CONFIG, encoding='utf-8')
        self.observed = {
            'state': 'Running', 'pid': 123, 'started_utc': '2020-01-01T00:00:00Z',
            'registration_matches_selected_root': True,
            'process_command_matches_selected_root': True,
            'timezone_id': 'Arab Standard Time', 'utc_offset_minutes': 180,
            'override_flags': {k: {'machine': False, 'service': False, 'diagnostic_process': False}
                               for k in ('PYTHONPATH', 'PYTHONHOME', 'TZ')},
        }

    def policy(self, text=CONFIG):
        return diagnostic.config_policy(text.encode())

    def diagnose(self):
        return diagnostic.diagnose(self.root, lambda: copy.deepcopy(self.observed))

    def test_literal_allowlist_and_defaults(self):
        result = self.policy()
        self.assertEqual(result['candidate_sending_path'], 'legacy')
        self.assertTrue(result['candidate_endpoint_method'].startswith('hrms.'))
        self.assertEqual(result['device_punch_values_IN'], [0, 4])
        self.assertEqual(result['device_punch_values_OUT'], [1, 5])
        self.assertEqual(result['device_policies']['FP2_DEVICE_08']['punch_direction'], 'AUTO')
        self.assertIsNone(result['device_policies']['FP2_DEVICE_11']['punch_direction'])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_durable_and_older_endpoint_selection(self):
        result = self.policy(CONFIG.replace('= False', '= True').replace('VERSION = 15', 'VERSION = 13'))
        self.assertEqual(result['candidate_sending_path'], 'durable')
        self.assertTrue(result['candidate_endpoint_method'].startswith('erpnext.'))

    def test_explicit_codes_and_absent_direction(self):
        text = CONFIG.replace(", 'punch_direction':None", '') + '\ndevice_punch_values_IN = [3]\ndevice_punch_values_OUT = [2,3]\n'
        result = self.policy(text)
        self.assertEqual(result['device_punch_values_IN'], [3])
        self.assertEqual(result['device_punch_values_OUT'], [2, 3])
        self.assertFalse(result['device_policies']['FP2_DEVICE_11']['direction_explicit'])

    def test_fixed_directions_and_import_time_defaults(self):
        for direction in ('IN', 'OUT'):
            result = self.policy(CONFIG.replace("'AUTO'", repr(direction)))
            self.assertEqual(result['device_policies']['FP2_DEVICE_08']['punch_direction'], direction)
        result = self.policy(CONFIG.replace('DURABLE_SYNC_ENABLED = False', '').replace('ERPNEXT_VERSION = 15', ''))
        self.assertEqual(result['candidate_sending_path'], 'legacy')
        self.assertTrue(result['candidate_endpoint_method'].startswith('hrms.'))
        self.assertIn('ERPNEXT_VERSION', result['defaulted_settings'])

    def test_source_profile_preserves_timestamp_scope(self):
        result = self.diagnose()
        self.assertEqual(result['timezone']['current_os_utc_offset_minutes'], 180)
        self.assertFalse(result['timezone']['service_process_timezone_verified'])
        self.assertFalse(result['timezone']['device_clock_and_server_conversion_verified'])
        self.assertIn('no explicit UTC conversion', result['timestamp_transformations_in_reviewed_source']['payload'])

    def test_no_local_service_side_effect_commands(self):
        for forbidden in ('Set-Service', 'Start-Service', 'Stop-Service', 'Restart-Service',
                          'Invoke-CimMethod', 'Set-ItemProperty', 'Remove-Item', 'Invoke-WebRequest'):
            self.assertNotIn(forbidden, diagnostic.WINDOWS_QUERY)

    def test_dynamic_content_and_overrides_blocked(self):
        for suffix in ('\nimport os', '\nDURABLE_SYNC_ENABLED=True',
                       '\ndevices[0]["punch_direction"]="IN"',
                       '\nERPNEXT_URL = get_secret()', '\nexec("SECRET_EXEC_SENTINEL")',
                       '\nif True:\n devices=[]', '\n__getattr__ = lambda key: None'):
            with self.subTest(suffix=suffix):
                with self.assertRaises(diagnostic.DiagnosticError):
                    self.policy(CONFIG + suffix)

    def test_invalid_and_secret_values_never_echoed(self):
        texts = [CONFIG.replace('https://erp.myfpf.com', 'https://SECRET:password@example.invalid'),
                 CONFIG.replace("'AUTO'", "'SECRET_DIRECTION'"),
                 CONFIG + '\ndevice_punch_values_IN=[True]',
                 CONFIG.replace('ENABLED = False', 'ENABLED = 1'),
                 CONFIG.replace('VERSION = 15', 'VERSION = True'),
                 CONFIG + '\ndevice_punch_values_OUT=[256]']
        for text in texts:
            with self.subTest(text_hash=diagnostic.digest(text.encode())):
                with self.assertRaises(diagnostic.DiagnosticError) as exc:
                    self.policy(text)
                self.assertNotIn('SECRET', str(exc.exception))

    def test_duplicate_missing_or_malformed_devices_blocked(self):
        for text in (CONFIG.replace('FP2_DEVICE_11', 'FP2_DEVICE_08'),
                     CONFIG.replace('FP2_DEVICE_11', 'OTHER_DEVICE'),
                     CONFIG.replace("'punch_direction':'AUTO'", "'punch_direction':'AUTO', 'punch_direction':'IN'")):
            with self.assertRaises(diagnostic.DiagnosticError):
                self.policy(text)

    def test_read_only_and_no_integration_import_or_network(self):
        original = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with mock.patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             mock.patch.object(diagnostic.subprocess, 'run', side_effect=AssertionError('unexpected process')), \
             mock.patch.object(Path, 'write_text', side_effect=AssertionError('writes forbidden')), \
             mock.patch.object(Path, 'write_bytes', side_effect=AssertionError('writes forbidden')):
            result = self.diagnose()
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertFalse(result['loaded_runtime_configuration_verified'])
        self.assertTrue(result['candidate_files_changed_since_process_start'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('registration', result['service_observation'])
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_unreviewed_source_withholds_semantics_without_executing(self):
        marker = self.root / 'MUST_NOT_EXIST'
        with (self.root / 'erpnext_sync.py').open('a') as stream:
            stream.write('\nraise RuntimeError("SECRET_CODE_SENTINEL")\n')
        result = self.diagnose()
        self.assertNotIn('on_disk_candidate_policy', result)
        self.assertNotIn('timestamp_transformations_in_reviewed_source', result)
        self.assertFalse(marker.exists())
        self.assertNotIn('SECRET', json.dumps(result))

    def test_hardened_worktree_does_not_inherit_old_source_attestation(self):
        for name in diagnostic.REVIEWED:
            (self.root / name).write_bytes((REPO / name).read_bytes())
        result = self.diagnose()
        self.assertIn('SOURCE_DIFFERS_FROM_REVIEWED_PROFILE_POLICY_WITHHELD', result['findings'])
        self.assertNotIn('on_disk_candidate_policy', result)
        self.assertNotIn('timestamp_transformations_in_reviewed_source', result)
        self.assertFalse(result['loaded_runtime_configuration_verified'])

    def test_crlf_and_lf_are_both_reviewed(self):
        path = self.root / 'erpnext_sync.py'
        path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
        self.assertIn('on_disk_candidate_policy', self.diagnose())

    def test_durable_sources_checked_only_when_selected(self):
        (self.root / 'local_config.py').write_text(CONFIG.replace('= False', '= True'))
        result = self.diagnose()
        self.assertIn('punch_normalization.py', result['sources'])
        self.assertEqual(result['on_disk_candidate_policy']['candidate_sending_path'], 'durable')
        (self.root / 'durable_delivery.py').write_text('raise RuntimeError()')
        self.assertNotIn('on_disk_candidate_policy', self.diagnose())

    def test_source_or_config_change_during_observation_blocks(self):
        for name in ('local_config.py', 'erpnext_sync.py'):
            with self.subTest(name=name):
                calls = []
                def observer():
                    calls.append(1)
                    if len(calls) == 2:
                        with (self.root / name).open('a') as f:
                            f.write('\n# changed')
                    return copy.deepcopy(self.observed)
                with self.assertRaises(diagnostic.DiagnosticError):
                    diagnostic.diagnose(self.root, observer)

    def test_service_change_during_observation_blocks(self):
        changed = dict(self.observed, pid=999)
        with self.assertRaises(diagnostic.DiagnosticError):
            diagnostic.diagnose(self.root, mock.Mock(side_effect=[self.observed, changed]))

    def test_override_presence_and_ambiguity_remain_visible(self):
        (self.root / 'sitecustomize.py').write_text('raise RuntimeError("not imported")')
        self.observed['override_flags']['PYTHONPATH']['service'] = True
        self.observed['process_command'] = 'SECRET_COMMAND_SENTINEL'
        self.observed['process_command_matches_selected_root'] = False
        result = self.diagnose()
        self.assertTrue(result['environment_override_presence']['PYTHONPATH']['service'])
        self.assertTrue(result['alternate_local_import_artifacts_present'])
        self.assertFalse(result['process_command_matches_selected_root'])
        self.assertNotIn('SECRET', json.dumps(result))

    @unittest.skipUnless(diagnostic.os.name == 'nt', 'Windows path syntax')
    def test_command_parser_rejects_flags_wrappers_and_alternate_scripts(self):
        # Execute ONLY the pure parser prefix with synthetic strings, never CIM.
        prefix = diagnostic.WINDOWS_QUERY.split('$s = Get-CimInstance')[0]
        commands = prefix + r'''
$command = '"C:\Trusted Python\python.exe" "' + (Join-Path $expectedRoot 'erpnext_sync_win.py') + '"'
@((Test-ReviewedLauncher $command),
  (Test-ReviewedLauncher ($command + ' SECRET_ARG')),
  (Test-ReviewedLauncher ($command.Replace('python.exe','cmd.exe'))),
  (Test-ReviewedLauncher ($command.Replace('erpnext_sync_win.py','other.py'))),
  (Test-ReviewedLauncher ($command.Substring(0,$command.Length-1)))) | ConvertTo-Json -Compress
'''
        environment = os.environ.copy()
        environment['FPF_DIAGNOSTIC_ROOT'] = str(self.root)
        result = diagnostic.subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                            '-Command', commands], env=environment,
                                           capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), [True, False, False, False, False])
        self.assertNotIn(b'SECRET', result.stdout)

    def test_local_query_permission_failure_is_sanitized(self):
        with mock.patch.object(diagnostic.os, 'name', 'nt'), \
             mock.patch('ctypes.windll', create=True) as windows, \
             mock.patch.object(diagnostic.subprocess, 'run') as run:
            windows.kernel32.GetSystemDirectoryW.return_value = 1
            run.return_value = types.SimpleNamespace(returncode=1, stdout=b'', stderr=b'SECRET_STDERR')
            with self.assertRaises(diagnostic.DiagnosticError) as exc:
                diagnostic.service_snapshot(self.root)
            self.assertEqual(str(exc.exception), 'LOCAL_SERVICE_QUERY_FAILED')
            self.assertEqual(run.call_args.kwargs['timeout'], 30)
            self.assertIn('-NoProfile', run.call_args.args[0])
            self.assertNotIn(str(self.root), ' '.join(run.call_args.args[0]))
            self.assertEqual(run.call_args.kwargs['env']['FPF_DIAGNOSTIC_ROOT'], str(self.root))

    @unittest.skipUnless(diagnostic.os.name == 'nt', 'PowerShell transport')
    def test_powershell_transport_does_not_return_secret_command_lines(self):
        # Shadow all service/registry/timezone cmdlets inside the disposable test
        # PowerShell. These functions return synthetic objects, not host records.
        fake_commands = r'''
function Get-CimInstance {
    param($ClassName, $Filter)
    if ($ClassName -eq 'Win32_Service') {
        return [pscustomobject]@{State='Running'; ProcessId=123; PathName='SECRET_REGISTRATION_SENTINEL'}
    }
    return [pscustomobject]@{CommandLine='SECRET_PROCESS_SENTINEL'; CreationDate=[datetime]::UtcNow}
}
function Get-Item {
    param($LiteralPath)
    $key = New-Object PSObject
    $key | Add-Member ScriptMethod GetValue { return @('TZ=SECRET_ENV_SENTINEL') }
    return $key
}
function Get-TimeZone { return [TimeZoneInfo]::Utc }
'''
        environment = os.environ.copy()
        environment['FPF_DIAGNOSTIC_ROOT'] = str(self.root)
        result = diagnostic.subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                            '-Command', fake_commands + diagnostic.WINDOWS_QUERY],
                                           env=environment, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(b'SECRET', result.stdout)
        response = json.loads(result.stdout)
        self.assertFalse(response['registration_matches_selected_root'])
        self.assertFalse(response['process_command_matches_selected_root'])
        self.assertTrue(response['override_flags']['TZ']['service'])
        self.assertNotIn('registration', response)
        self.assertNotIn('process_command', response)

    def test_checkout_revision_detached_loose_and_packed(self):
        git = self.root / '.git'
        git.mkdir()
        commit = 'a' * 40
        head = git / 'HEAD'
        head.write_text(commit)
        self.assertEqual(diagnostic.checkout_revision(self.root)['commit'], commit)
        head.write_text('ref: refs/heads/main')
        reference = git / 'refs/heads/main'
        reference.parent.mkdir(parents=True)
        reference.write_text(commit)
        self.assertEqual(diagnostic.checkout_revision(self.root)['commit'], commit)
        reference.unlink()
        (git / 'packed-refs').write_text(commit + ' refs/heads/main\n')
        result = self.diagnose()['deployed_checkout_revision']
        self.assertEqual(result['commit'], commit)
        self.assertFalse(result['loaded_service_revision_verified'])
        self.assertFalse(result['files_match_commit_verified'])

    def test_invalid_or_external_git_metadata_is_not_exposed(self):
        self.assertIsNone(diagnostic.checkout_revision(self.root)['commit'])
        git = self.root / '.git'
        git.mkdir()
        for value in ('ref: ../SECRET_PATH', 'SECRET_HEAD_SENTINEL'):
            (git / 'HEAD').write_text(value)
            result = diagnostic.checkout_revision(self.root)
            self.assertIsNone(result['commit'])
            self.assertNotIn('SECRET', json.dumps(result))

    def test_checkout_revision_change_blocks(self):
        with mock.patch.object(diagnostic, 'checkout_revision', side_effect=[{'commit': 'a'*40}, {'commit': 'b'*40}]):
            with self.assertRaisesRegex(diagnostic.DiagnosticError, 'CHECKOUT_REVISION_CHANGED_DURING_READ'):
                self.diagnose()

    def test_main_suppresses_tracebacks_and_raw_errors(self):
        flags = types.SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)
        for error in (SyntaxError('SECRET_CONFIG_LINE'), diagnostic.DiagnosticError('FIXED_CODE')):
            capture = io.StringIO()
            with mock.patch.object(diagnostic.sys, 'flags', flags), \
                 mock.patch.object(diagnostic, 'diagnose', side_effect=error), \
                 contextlib.redirect_stdout(capture):
                self.assertEqual(diagnostic.main(['--integration-root', str(self.root)]), 2)
            self.assertNotIn('SECRET', capture.getvalue())
            self.assertEqual(json.loads(capture.getvalue())['status'], 'DIAGNOSTIC_BLOCKED')

    def test_source_size_limit_and_safe_startup_flags(self):
        with mock.patch.object(diagnostic, 'MAX_SOURCE', 1):
            with self.assertRaises(diagnostic.DiagnosticError):
                self.diagnose()
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            self.assertEqual(diagnostic.main(['--integration-root', str(self.root)]), 2)
        self.assertNotIn('SECRET', capture.getvalue())

    def settings(self, text=CONFIG):
        return diagnostic.inspect_config(text.encode('utf-8'))

    def settings_report(self):
        return diagnostic.diagnose(self.root, lambda: copy.deepcopy(self.observed), settings_only=True)

    def test_sanitized_settings_allowlist_and_no_defaults(self):
        result = self.settings()
        self.assertEqual(set(result), {
            'FP2_DEVICE_08.punch_direction', 'FP2_DEVICE_11.punch_direction',
            'device_punch_values_IN', 'device_punch_values_OUT', 'ERPNEXT_VERSION',
            'erpnext_url_matches_approved_site'})
        self.assertEqual(result['FP2_DEVICE_08.punch_direction']['value'], 'AUTO')
        self.assertIsNone(result['FP2_DEVICE_11.punch_direction']['value'])
        self.assertEqual(result['device_punch_values_IN']['state'], 'absent')
        self.assertFalse(result['device_punch_values_IN']['present'])
        self.assertNotIn('value', result['device_punch_values_IN'])
        self.assertTrue(result['erpnext_url_matches_approved_site']['value'])
        self.assertNotIn('https://', json.dumps(result))
        self.assertNotIn('SECRET', json.dumps(result))

    def test_sanitized_explicit_codes_and_version(self):
        result = self.settings(CONFIG + '\ndevice_punch_values_IN = (0,4)\ndevice_punch_values_OUT = [1,5,5]\n')
        self.assertEqual(result['device_punch_values_IN']['value'], [0, 4])
        self.assertEqual(result['device_punch_values_OUT']['value'], [1, 5, 5])
        self.assertEqual(result['ERPNEXT_VERSION']['value'], 15)
        self.assertTrue(result['ERPNEXT_VERSION']['present'])

    def test_sanitized_all_absent_and_missing_direction_distinction(self):
        self.assertTrue(all(v['state'] == 'absent' for v in self.settings('').values()))
        result = self.settings(CONFIG.replace(", 'punch_direction':None", ''))
        self.assertEqual(result['FP2_DEVICE_11.punch_direction']['reason'], 'DIRECTION_FIELD_ABSENT')
        result = self.settings(CONFIG.replace('FP2_DEVICE_11', 'OTHER_DEVICE'))
        self.assertEqual(result['FP2_DEVICE_11.punch_direction']['reason'], 'DEVICE_ENTRY_ABSENT')

    def test_sanitized_fixed_directions(self):
        for value in ('IN', 'OUT'):
            result = self.settings(CONFIG.replace("'AUTO'", repr(value)))
            self.assertEqual(result['FP2_DEVICE_08.punch_direction']['value'], value)

    def test_sanitized_dynamic_names_not_resolved(self):
        result = self.settings(CONFIG.replace("'punch_direction':'AUTO'", "'punch_direction':MODE")
                               + '\ndevice_punch_values_IN = CODES\n')
        self.assertEqual(result['FP2_DEVICE_08.punch_direction']['state'], 'dynamic')
        self.assertNotIn('value', result['FP2_DEVICE_08.punch_direction'])
        self.assertEqual(result['device_punch_values_IN']['state'], 'dynamic')
        self.assertEqual(result['FP2_DEVICE_11.punch_direction']['state'], 'literal')

    def test_sanitized_module_execution_and_overrides_unresolved(self):
        for suffix in ('\nimport os', '\nfrom overrides import *',
                       '\nERPNEXT_API_SECRET = get_secret()',
                       '\nif flag:\n ERPNEXT_VERSION=15',
                       '\ndevices[0]["punch_direction"]="IN"',
                       '\ndevices.append(extra)', '\ndel devices',
                       '\ndef __getattr__(name):\n return 15',
                       '\nexec("SECRET_EXEC_SENTINEL")'):
            with self.subTest(case=diagnostic.digest(suffix.encode())):
                result = self.settings(CONFIG + suffix)
                self.assertTrue(all(v['confidence'] == 'unresolved' and 'value' not in v for v in result.values()))
                self.assertNotIn('SECRET', json.dumps(result))

    def test_sanitized_duplicate_assignments_and_aliases_unresolved(self):
        for suffix, field in (('\nERPNEXT_VERSION=14', 'ERPNEXT_VERSION'),
                              ('\ndevice_punch_values_IN = device_punch_values_OUT = [1]', 'device_punch_values_IN'),
                              ('\ndevices = []', 'FP2_DEVICE_08.punch_direction')):
            result = self.settings(CONFIG + suffix)
            self.assertEqual(result[field]['state'], 'ambiguous')
            self.assertNotIn('value', result[field])

    def test_sanitized_device_ambiguity_is_not_arbitrarily_resolved(self):
        cases = [CONFIG.replace('FP2_DEVICE_11', 'FP2_DEVICE_08'),
                 CONFIG.replace("'punch_direction':'AUTO'", "'punch_direction':'AUTO','punch_direction':'IN'"),
                 CONFIG.replace("'device_id':'FP2_DEVICE_11'", "'device_id':UNKNOWN_DEVICE"),
                 CONFIG.replace("'punch_direction':'AUTO'", "'punch_direction':'AUTO', OTHER_KEY:None")]
        for text in cases:
            result = self.settings(text)['FP2_DEVICE_08.punch_direction']
            self.assertEqual(result['state'], 'ambiguous')
            self.assertNotIn('value', result)

    def test_sanitized_invalid_literals_and_url_credentials_withheld(self):
        text = (CONFIG.replace("'AUTO'", "'SECRET_DIRECTION'")
                .replace('https://erp.myfpf.com', 'https://SECRET_KEY:SECRET_TOKEN@example.invalid')
                .replace('VERSION = 15', "VERSION = 'SECRET_VERSION'"))
        result = self.settings(text + "\ndevice_punch_values_IN=['SECRET_CODE']\n")
        self.assertFalse(result['erpnext_url_matches_approved_site']['value'])
        for field in ('FP2_DEVICE_08.punch_direction', 'ERPNEXT_VERSION', 'device_punch_values_IN'):
            self.assertEqual(result[field]['state'], 'invalid_literal')
            self.assertNotIn('value', result[field])
        self.assertNotIn('SECRET', json.dumps(result))
        for value in ('[True]', '[256]', "{'token':'SECRET_DICT'}"):
            self.assertEqual(self.settings(CONFIG + '\ndevice_punch_values_OUT=' + value)
                             ['device_punch_values_OUT']['state'], 'invalid_literal')

    def test_sanitized_code_never_executes_and_never_writes(self):
        path = self.root / 'MUST_NOT_EXIST'
        text = CONFIG + '\nopen(' + repr(str(path)) + ', "w").write("SECRET")\n'
        (self.root / 'local_config.py').write_text(text)
        original = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with mock.patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             mock.patch.object(diagnostic.subprocess, 'run', side_effect=AssertionError('unexpected process')), \
             mock.patch.object(Path, 'write_text', side_effect=AssertionError('writes forbidden')), \
             mock.patch.object(Path, 'write_bytes', side_effect=AssertionError('writes forbidden')):
            result = self.settings_report()
        self.assertFalse(path.exists())
        self.assertEqual(original, {p.name:p.read_bytes() for p in self.root.iterdir()})
        self.assertNotIn('SECRET', json.dumps(result))

    def test_sanitized_report_confidence_keeps_source_trace_checks(self):
        result = self.settings_report()
        self.assertEqual(set(result), {'schema', 'settings', 'confidence'})
        confidence = result['confidence']
        self.assertFalse(confidence['loaded_runtime_configuration_verified'])
        self.assertEqual(confidence['source_traceability']['matched_configuration_loader_profile'],
                         '478be326ff35fbc8a62a150866eb976b6a658544')
        self.assertIn('sha256', confidence['source_traceability']['sources']['erpnext_sync.py'])
        self.assertTrue(confidence['checks']['registration_matches_selected_root'])
        self.assertTrue(confidence['checks']['candidate_files_changed_since_process_start'])
        self.assertNotIn('https://', json.dumps(result))
        self.assertNotIn('candidate_sending_path', json.dumps(result))

    def test_sanitized_production_profile_uses_actual_reviewed_blobs(self):
        commit = 'a23b8112fd1b87f3d2bb10f798b45e150aadcd7a'
        for name in diagnostic.PRODUCTION_REVIEWED:
            data = diagnostic.subprocess.run(['git', 'show', commit + ':' + name], cwd=REPO,
                                             capture_output=True, check=True).stdout
            self.assertEqual(diagnostic.digest(data.replace(b'\r\n', b'\n')), diagnostic.PRODUCTION_REVIEWED[name])
            (self.root / name).write_bytes(data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        result = self.settings_report()
        self.assertEqual(result['confidence']['source_traceability']['matched_configuration_loader_profile'], commit)
        self.assertEqual(result['settings']['FP2_DEVICE_08.punch_direction']['value'], 'AUTO')

    def test_sanitized_unknown_profile_not_certified(self):
        with (self.root / 'erpnext_sync.py').open('a') as f:
            f.write('\n# unknown revision')
        result = self.settings_report()
        self.assertIsNone(result['confidence']['source_traceability']['matched_configuration_loader_profile'])
        self.assertIn('CONFIGURATION_LOADER_SOURCE_UNREVIEWED', result['confidence']['checks']['findings'])
        self.assertFalse(result['confidence']['loaded_runtime_configuration_verified'])

    def test_sanitized_config_change_still_blocks(self):
        observations = []
        def observer():
            observations.append(1)
            if len(observations) == 2:
                with (self.root / 'local_config.py').open('a') as f:
                    f.write('\n# changed')
            return copy.deepcopy(self.observed)
        with self.assertRaisesRegex(diagnostic.DiagnosticError, 'CONFIG_CHANGED_DURING_READ'):
            diagnostic.diagnose(self.root, observer, settings_only=True)

    def test_sanitized_cli_and_syntax_error_suppression(self):
        # Preserve other interpreter flags used by stdlib internals; only model
        # the three required startup flags rather than replacing the whole API.
        flags = types.SimpleNamespace(**{key: getattr(diagnostic.sys.flags, key)
                                       for key in dir(diagnostic.sys.flags)
                                       if not key.startswith('_')})
        flags.isolated = flags.no_site = flags.dont_write_bytecode = 1
        for text in (CONFIG, "ERPNEXT_API_SECRET = 'SECRET_BAD_SYNTAX"):
            (self.root / 'local_config.py').write_text(text)
            output = io.StringIO()
            with mock.patch.object(diagnostic.sys, 'flags', flags), \
                 mock.patch.object(diagnostic, 'service_snapshot', return_value=copy.deepcopy(self.observed)), \
                 contextlib.redirect_stdout(output):
                code = diagnostic.main(['--integration-root', str(self.root), '--settings-only'])
            self.assertNotIn('SECRET', output.getvalue())
            self.assertNotIn('https://', output.getvalue())
            self.assertEqual(code, 0 if text == CONFIG else 2)


if __name__ == '__main__':
    unittest.main()
