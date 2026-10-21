"""New SCM adapter tests only. Never register, start or stop a real service."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from scripts import phase1_staging_scm_host as host

REPOSITORY = Path(__file__).resolve().parents[1]
ROOT = REPOSITORY


def setUpModule():
    global ROOT, _frozen
    from windows_powershell_environment import native_modules
    unittest.enterModuleContext(native_modules())
    from frozen_phase1 import materialize
    _frozen, ROOT = materialize(REPOSITORY)


def tearDownModule():
    _frozen.cleanup()

OPENSSL = Path(r'C:\Program Files\Git\usr\bin\openssl.exe')
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(sys.platform == 'win32' and OPENSSL.is_file(), 'Existing Windows runtime/OpenSSL required')
class ScmAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='synthetic-scm-adapter-')
        cls.root = Path(cls.temp.name)
        cls.stage = cls.root / 'FPF-Phase1-Staging-unit'
        cls.stage.mkdir()
        config = cls.stage / 'request.cnf'
        config.write_text('[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n')
        result = subprocess.run([str(OPENSSL), 'req', '-quiet', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1',
            '-config', str(config), '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-keyout', str(cls.stage / 'localhost-key.pem'),
            '-out', str(cls.stage / 'localhost-cert.pem')], capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Synthetic certificate generation failed')
        cls.runtime = cls.root / 'runtime'
        host.package_runtime(cls.runtime)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def bundle(self, name):
        attempt = self.stage / ('scm-' + name)
        code, data = attempt / 'code', attempt / 'data'
        code.mkdir(parents=True)
        data.mkdir()
        _, base = host.shared_helpers(ROOT / 'scripts')
        sources = {name: ROOT / name for name in dict(base.HASHES, **host.WRAPPER_HASHES)}
        for name in ('phase1_staging_engine_crash.py', 'phase1_staging_engine_https.py', 'phase1_staging_scm_host.py'):
            sources[name] = ROOT / 'scripts' / name
        for name, source in sources.items():
            shutil.copyfile(source, code / name)
        shutil.copyfile(self.stage / 'localhost-cert.pem', code / 'localhost-cert.pem')
        hashes = {p.name: host.digest(p) for p in code.iterdir()}
        (code / 'bundle.json').write_text(json.dumps(dict(sha256=hashes)))
        settings = dict(port=12345, cutover=str(dt.datetime.now().replace(microsecond=0) + dt.timedelta(minutes=1)),
                        service_sid='S-1-5-80-1-2-3-4-5')
        (code / 'settings.json').write_text(json.dumps(settings))
        return code, data

    def test_private_runtime_native_imports_without_SCM_calls(self):
        code, _ = self.bundle('preflight')
        result = subprocess.run([str(self.runtime / 'python.exe'), '-I', '-S', '-B',
            str(code / 'phase1_staging_scm_host.py'), '--preflight'], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(status='PASS', scope='imports_only_no_SCM'))
        self.assertEqual(result.stderr, '')
        self.assertFalse((code.parent / 'data' / 'synthetic.sqlite3').exists())
        manifest = json.loads((self.runtime / 'runtime-manifest.json').read_text())
        for name, digest in manifest['sha256'].items():
            self.assertEqual(host.digest(self.runtime / name), digest)
        self.assertFalse((self.runtime / 'Lib' / 'site-packages').exists())

    def test_bootstrap_native_registration_without_SCM_dispatch(self):
        # Real private Python + pywin32 Initialize/PrepareToHostSingle and real
        # audit guard. Dispatcher is replaced before host load: never contact SCM.
        for blocked in (False, True):
            code, data = self.bundle('bootstrap-' + str(blocked))
            identifier = ('c' if blocked else 'd') * 32
            adapter = code / ('phase1_staging_scm_bootstrap_' + identifier + '.py')
            shutil.copyfile(code / 'phase1_staging_scm_host.py', adapter)
            action = "sys.audit('socket.connect',None,('127.0.0.1',12345))" if blocked else 'None'
            probe = ("import runpy,sys,servicemanager; "
                     "servicemanager.StartServiceCtrlDispatcher=lambda:" + action + "; "
                     "sys.argv=[" + repr(str(adapter)) + ",'--bootstrap-idle']; "
                     "runpy.run_path(sys.argv[0],run_name='__main__')")
            result = subprocess.run([str(self.runtime / 'python.exe'), '-I', '-S', '-B', '-c', probe],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1 if blocked else 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout + result.stderr, '')
            events = [json.loads(line) for line in (data / ('bootstrap-' + identifier + '.jsonl')).read_text().splitlines()]
            stages = [e['stage'] for e in events]
            self.assertIn('dispatcher_registered', stages)
            self.assertIn('isolation_guard_installed', stages)
            self.assertEqual(stages[-1], 'startup_exception' if blocked else 'dispatcher_returned')
            if blocked:
                self.assertEqual(events[-1]['category'], 'Blocked')
            self.assertFalse(any(data.glob('*.sqlite3*')))

    def fake_native(self):
        statuses = []
        framework = type('SyntheticFramework', (), {'__init__': lambda self, args: None,
            'ReportServiceStatus': lambda self, status: statuses.append(status)})
        event = types.ModuleType('win32event')
        event.CreateEvent = lambda *args: threading.Event()
        event.SetEvent = lambda handle: handle.set()
        event.WaitForSingleObject = lambda handle, ms: handle.wait(ms / 1000)
        util = types.ModuleType('win32serviceutil'); util.ServiceFramework = framework
        service = types.ModuleType('win32service'); service.SERVICE_STOP_PENDING = 3
        manager = types.ModuleType('servicemanager')
        manager.LogMsg = mock.Mock(); manager.EVENTLOG_INFORMATION_TYPE = 1; manager.PYS_SERVICE_STARTED = 2
        modules = dict(win32serviceutil=util, win32event=event, win32service=service, servicemanager=manager)
        for name in ('win32api', 'win32con', 'win32security'):
            modules[name] = types.ModuleType(name)
        return modules, statuses

    def test_synthetic_idle_inflight_stop_restart_over_real_https(self):
        code, data = self.bundle('lifecycle')
        original_timeout = socket.getdefaulttimeout()
        self.addCleanup(socket.setdefaulttimeout, original_timeout)
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(code / 'phase1_staging_scm_host.py'),
            '--mock-stage', str(self.stage)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            ready = data / 'mock-ready.json'
            end = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None or time.monotonic() > end:
                    if process.poll() is not None:
                        out, err = process.communicate(timeout=2)
                        self.fail('Isolated TLS mock did not become ready: ' + repr((out, err)))
                    self.fail('Isolated TLS mock readiness timeout')
                time.sleep(0.02)
            settings = json.loads((code / 'settings.json').read_text())
            settings['port'] = json.loads(ready.read_text())['port']
            (code / 'settings.json').write_text(json.dumps(settings))
            ctx = host.context(code)
            origin = 'https://127.0.0.1:' + str(settings['port'])
            ctx.startup.provision(ctx.ledger, origin, 15, settings['cutover'], 'SYNTHETIC UNIT ONLY', storage_check=lambda _: None)
            modules, statuses = self.fake_native()
            with mock.patch.dict(sys.modules, modules), mock.patch.object(ctx.sync, 'private_storage'):
                cls, _ = host.build_service(ctx, token_sid=lambda: settings['service_sid'])
                wrapper = sys.modules['erpnext_sync_win'].PythonCornerExample
                self.assertIs(cls.main, wrapper.main)
                self.assertIs(cls.stop, wrapper.stop)
                self.assertIs(cls.SvcStop, wrapper.SvcStop)
                with self.assertRaises(ValueError):
                    cls(['OTHER-SERVICE', 'idle'])
                with self.assertRaises(ValueError):
                    cls([host.NAME, 'not-a-phase'])
                for phase in host.PHASES:
                    instance = cls([host.NAME, phase])
                    errors = []
                    def execute():
                        try:
                            instance.SvcDoRun()
                        except BaseException as error:
                            errors.append(type(error).__name__)
                    thread = threading.Thread(target=execute)
                    thread.start()
                    try:
                        end = time.monotonic() + 10
                        while not (data / (phase + '-ready.json')).exists():
                            if errors or time.monotonic() > end:
                                self.fail('New synthetic phase evidence missing: ' + phase + repr(errors))
                            time.sleep(0.02)
                        instance.SvcStop()
                    finally:
                        instance.stop()
                        instance.hWaitStop.set()
                        thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(errors, [])
                    complete = json.loads((data / (phase + '-complete.json')).read_text())
                    if phase == 'inflight':
                        self.assertTrue(complete['stop_requested'])
                        self.assertEqual(complete['states']['pending'], 1)
                        self.assertEqual(complete['states']['synced'], 0)
                    if phase == 'recover':
                        self.assertEqual(complete['states']['pending'], 0)
                        self.assertEqual(complete['states']['synced'], 1)
                    with ctx.startup.worker_lock(ctx.ledger):
                        pass
                self.assertEqual(statuses, [3, 3, 3])
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(stderr, b'')
            self.assertEqual(json.loads((data / 'mock-result.json').read_text()), dict(status='PASS', mock_records=1, stopped=True))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def test_guard_and_bundle_fail_closed(self):
        code, _ = self.bundle('bad-hash')
        (code / 'durable_sync.py').write_text('raise AssertionError("must not execute")')
        with self.assertRaises(ValueError):
            host.context(code)
        with self.assertRaises(ValueError):
            host.package_runtime(self.runtime)

    @unittest.skipUnless(POWERSHELL, 'PowerShell required')
    def test_guarded_provision_and_readonly_inspection(self):
        code, data = self.bundle('provision')
        # Only this temporary test directory gets private ACLs. The extra SID is
        # a descriptor entry, not account creation or a service registration.
        script = r'''
$ErrorActionPreference='Stop'
$p=$env:SCM_UNIT_DATA
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$a=[Security.AccessControl.DirectorySecurity]::new()
$a.SetOwner($sid);$a.SetAccessRuleProtection($true,$false)
foreach($s in @($sid.Value,'S-1-5-18','S-1-5-32-544','S-1-5-80-1-2-3-4-5')) {
 $a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($s),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
}
Set-Acl -LiteralPath $p -AclObject $a
'''
        setup = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
            env=dict(os.environ, SCM_UNIT_DATA=str(data)), capture_output=True, timeout=15)
        self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
        command = [str(self.runtime / 'python.exe'), '-I', '-S', '-B', str(code / 'phase1_staging_scm_host.py')]
        result = subprocess.run(command + ['--provision'], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        original = (data / 'synthetic.sqlite3').read_bytes()
        result = subprocess.run(command + ['--inspect', 'unlocked'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['states']['pending'], 0)
        self.assertEqual((data / 'synthetic.sqlite3').read_bytes(), original)
        # Wrong mutex expectation must fail, not invent a second worker.
        result = subprocess.run(command + ['--inspect', 'locked'], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stdout + result.stderr)


class RunnerReviewTests(unittest.TestCase):
    @unittest.skipUnless(POWERSHELL, 'PowerShell required')
    def test_runner_child_quoting_and_native_exit_capture(self):
        with tempfile.TemporaryDirectory(prefix='scm runner spaces ') as directory:
            root = Path(directory)
            child = root / 'synthetic child.py'
            child.write_text('import sys; sys.exit(int(sys.argv[1]))')
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            script = r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(RUNNER,[ref]$tokens,[ref]$errors)
foreach($name in @('Start-LocalChild','Finish-LocalChild')) {
 $node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
 . ([ScriptBlock]::Create($node.Extent.Text))
}
$bundle=TEMP_ROOT;$output=TEMP_ROOT
# Unit test is not elevated. Test ownership logic separately from native quoting.
function New-HelperLog([string]$Path) { New-Item -ItemType File -Path $Path | Out-Null }
$p=Start-LocalChild PYTHON @('-I','-S','-B',CHILD,'0') 'zero'
Finish-LocalChild $p 'zero'
$p=Start-LocalChild PYTHON @('-I','-S','-B',CHILD,'7') 'seven'
$failed=$false
try { Finish-LocalChild $p 'seven' } catch { $failed=$true }
if(-not $failed){throw 'Nonzero native exit accepted'}
Write-Output 'PASS'
'''
            for key, value in dict(RUNNER=ROOT / 'scripts' / 'run_phase1_staging_scm.ps1',
                                   TEMP_ROOT=root, PYTHON=sys.executable, CHILD=child).items():
                script = script.replace(key, quote(value))
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), 'PASS')
            self.assertEqual(result.stderr, '')

    @unittest.skipUnless(POWERSHELL, 'PowerShell required')
    def test_powershell_syntax_and_staging_only_controls(self):
        path = ROOT / 'scripts' / 'run_phase1_staging_scm.ps1'
        text = path.read_text()
        for forbidden in ('ERPNextBiometricPushService', 'Remove-NetFirewallRule', 'Set-NetFirewallRule',
                          'Remove-Item', 'Set-Service', 'New-Service', 'pip install'):
            self.assertNotIn(forbidden, text)
        self.assertIn('original_registration_restored_stopped', text)
        script = "$e=$null;$t=$null;[void][Management.Automation.Language.Parser]::ParseFile('" + str(path) + "',[ref]$t,[ref]$e);if($e.Count){exit 1}"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class FreshRuntimeTests(unittest.TestCase):
    def test_destination_remains_confined_to_staging_code(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            stage = parent / ('FPF-Phase1-Staging-' + '1' * 32)
            code = stage / 'service-boundary' / 'code'
            code.mkdir(parents=True)
            with mock.patch.dict(os.environ, LOCALAPPDATA=str(parent)):
                for name in ('runtime', 'runtime-' + 'a' * 32):
                    self.assertEqual(host.validate_runtime_destination(code / name), code / name)
                for path in (code / 'anything', code / 'runtime-short', parent / 'runtime',
                             code / '..' / '..' / '..' / 'runtime'):
                    with self.assertRaises(ValueError):
                        host.validate_runtime_destination(path)

    @unittest.skipUnless(POWERSHELL, 'PowerShell required')
    def test_fresh_rules_exact_scope_and_original_bindings_preserved(self):
        # Extract only the two new functions. All firewall cmdlets are fakes.
        path = ROOT / 'scripts' / 'run_phase1_staging_scm.ps1'
        inspector = ROOT / 'scripts' / 'inspect_phase1_reserved_runtime.ps1'
        script = r'''
$ErrorActionPreference='Stop'
$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(RUNNER,[ref]$t,[ref]$e)
if($e.Count){throw 'Runner syntax failed'}
[void][Management.Automation.Language.Parser]::ParseFile(INSPECTOR,[ref]$t,[ref]$e)
if($e.Count){throw 'Inspector syntax failed'}
foreach($name in @('Add-FreshFirewall','Assert-FreshFirewall')) {
 $node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
 . ([ScriptBlock]::Create($node.Extent.Text))
}
$code='C:\synthetic\code';$runtime=Join-Path $code ('runtime-'+('a'*32))
$freshRulePrefix='FPF-SCM-'+('a'*32);$rulePrefix='ORIGINAL'
$programs=@('old-probe.exe','old-runtime.exe')
$nonLoopback=@('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::-::','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
$script:made=@();$script:validated=0
function Get-NetFirewallRule { param($PolicyStore) $script:made }
function New-NetFirewallRule {
 param($PolicyStore,$Name,$DisplayName,$Group,$Enabled,$Profile,$Direction,$Action,$Program,$Protocol,$RemoteAddress)
 if($PolicyStore -ne 'PersistentStore' -or $Name -ne $DisplayName -or $Group -ne $freshRulePrefix -or
    $Action -ne 'Block' -or $Enabled -ne 'True' -or $Profile -ne 'Any' -or $Program -ne (Join-Path $runtime 'python.exe')) {throw 'Wrong fresh rule scope'}
 $udp=$Name.EndsWith('-UDP')
 $wantProtocol=if($udp){'UDP'}else{'Any'}
 $wantDirection=if($Name.EndsWith('-Inbound')){'Inbound'}else{'Outbound'}
 $wantAddresses=if($udp){@('Any')}else{$nonLoopback}
 if($Protocol -ne $wantProtocol -or $Direction -ne $wantDirection -or @(Compare-Object $RemoteAddress $wantAddresses).Count){throw 'Weakened rule'}
 $script:made+=@([pscustomobject]@{Name=$Name;Group=$Group})
}
function Test-StagingFirewallRule {
 param($Rule,$Name,[switch]$Active)
 if($rulePrefix -ne $freshRulePrefix -or $programs[1] -ne (Join-Path $runtime 'python.exe') -or $Rule.Count -ne 1 -or $Rule[0].Name -ne $Name){throw 'Wrong validator binding'}
 $script:validated++
}
Add-FreshFirewall
if($made.Count -ne 3 -or $validated -ne 6 -or $rulePrefix -ne 'ORIGINAL' -or $programs[1] -ne 'old-runtime.exe'){throw 'Original binding changed'}
$refused=$false
try{Add-FreshFirewall}catch{$refused=$true}
if(-not $refused -or $made.Count -ne 3){throw 'Duplicate rule creation allowed'}
Write-Output 'PASS: three equivalent fresh rules; original bindings unchanged'
'''
        for key, value in {'RUNNER': path, 'INSPECTOR': inspector}.items():
            script = script.replace(key, "'" + str(value).replace("'", "''") + "'")
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS:', result.stdout)
        self.assertEqual(result.stderr, '')


class BootstrapDiagnosticsTests(unittest.TestCase):
    @unittest.skipUnless(POWERSHELL, 'PowerShell required')
    def test_registration_history_does_not_mask_active_or_wrong_service(self):
        path = ROOT / 'scripts' / 'run_phase1_scm_bootstrap_idle.ps1'
        script = r'''
$ErrorActionPreference='Stop';$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(RUNNER,[ref]$t,[ref]$e)
if($e.Count){throw 'Syntax failed'}
$gate=$ast.Find({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and
 $n.Extent.Text.Contains("throw 'Original staging registration must be restored/stopped.'")},$true)
$oldImage='"C:\synthetic\FPFPhase1Staging.exe"'
$baseline=@{State='Stopped';StartMode='Manual';StartName='NT SERVICE\FPFPhase1Staging';PathName=$oldImage;ProcessId=0;ExitCode=1066;ServiceSpecificExitCode=1}
$svc=[pscustomobject]$baseline
. ([ScriptBlock]::Create($gate.Extent.Text))
foreach($pair in @(@('State','Running'),@('StartMode','Auto'),@('StartName','LocalSystem'),@('PathName','other.exe'),@('ProcessId',123))) {
 $changed=$baseline.Clone();$changed[$pair[0]]=$pair[1];$svc=[pscustomobject]$changed
 $rejected=$false
 try{. ([ScriptBlock]::Create($gate.Extent.Text))}catch{$rejected=$true}
 if(-not $rejected){throw 'Unsafe registration accepted'}
}
$svc=$null;$rejected=$false
try{. ([ScriptBlock]::Create($gate.Extent.Text))}catch{$rejected=$true}
if(-not $rejected){throw 'Missing service accepted'}
Write-Output 'PASS'
'''.replace('RUNNER', "'" + str(path).replace("'", "''") + "'")
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')
        text = path.read_text()
        self.assertIn('$stopped.ExitCode -eq 0 -and $stopped.ServiceSpecificExitCode -eq 0', text)
        self.assertIn('previous_exit_code=$svc.ExitCode', text)

    @unittest.skipUnless(POWERSHELL, 'PowerShell required')
    def test_ancestor_correction_grants_only_noninheriting_metadata(self):
        # Execute ONLY the in-memory rule constructor. Never run the script body
        # or change an ACL in this automated test.
        path = ROOT / 'scripts' / 'repair_phase1_staging_ancestor_metadata.ps1'
        script = r'''
$ErrorActionPreference='Stop';$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(REPAIR,[ref]$t,[ref]$e)
if($e.Count){throw 'Syntax failed'}
$node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'New-MetadataOnlyRule'},$true)
. ([ScriptBlock]::Create($node.Extent.Text))
$sid='S-1-5-80-3239607251-981885122-1049059141-1040181771-757005368'
$rule=New-MetadataOnlyRule
if([int]$rule.FileSystemRights -ne 0x100080 -or $rule.InheritanceFlags -ne 'None' -or
   $rule.PropagationFlags -ne 'None' -or $rule.AccessControlType -ne 'Allow' -or
   $rule.IdentityReference.Value -ne $sid){throw 'Excess rights or wrong identity'}
Write-Output 'PASS'
'''.replace('REPAIR', "'" + str(path).replace("'", "''") + "'")
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')
        text = path.read_text()
        self.assertIn('param([switch]$Apply,[switch]$Resume,[string]$OriginalBackup)', text)
        self.assertIn('[IO.FileMode]::CreateNew', text)
        for forbidden in ('-Recurse', 'FullControl', 'New-NetFirewallRule', 'Start-Service', 'SetOwner'):
            self.assertNotIn(forbidden, text)

    def test_staging_administrator_checks_use_enum_not_account_name(self):
        # Role-name lookup differs from built-in role membership on Windows.
        # Check the guard without executing any staging runner or service action.
        runner = ROOT / 'scripts' / 'run_phase1_scm_bootstrap_idle.ps1'
        self.assertIn('.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)', runner.read_text())
        for path in (ROOT / 'scripts').glob('*.ps1'):
            with self.subTest(script=path.name):
                self.assertNotRegex(path.read_text(), r'''IsInRole\s*\(\s*['"]Administrator['"]\s*\)''')

    def test_early_failure_logged_without_exception_text_or_old_file_changes(self):
        # No bundle/native dependencies exist: failure MUST happen before SCM.
        with tempfile.TemporaryDirectory(prefix='scm-bootstrap-log-') as directory:
            root = Path(directory) / ('scm-' + 'a' * 32)
            code, data = root / 'code', root / 'data'
            code.mkdir(parents=True); data.mkdir()
            identifier = 'b' * 32
            adapter = code / ('phase1_staging_scm_bootstrap_' + identifier + '.py')
            shutil.copyfile(ROOT / 'scripts' / 'phase1_staging_scm_host.py', adapter)
            old = data / 'old-evidence.txt'
            old.write_text('retained evidence')
            result = subprocess.run([sys.executable, '-I', '-S', '-B', str(adapter), '--bootstrap-idle'],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout + result.stderr, '')
            log = data / ('bootstrap-' + identifier + '.jsonl')
            raw = log.read_bytes()
            events = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual(events[0]['stage'], 'process_entry_python_initialized')
            self.assertEqual(events[-1]['stage'], 'startup_exception')
            self.assertEqual(events[-1]['category'], 'FileNotFoundError')
            self.assertTrue(events[-1]['frames'])
            self.assertFalse(any(e['stage'] == 'dispatcher_connect_begin' for e in events))
            self.assertEqual(old.read_text(), 'retained evidence')
            # Reusing the same log is refused; no old diagnostic bytes overwritten.
            again = subprocess.run([sys.executable, '-I', '-S', '-B', str(adapter), '--bootstrap-idle'],
                                   capture_output=True, timeout=10)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(log.read_bytes(), raw)

    def test_exception_redaction_and_native_error_codes(self):
        with tempfile.TemporaryFile(mode='w+', encoding='ascii') as stream:
            with mock.patch.object(host, 'BOOTSTRAP_STREAM', stream):
                try:
                    raise PermissionError(13, 'SECRET_PASSWORD must never appear')
                except PermissionError as error:
                    host.bootstrap_event('startup_exception', error)
            stream.seek(0)
            raw = stream.read()
            value = json.loads(raw)
            self.assertNotIn('SECRET_PASSWORD', raw)
            self.assertNotIn('must never appear', raw)
            self.assertEqual(value['errno'], 13)
            self.assertEqual(set(value), {'stage', 'pid', 'time_ns', 'category', 'errno', 'frames'})
            self.assertEqual(set(value['frames'][0]), {'file', 'line'})

    @unittest.skipUnless(POWERSHELL, 'PowerShell required')
    def test_idle_runner_parses_without_permissions_rules_or_timeout_changes(self):
        path = ROOT / 'scripts' / 'run_phase1_scm_bootstrap_idle.ps1'
        text = path.read_text()
        for forbidden in ('Set-Acl', 'SetAccessRule', 'New-NetFirewallRule', 'Set-NetFirewallRule',
                          'Remove-NetFirewallRule', 'Remove-Item', 'ServicesPipeTimeout',
                          'ERPNextBiometricPushService', "@('idle','inflight','recover')"):
            self.assertNotIn(forbidden, text)
        self.assertIn("$controller.Start([string[]]@('idle'))", text)
        self.assertIn('[IO.FileMode]::CreateNew', text)
        script = "$e=$null;$t=$null;[void][Management.Automation.Language.Parser]::ParseFile('" + str(path) + "',[ref]$t,[ref]$e);if($e.Count){$e;exit 1}"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
