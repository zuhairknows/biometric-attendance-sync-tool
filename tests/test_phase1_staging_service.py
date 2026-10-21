"""Compile and test the standalone probe; never register or operate a service."""
import os
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import sys
import unittest




def setUpModule():
    from windows_powershell_environment import native_modules
    unittest.enterModuleContext(native_modules())

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts' / 'phase1_staging_service.cs'
CSC = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
OPENSSL = Path(r'C:\Program Files\Git\usr\bin\openssl.exe')
POWERSHELL = shutil.which('powershell.exe')


class ServiceProbeTests(unittest.TestCase):
    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
    def test_readonly_finalization_against_synthetic_retained_evidence(self):
        with tempfile.TemporaryDirectory(prefix='FPF-finalizer-test-') as directory:
            root = Path(directory) / ('FPF-Phase1-Staging-' + '2' * 32)
            root.mkdir()
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-File',
                str(ROOT / 'tests/fixtures/phase1_step4_finalize_test.ps1'),
                '-ScriptsRoot', str(ROOT / 'scripts'), '-Root', str(root)],
                capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS: read-only finalization', result.stdout)
            self.assertEqual(result.stderr, '')

    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
    def test_redirected_process_completion_and_strict_failure_cases(self):
        with tempfile.TemporaryDirectory(prefix='FPF-process-completion-') as directory:
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            code = r"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(RUNNER,[ref]$tokens,[ref]$errors)
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Wait-StagingMockCompletion'},$true)
. ([ScriptBlock]::Create($function.Extent.Text))
foreach ($exit in @(0,7)) {
    $process=Start-Process -FilePath $env:ComSpec -ArgumentList "/c exit $exit" -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path TEMP_PATH "$exit-out.txt") -RedirectStandardError (Join-Path TEMP_PATH "$exit-err.txt")
    $handle=$process.Handle
    $failed=$false
    try { $result=Wait-StagingMockCompletion $process 5000 }
    catch { $failed=$true; if (-not $_.Exception.Message.Contains('native code 7')) { throw } }
    if (($exit -eq 0 -and ($failed -or $result -ne 0)) -or ($exit -eq 7 -and -not $failed)) { throw 'Incorrect completion result' }
    $process.Dispose()
}
foreach ($case in @(@{Finished=$false;Code=0;Message='timed out'},@{Finished=$true;Code=$null;Message='unavailable'})) {
    $fake=[pscustomobject]@{Finished=$case.Finished;ExitCode=$case.Code}
    $fake | Add-Member ScriptMethod WaitForExit { param($timeout) if ($null -ne $timeout) { return $this.Finished } }
    $failed=$false
    try { Wait-StagingMockCompletion $fake 1 | Out-Null } catch { $failed=$true; if (-not $_.Exception.Message.Contains($case.Message)) { throw } }
    if (-not $failed) { throw 'Timeout/null exit code accepted' }
}
Write-Output 'PASS'
""".replace('RUNNER', quote(ROOT / 'scripts/verify_phase1_staging_service.ps1')).replace('TEMP_PATH', quote(directory))
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', code],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')
            self.assertEqual(result.stderr, '')

    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
    def test_finalizer_syntax_and_no_mutation_commands(self):
        path = ROOT / 'scripts/finalize_phase1_staging_service.ps1'
        source = path.read_text()
        for forbidden in ('Start-Service', 'Stop-Service', 'Start-Process', 'New-Item', 'Set-Acl', 'Set-Content',
                          'New-NetFirewallRule', 'Invoke-CimMethod', '[StagingRegistration]::Create', '::WriteAll'):
            self.assertNotIn(forbidden, source)
        quote = "'" + str(path).replace("'", "''") + "'"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command',
            "$tokens=$null;$errors=$null;[Management.Automation.Language.Parser]::ParseFile(" + quote +
            ",[ref]$tokens,[ref]$errors)|Out-Null;if($errors.Count){$errors|ForEach-Object Message;exit 1}"],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
    def test_resume_artifact_validation_with_mocked_registration(self):
        with tempfile.TemporaryDirectory(prefix='FPF-Step4-resume-unit-') as directory:
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-File',
                str(ROOT / 'tests/fixtures/phase1_step4_resume_test.ps1'),
                '-Runner', str(ROOT / 'scripts/verify_phase1_staging_service.ps1'),
                '-ReviewedSourcePath', str(SOURCE), '-Root', directory],
                capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS: frozen legacy artifacts', result.stdout)
            self.assertEqual(result.stderr, '')
    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
    def test_runner_syntax_and_reused_function_import_without_execution(self):
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        code = r"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(RUNNER,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Syntax error' }
$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Import-ReviewedFunctions'},$true)
. ([ScriptBlock]::Create($function.Extent.Text))
Import-ReviewedFunctions BOUNDARY @('Test-PrivateStagingDirectory','Test-StagingFirewallRule')
Import-ReviewedFunctions BOOTSTRAP @('Invoke-StagingOpenSsl')
foreach ($name in @('Test-PrivateStagingDirectory','Test-StagingFirewallRule','Invoke-StagingOpenSsl')) {
    if (-not (Get-Command -Name $name -CommandType Function -ErrorAction Stop)) { throw 'Helper function unavailable' }
}
Write-Output 'PASS'
""".replace('RUNNER', quote(ROOT / 'scripts/verify_phase1_staging_service.ps1')).replace(
            'BOUNDARY', quote(ROOT / 'scripts/prepare_phase1_staging_boundary.ps1')).replace(
            'BOOTSTRAP', quote(ROOT / 'scripts/prepare_phase1_windows_staging.ps1'))
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', code],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')
        self.assertEqual(result.stderr, '')

    def test_no_production_imports_children_or_system_certificate_changes(self):
        source = SOURCE.read_text()
        for forbidden in ('erpnext_sync', 'local_config', 'zk', 'Process.Start', 'Dns.', 'X509Store',
                          'HttpWebRequest', 'WebClient', 'ERPNextBiometricPushService'):
            self.assertNotIn(forbidden, source)
        self.assertIn('FileMode.CreateNew', source)
        self.assertIn('SslProtocols.Tls12', source)
        self.assertIn('IPAddress.Loopback', source)
        runner = (ROOT / 'scripts/verify_phase1_staging_service.ps1').read_text()
        for forbidden in ('ERPNextBiometricPushService', 'New-NetFirewallRule', 'Set-NetFirewall',
                          'Restart-Service', 'Remove-Service', 'local_config'):
            self.assertNotIn(forbidden, runner)
        self.assertIn("Start-Service -Name 'FPFPhase1Staging'", runner)
        self.assertIn("Stop-Service -Name 'FPFPhase1Staging'", runner)
        self.assertIn('"NT SERVICE\\\\FPFPhase1Staging", null)', source)
        self.assertNotIn("StartPassword=''", runner)
        self.assertNotIn('Invoke-CimMethod', runner)
        self.assertEqual(runner.count('[StagingRegistration]::Create($exe)'), 1)
        self.assertIn('[IO.FileMode]::CreateNew', runner)

    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
    def test_native_creation_error_reporting_without_service_creation(self):
        runner = ROOT / 'scripts/verify_phase1_staging_service.ps1'
        quote = "'" + str(runner).replace("'", "''") + "'"
        code = r"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(RUNNER,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Syntax error' }
$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-StagingCreationErrorMessage'},$true)
. ([ScriptBlock]::Create($function.Extent.Text))
foreach ($number in @(5,87,1057,1073)) {
    $native=[ComponentModel.Win32Exception]::new($number)
    $wrapped=[InvalidOperationException]::new('DO-NOT-PRINT-THIS', $native)
    $text=Get-StagingCreationErrorMessage $wrapped
    if (-not $text.Contains("Win32 code=$number;") -or -not $text.Contains($native.Message) -or $text.Contains('DO-NOT-PRINT-THIS')) { throw 'Native error reporting failed' }
}
$text=Get-StagingCreationErrorMessage ([InvalidOperationException]::new('DO-NOT-PRINT-THIS'))
if (-not $text.Contains('native Win32 code unavailable') -or $text.Contains('DO-NOT-PRINT-THIS')) { throw 'Unavailable code was fabricated or raw exception leaked' }
Write-Output 'PASS'
""".replace('RUNNER', quote)
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', code],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')
        self.assertEqual(result.stderr, '')

    @unittest.skipUnless(CSC.is_file() and OPENSSL.is_file() and POWERSHELL, 'Windows .NET/OpenSSL required')
    def test_compilation_endpoint_denials_and_real_local_tls(self):
        with tempfile.TemporaryDirectory(prefix='FPF-Phase1-Staging-service-test-') as directory:
            root = Path(directory)
            dll = root / 'probe.dll'
            compile_result = subprocess.run([str(CSC), '/nologo', '/target:library', '/out:' + str(dll),
                '/reference:System.ServiceProcess.dll', '/reference:System.Web.Extensions.dll', str(SOURCE)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
            config = root / 'request.cnf'
            config.write_text('[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n')
            for arguments in (
                ['req', '-quiet', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1', '-config', str(config),
                 '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
                 '-keyout', str(root / 'localhost-key.pem'), '-out', str(root / 'localhost-cert.pem')],
                ['x509', '-in', str(root / 'localhost-cert.pem'), '-outform', 'DER', '-out', str(root / 'localhost.cer')],
            ):
                result = subprocess.run([str(OPENSSL)] + arguments, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, 'Synthetic certificate preparation failed')
            quote = lambda path: "'" + str(path).replace("'", "''") + "'"
            code = r"""
$ErrorActionPreference='Stop'
Add-Type -Path DLL_PATH
[StagingProbe]::RequireEndpoint('127.0.0.1',12345,12345,$false)
foreach ($hostName in @('stage.invalid','192.0.2.1','::1')) {
    if (-not [StagingProbe]::Rejects($hostName,12345,12345,$false)) { throw 'Endpoint restriction failed' }
}
if (-not [StagingProbe]::Rejects('127.0.0.1',4370,12345,$false)) { throw 'Other port allowed' }
if (-not [StagingProbe]::Rejects('127.0.0.1',12345,12345,$true)) { throw 'UDP allowed' }
if (-not [StagingProbe]::LocalHttps(CERT_PATH,MOCK_PORT)) { throw 'Local HTTPS failed' }
$cert=[Security.Cryptography.X509Certificates.X509Certificate2]::new(CERT_PATH)
if ([StagingProbe]::CheckPeer($cert,$cert,$null,[Net.Security.SslPolicyErrors]::RemoteCertificateNameMismatch)) { throw 'Name mismatch accepted' }
if ([StagingProbe]::CheckPeer($cert,$null,$null,[Net.Security.SslPolicyErrors]::None)) { throw 'Missing certificate accepted' }
[IO.File]::WriteAllText(SID_PATH,'S-1-5-80-1-2-3-4-5')
if ([StagingProbe]::Run(CODE_PATH)) { throw 'Interactive token accepted as staging service' }
$report=Get-Content -LiteralPath RESULT_PATH -Raw | ConvertFrom-Json
if ($report.status -ne 'FAIL' -or $report.checks.dedicated_service_token -ne $false) { throw 'Wrong-token refusal not recorded' }
Write-Output 'PASS'
""".replace('DLL_PATH', quote(dll)).replace('CERT_PATH', quote(root / 'localhost.cer'))
            service_code = root / 'service-boundary/code'
            service_code.mkdir(parents=True)
            data = root / 'service-boundary/data'
            data.mkdir()
            mock = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/phase1_service_local_mock.py'),
                                     '--stage-root', str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                endpoint = service_code / 'mock-endpoint.json'
                deadline = time.monotonic() + 10
                while not endpoint.exists() and mock.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(endpoint.exists(), 'Local mock did not start')
                port = json.loads(endpoint.read_text())['port']
                code = code.replace('MOCK_PORT', str(port)).replace('SID_PATH', quote(service_code / 'service-sid.txt')).replace(
                    'CODE_PATH', quote(service_code)).replace('RESULT_PATH', quote(data / 'step4-result.json'))
                result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', code],
                                        capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), 'PASS')
                self.assertEqual(result.stderr, '')
                stdout, stderr = mock.communicate(timeout=10)
                self.assertEqual(mock.returncode, 0, stdout + stderr)
                self.assertEqual(json.loads((data / 'mock-result.json').read_text())['synthetic_exchanges'], 1)
            finally:
                if mock.poll() is None:
                    mock.terminate()
                    mock.communicate(timeout=10)

            # Simulate the old creation failure: endpoint exists, but no client ever
            # reached it. Resume must reuse its port and preserve every old file.
            resumed = root / 'FPF-Phase1-Staging-resume-test'
            resumed_code, resumed_data = resumed / 'service-boundary/code', resumed / 'service-boundary/data'
            resumed_code.mkdir(parents=True)
            resumed_data.mkdir()
            for name in ('localhost-cert.pem', 'localhost-key.pem'):
                shutil.copyfile(root / name, resumed / name)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                reservation.bind(('127.0.0.1', 0))
                retained_port = reservation.getsockname()[1]
            retained_endpoint = resumed_code / 'mock-endpoint.json'
            retained_endpoint.write_text(json.dumps({'port': retained_port}))
            (resumed_data / 'mock-result.json').write_text('{"status":"FAIL","synthetic_exchanges":0}')
            original = {path: path.read_bytes() for path in (retained_endpoint, resumed_data / 'mock-result.json')}
            attempt_id = '7' * 32
            attempt = resumed_data / ('step4-resume-' + attempt_id)
            attempt.mkdir()
            mock = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/phase1_service_local_mock.py'),
                '--stage-root', str(resumed), '--resume-attempt', attempt_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                ready = attempt / 'mock-ready.json'
                deadline = time.monotonic() + 10
                while not ready.exists() and mock.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), 'Resume mock did not start on retained port')
                self.assertEqual(json.loads(ready.read_text())['port'], retained_port)
                client_code = ('Add-Type -Path ' + quote(dll) + '; if (-not [StagingProbe]::LocalHttps(' +
                               quote(root / 'localhost.cer') + ',' + str(retained_port) + ")) { throw 'Resume TLS failed' }")
                result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', client_code],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                stdout, stderr = mock.communicate(timeout=10)
                self.assertEqual(mock.returncode, 0, stdout + stderr)
                self.assertEqual(json.loads((attempt / 'mock-result.json').read_text())['status'], 'PASS')
                self.assertTrue(all(path.read_bytes() == value for path, value in original.items()))
            finally:
                if mock.poll() is None:
                    mock.terminate()
                    mock.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
