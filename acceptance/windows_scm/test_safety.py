"""Non-administrative guard tests; never register services, alter ACLs or use ROOT."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('isolated_scm_host', HERE / 'scm_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class SafetyTests(unittest.TestCase):
    def test_exact_candidate_and_fixed_disposable_targets(self):
        self.assertEqual(host.CANDIDATE, '4f20179f5fb10f84a6f86a6ff720f9c2876efc20')
        self.assertEqual(str(host.ROOT), r'C:\FPFPhase2AclAcceptance-A4F20179C01')
        self.assertEqual(host.DB.parent, host.ROOT / 'data')
        self.assertEqual(host.NAME, 'FPFPhase2AclAcceptance_A4F20179C01')
        self.assertEqual(host.ORIGIN, 'https://127.0.0.1:18879')
        self.assertNotIn('ERPNextBiometricPushService', str(host.DB))
        self.assertNotEqual(host.NAME, 'ERPNextBiometricPushService')

    def test_uninstalled_host_refuses_runtime_modes_before_reading_receipt(self):
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('no receipt reads')):
            with self.assertRaisesRegex(RuntimeError, 'installed_harness_path_required'):
                host.installed()

    def test_only_complete_synthetic_payloads_are_allowed(self):
        for number in range(1, 13):
            suffix = str(number).zfill(2)
            value = dict(device_id='SCM_SYNTH_' + suffix, employee_field_value='SCM_EMP_' + suffix,
                         timestamp='2026-10-21 12:00:00', log_type=None, latitude=None, longitude=None)
            self.assertTrue(host.payload_ok(value))
            for changes in ({'device_id': 'FP1_DEVICE_01'}, {'employee_field_value': 'REAL'},
                            {'latitude': 1}, {'secret': 'REAL'}):
                self.assertFalse(host.payload_ok(dict(value, **changes)))

    def test_real_destination_credentials_methods_and_redirects_never_create_transport(self):
        good = dict(device_id='SCM_SYNTH_01', employee_field_value='SCM_EMP_01',
                    timestamp='2026-10-21 12:00:00', log_type=None, latitude=None, longitude=None)
        auth = {'Authorization': 'token ' + host.SYNTHETIC + ':' + host.SYNTHETIC}
        cases = [('POST','https://erp.myfpf.com' + host.METHOD,auth,False),
                 ('POST','https://192.0.2.10' + host.METHOD,auth,False),
                 ('GET',host.ORIGIN + host.METHOD,auth,False),
                 ('POST',host.ORIGIN + host.METHOD,{'Authorization':'token REAL:REAL'},False),
                 ('POST',host.ORIGIN + host.METHOD,auth,True)]
        with mock.patch('http.client.HTTPSConnection', side_effect=AssertionError('no sockets')) as connection:
            for method,url,headers,redirect in cases:
                with self.subTest(url=url, method=method), self.assertRaises(RuntimeError):
                    host.request(method,url,headers=headers,json=good,allow_redirects=redirect)
            connection.assert_not_called()

    def test_socket_guard_rejects_every_nonfixture_destination_without_network(self):
        # Isolate permanent socket monkeypatches in a child process.
        code = """
import importlib.util,socket
s=importlib.util.spec_from_file_location('h',PATH)
h=importlib.util.module_from_spec(s);s.loader.exec_module(h);h.transport_guard()
blocked=0
for address in [('erp.myfpf.com',443),('192.0.2.10',4370),('127.0.0.1',4370),('::1',18879)]:
 try: socket.getaddrinfo(*address)
 except RuntimeError: blocked+=1
 else: raise AssertionError('unexpected destination accepted')
for address in [('192.0.2.10',4370),('127.0.0.1',4370)]:
 with socket.socket() as sock:
  try: sock.connect(address)
  except RuntimeError: blocked+=1
  else: raise AssertionError('unexpected connection')
try: socket.gethostbyname('erp.myfpf.com')
except RuntimeError: blocked+=1
else: raise AssertionError('DNS accepted')
assert blocked==7
print('PASS: blocked before DNS/connect')
""".replace('PATH', repr(str(HERE / 'scm_host.py')))
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code],
                                capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr + result.stdout)

    def test_real_loopback_tls_accepts_only_twelve_synthetic_normal_payloads(self):
        openssl = Path(r'C:\Program Files\Git\usr\bin\openssl.exe')
        self.assertTrue(openssl.is_file(), 'Approved development OpenSSL required for TLS adapter test')
        with tempfile.TemporaryDirectory(prefix='phase2-scm-pack-unit-') as directory:
            root = Path(directory)
            tools = root / 'tools'
            tools.mkdir()
            result = subprocess.run([str(openssl),'req','-x509','-newkey','rsa:2048','-sha256',
                '-nodes','-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1',
                '-keyout',str(tools/'localhost-key.pem'),'-out',str(tools/'localhost-cert.pem')],
                capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0)
            with mock.patch.object(host,'ROOT',root), mock.patch.object(host,'PORT',0):
                server, thread, accepted = host.serve()
                try:
                    with mock.patch.object(host,'PORT',server.server_port), \
                         mock.patch.object(host,'ORIGIN','https://127.0.0.1:'+str(server.server_port)):
                        for number in range(1,13):
                            suffix = str(number).zfill(2)
                            payload = dict(device_id='SCM_SYNTH_'+suffix,
                                employee_field_value='SCM_EMP_'+suffix, timestamp='2026-10-21 12:00:00',
                                log_type=None,latitude=None,longitude=None)
                            response = host.request('POST',host.ORIGIN+host.METHOD,
                                headers={'Authorization':'token '+host.SYNTHETIC+':'+host.SYNTHETIC},
                                json=payload,allow_redirects=False)
                            self.assertEqual(response.status_code,200)
                        self.assertEqual(len(accepted),12)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(5)
                    self.assertFalse(thread.is_alive())

    def test_monitor_requires_the_approved_actual_token(self):
        handle = mock.Mock()
        with mock.patch.object(host,'installed',return_value={'monitor_sid':'S-1-5-21-1-2-3-1001'}), \
             mock.patch.object(host,'token',return_value=(handle,host.SYSTEM)):
            with self.assertRaisesRegex(RuntimeError,'approved_actual_monitor_token_required'):
                host.monitor()
        handle.Close.assert_called_once()

    def test_denied_access_probe_never_performs_mutations(self):
        # A successful write-right open must fail the test; handles are still closed.
        api = mock.Mock()
        api.CreateFileW.return_value = 123
        with mock.patch('ctypes.WinDLL',return_value=api):
            with self.assertRaisesRegex(RuntimeError,'access_probe_failed'):
                host.access_probe(Path('SYNTHETIC-DO-NOT-OPEN'),2,False)
        api.CloseHandle.assert_called_once_with(123)

    def test_both_sources_parse_and_preflight_contains_no_direct_mutator(self):
        ast.parse((HERE / 'scm_host.py').read_text(encoding='utf-8'))
        # The PowerShell parser is used only as a parser, never dot-sourcing the driver.
        script = r"""
$p=$null;$e=$null
$a=[Management.Automation.Language.Parser]::ParseFile('SCRIPT',[ref]$p,[ref]$e)
if ($e.Count) { throw ($e | Out-String) }
$f=$a.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Preflight'},$true)
$names=@($f.Body.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true) | ForEach-Object GetCommandName)
$bad=@($names | Where-Object { $_ -in @('Set-Acl','New-Service','Set-Service','Start-Service','Stop-Service','Remove-Item','New-Item','Copy-Item') })
if ($bad.Count) { throw 'mutating preflight' }
'PARSE_AND_PREFLIGHT_COMMAND_CHECK_PASS'
""".replace('SCRIPT',str(HERE / 'Invoke-Acceptance.ps1').replace("'","''"))
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_native_argument_forwarding_in_windows_powershell_51(self):
        script = r"""
$t=$null;$e=$null
$a=[Management.Automation.Language.Parser]::ParseFile('DRIVER',[ref]$t,[ref]$e)
$names=@('Assert-True','Native','Windows-Argument')
$defs=@($a.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $names},$true) | ForEach-Object { $_.Extent.Text })
. ([ScriptBlock]::Create(($defs -join [Environment]::NewLine)))
$r=Native 'PYTHON' @('-I','-B','-c','import json; print(json.dumps({"base":"ok","spaces":"one two"}))')
$v=$r | ConvertFrom-Json
if ($v.base -ne 'ok' -or $v.spaces -ne 'one two') { throw 'argument transport failed' }
'ARGUMENT_TRANSPORT_PASS'
""".replace('DRIVER',str(HERE/'Invoke-Acceptance.ps1').replace("'","''")).replace('PYTHON',sys.executable.replace("'","''"))
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_no_caller_supplied_service_path_destination_or_credentials(self):
        text=(HERE/'Invoke-Acceptance.ps1').read_text(encoding='utf-8')
        parameters=text.split('Set-StrictMode',1)[0]
        for forbidden in ('$Root', '$Service', '$Database', '$Destination', '$Credentials'):
            self.assertNotIn(forbidden,parameters)
        self.assertNotIn('Restart-Computer',text)
        self.assertNotIn('shutdown.exe',text)
        self.assertEqual(text.count('Remove-Item -LiteralPath $Root -Recurse -Force'),1)
        self.assertIn('unrecognized_cleanup_entry',text)
        self.assertIn('verify-evidence',text)
        self.assertIn('evidence_hash_mismatch',(HERE/'harness_safety.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
