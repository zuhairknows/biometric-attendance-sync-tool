"""Synthetic HTTPS fixture tests. No real configuration, service, devices or external requests."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

from scripts.phase1_staging_https import Isolation, IsolationError




def setUpModule():
    from windows_powershell_environment import native_modules
    unittest.enterModuleContext(native_modules())

ROOT = Path(__file__).resolve().parents[1]
OPENSSL = shutil.which('openssl') or (r'C:\Program Files\Git\usr\bin\openssl.exe' if os.name == 'nt' else None)
POWERSHELL = shutil.which('powershell.exe')


class StagingHttpsTests(unittest.TestCase):
    @unittest.skipUnless(POWERSHELL and OPENSSL and Path(OPENSSL).is_file(), 'Windows PowerShell and OpenSSL required')
    def test_powershell_native_stderr_exit_codes_and_certificate_validation(self):
        # Load only the reviewed helper definitions, never the directory/ACL bootstrap.
        helpers = (ROOT / 'scripts' / 'prepare_phase1_windows_staging.ps1').read_text().split('$repo =', 1)[0]
        with tempfile.TemporaryDirectory(prefix='FPF-Phase1-Staging-') as directory:
            root = Path(directory)
            (root / 'request.cnf').write_text('[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n')
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            setup = helpers + '\n$openssl = ' + quote(OPENSSL) + '\nSet-Location -LiteralPath ' + quote(root) + '\n'
            generate = """
Invoke-StagingOpenSsl -OpenSslArguments @('req', '-quiet', '-x509', '-newkey', 'rsa:2048',
    '-noenc', '-days', '1', '-config', 'request.cnf', '-subj', '/CN=localhost',
    '-keyout', 'key.pem', '-out', 'cert.pem')
"""

            def run(body):
                script = root / 'test.ps1'
                script.write_text(setup + body)
                return subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                       '-File', str(script)], capture_output=True, text=True, timeout=30)

            success = run(generate + """
Test-StagingCertificate -Certificate 'cert.pem' -PrivateKey 'key.pem' -StageRoot '.'
if ($ErrorActionPreference -ne 'Stop') { throw 'Strict preference was changed.' }
Write-Output 'PASS'
""")
            self.assertEqual(success.returncode, 0, success.stdout + success.stderr)
            self.assertEqual(success.stdout.strip(), 'PASS')
            self.assertEqual(success.stderr, '')
            cases = {
                'nonzero exit': "Invoke-StagingOpenSsl -OpenSslArguments @('x509', '-in', 'missing.pem', '-noout')",
                'invalid key': "Set-Content -LiteralPath bad.pem -Value invalid\nTest-StagingCertificate -Certificate cert.pem -PrivateKey bad.pem -StageRoot '.'",
                'invalid certificate': "Test-StagingCertificate -Certificate bad.pem -PrivateKey key.pem -StageRoot '.'",
                'missing key': "Test-StagingCertificate -Certificate cert.pem -PrivateKey absent.pem -StageRoot '.'",
                'empty key': "New-Item -ItemType File -Path empty.pem | Out-Null\nTest-StagingCertificate -Certificate cert.pem -PrivateKey empty.pem -StageRoot '.'",
                'launch failure': "$openssl = Join-Path (Get-Location) 'absent-openssl.exe'\nInvoke-StagingOpenSsl -OpenSslArguments @('version')",
                'mismatched pair': "Copy-Item key.pem original-key.pem\n" + generate + "Test-StagingCertificate -Certificate cert.pem -PrivateKey original-key.pem -StageRoot '.'",
            }
            for label, body in cases.items():
                with self.subTest(case=label):
                    result = run(body)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn('PRIVATE KEY', result.stdout + result.stderr)
                    self.assertNotIn('Could not', result.stdout + result.stderr)
            strict = run("""
try { Invoke-StagingOpenSsl -OpenSslArguments @('x509', '-in', 'missing.pem', '-noout') }
catch {
    if ($ErrorActionPreference -ne 'Stop') { throw 'Strict preference was changed after failure.' }
    Write-Output 'EXPECTED FAILURE'
    exit 0
}
throw 'OpenSSL failure was ignored.'
""")
            self.assertEqual(strict.returncode, 0, strict.stdout + strict.stderr)
            self.assertEqual(strict.stdout.strip(), 'EXPECTED FAILURE')
            self.assertEqual(strict.stderr, '')

    def test_guard_rejects_external_dns_tcp_udp_process_import_and_files(self):
        root = Path(tempfile.gettempdir()) / 'FPF-Phase1-Staging-unit-only'
        policy = Isolation(root)
        policy.port = 12345
        cases = [('socket.getaddrinfo', ('erp.myfpf.com', 443)),
                 ('socket.connect', (None, ('192.0.2.1', 4370))),
                 ('socket.connect', (None, ('127.0.0.1', 4370))),
                 ('socket.bind', (None, ('0.0.0.0', 12345))),
                 ('socket.__new__', (None, socket.AF_INET, socket.SOCK_DGRAM, 0)),
                 ('socket.sendto', (None, b'no', ('192.0.2.1', 4370))),
                 ('subprocess.Popen', ('must-not-run', [], None, None)),
                 ('os.system', ('must-not-run',)), ('ctypes.dlopen', ('must-not-load',)),
                 ('import', ('local_config',)), ('import', ('zk',)), ('import', ('erpnext_sync',)),
                 ('open', (str(root.parent / 'local_config.py'), 'r', 0)),
                 ('open', (str(root.parent / 'MUST-NOT-CREATE'), 'w', os.O_CREAT | os.O_WRONLY)),
                 ('os.remove', (str(root.parent / 'MUST-NOT-REMOVE'), -1))]
        for event, args in cases:
            with self.subTest(event=event), self.assertRaises(IsolationError):
                policy.audit(event, args)

    def test_guard_allows_only_selected_endpoint_and_staging_writes(self):
        root = Path(tempfile.gettempdir()) / 'FPF-Phase1-Staging-unit-only'
        policy = Isolation(root)
        policy.port = 12345
        policy.audit('socket.connect', (None, ('127.0.0.1', 12345)))
        policy.audit('socket.bind', (None, ('127.0.0.1', 0)))
        policy.audit('open', (str(root / 'synthetic.json'), 'w', os.O_CREAT | os.O_WRONLY))
        self.assertFalse(policy.blocked)

    @unittest.skipUnless(OPENSSL and Path(OPENSSL).is_file(), 'Existing OpenSSL needed for HTTPS fixture')
    def test_verified_https_and_real_audit_hook_in_isolated_process(self):
        with tempfile.TemporaryDirectory(prefix='FPF-Phase1-Staging-') as directory:
            root = Path(directory)
            config = root / 'request.cnf'
            config.write_text('[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n')
            result = subprocess.run([OPENSSL, 'req', '-quiet', '-x509', '-newkey', 'rsa:2048', '-noenc',
                '-days', '1', '-config', str(config), '-subj', '/CN=localhost',
                '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
                '-addext', 'basicConstraints=critical,CA:TRUE',
                '-keyout', str(root / 'localhost-key.pem'), '-out', str(root / 'localhost-cert.pem')],
                capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, 'Synthetic certificate generation failed')
            # Hostile proxy settings prove that the mock never uses environment proxy routing.
            environment = dict(os.environ, HTTPS_PROXY='http://192.0.2.1:9999', HTTP_PROXY='http://192.0.2.1:9999', NO_PROXY='')
            run = subprocess.run([sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts' / 'phase1_staging_https.py'),
                                  '--stage-root', str(root)], capture_output=True, text=True,
                                  env=environment, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = json.loads(run.stdout)
            self.assertEqual(report['status'], 'PASS')
            self.assertTrue(all(report['checks'].values()))
            self.assertEqual(report['mock_records'], 1)
            self.assertFalse(report['service_operations'])
            self.assertFalse(report['production_access'])
            self.assertEqual(report, json.loads((root / 'https-self-test.json').read_text()))
            self.assertFalse((root.parent / 'MUST-NOT-CREATE.txt').exists())

    def test_bootstrap_has_no_service_or_network_configuration_commands(self):
        text = (ROOT / 'scripts' / 'prepare_phase1_windows_staging.ps1').read_text()
        for word in ('Start-Service', 'Stop-Service', 'Restart-Service', 'New-Service', 'Set-Service',
                     'Disable-NetAdapter', 'New-NetFirewallRule', 'Import-Certificate', 'Set-ExecutionPolicy'):
            self.assertNotIn(word, text)
        self.assertIn('4256cd6201b17dcda3f9d078552acf1d3c58b3f0', text)


if __name__ == '__main__':
    unittest.main()
