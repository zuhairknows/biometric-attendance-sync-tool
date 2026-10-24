"""Non-admin regressions. PowerShell functions are AST-extracted with stubbed machine APIs."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("harness_safety", HERE / "harness_safety.py")
safe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe)


def powershell(names, body):
    definitions = """
$t=$null;$e=$null
$a=[Management.Automation.Language.Parser]::ParseFile('DRIVER',[ref]$t,[ref]$e)
if ($e.Count) { throw ($e | Out-String) }
$names=NAMES
$defs=@($a.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $names},$true) | ForEach-Object { $_.Extent.Text })
. ([ScriptBlock]::Create(($defs -join [Environment]::NewLine)))
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$Root='C:\\FPFPhase2AclAcceptance-A4F20179C01'
$Evidence='C:\\FPFPhase2AclEvidence-A4F20179C01'
""".replace("DRIVER", str(HERE / "Invoke-Acceptance.ps1").replace("'", "''"))
    definitions = definitions.replace("NAMES", "@(" + ",".join("'" + n + "'" for n in names) + ")")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                             definitions + body + "\nWrite-Output 'TEST_PASS'; exit 0"], capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)


class ProcessTests(unittest.TestCase):
    def test_real_powershell_normalization_boundaries_and_missing_metadata(self):
        powershell(["Assert-True", "Normalize-WindowsPath", "Path-InEnvironment", "Process-Classification"], r"""
function Check($exe,$line,$wanted) {
 $p=[pscustomobject]@{ProcessId=999;Name='python.exe';ExecutablePath=$exe;CommandLine=$line}
 $got=Process-Classification $p
 if ($got -ne $wanted) { throw "classification: $exe / $line => $got expected $wanted" }
}
Check ($Root+'\runtime\python.exe') 'python.exe worker' 'active'
Check ($Root+'\\runtime\\python.exe') 'python.exe worker' 'active'
Check ($Root.Replace('\','/')+'/runtime/python.exe') 'python.exe worker' 'active'
Check ('\\?\'+$Root+'\runtime\python.exe') 'python.exe worker' 'active'
Check 'C:\Python\python.exe' ('python.exe "'+$Root+'\tools\scm_host.py" service') 'active'
Check $null ('python.exe "'+$Root+'\tools\scm_host.py" service') 'active'
Check ($Root+'-other\python.exe') 'python.exe unrelated' 'clear'
Check 'C:\Python\python.exe' ('python.exe "'+$Root+'-other\worker.py"') 'clear'
Check $null 'unrelated.exe' 'unknown'
Check 'C:\Windows\protected.exe' $null 'unknown'
Check $null $null 'unknown'
Check 'C:\Python\python.exe' ('python.exe "C:\other\..\'+$Root.Substring(3)+'\tools\scm_host.py"') 'active'

Check 'relative.exe' 'relative.exe' 'unknown'
Check 'C:\Python\python.exe' 'python.exe tools\scm_host.py service' 'unknown'
""")

    def test_cleanup_gate_refuses_worker_and_unknown_never_kills(self):
        powershell(["Assert-True", "Assert-Processes", "Process-Classification",
                    "Path-InEnvironment", "Normalize-WindowsPath"], r"""
function Get-CimInstance {
 [pscustomobject]@{ProcessId=999;Name='python.exe';ExecutablePath=($Root+'\runtime\python.exe');CommandLine='worker';CreationDate='now'}
}
try { Assert-Processes; throw 'gate failed' } catch { if ($_ -notmatch 'active_or_unclassifiable_process') { throw } }
function Get-CimInstance {
 [pscustomobject]@{ProcessId=999;Name='protected';ExecutablePath=$null;CommandLine=$null;CreationDate='now'}
}
try { Assert-Processes; throw 'gate failed' } catch { if ($_ -notmatch 'active_or_unclassifiable_process') { throw } }
""")

    def test_reboot_finally_restores_after_verification_throws(self):
        powershell(["Assert-True", "Verify-Reboot"], r"""
$script:restores=0
function Owned-Service { param([switch]$AllowAutomatic); [pscustomobject]@{StartMode='Auto'} }
function Read-Receipt { throw 'injected_verification_failure' }
function Restore-Manual { $script:restores++ }
try { Verify-Reboot; throw 'expected failure' } catch { if ($_ -notmatch 'injected_verification_failure') { throw } }
if ($script:restores -ne 1) { throw 'restoration not attempted' }
""")

    def test_manual_restoration_is_verified(self):
        powershell(["Assert-True", "Restore-Manual"], r"""
$Service='synthetic'
function Owned-Service { param([switch]$AllowAutomatic); [pscustomobject]@{StartMode='Auto'} }
function Set-Service { param($Name,$StartupType) }
try { Restore-Manual; throw 'expected failure' } catch { if ($_ -notmatch 'MANUAL_RESTORATION_FAILED') { throw } }
""")


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scm-safety-unit-")
        self.root = Path(self.temp.name) / "disposable"
        self.root.mkdir()
        self.final = Path(self.temp.name) / "evidence"
        self.patch = mock.patch.multiple(safe, ROOT=self.root, FINAL=self.final)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def ready(self):
        attempt = safe.begin()
        safe.write_json(attempt / "payload" / "receipt.json", dict(
            candidate=safe.CANDIDATE, root=str(self.root), prepared=False,
            service="FPFPhase2AclAcceptance_" + safe.RUN))
        safe.write_json(attempt / "payload" / "summary.json", dict(result="NO_GO_INCOMPLETE_OR_FAILED"))
        safe.write_json(attempt / "payload" / "ACLs.json", [])
        return attempt

    def seal(self, attempt, inject=lambda step: None):
        return safe.seal_publish(attempt, ["receipt.json", "summary.json", "ACLs.json", "partials.json"], inject)

    def test_injected_failures_preserved_retry_and_cleanup(self):
        for step in ("after_partials", "after_manifest", "before_publish"):
            with self.subTest(step=step):
                old = self.ready()
                def fail(actual):
                    if actual == step:
                        raise RuntimeError("injected")
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    self.seal(old, fail)
                self.assertFalse(self.final.exists())
                prior = safe.files(old)
                new = self.ready()
                self.seal(new)
                safe.validate_cleanup()
                self.assertEqual(safe.files(self.final / "partials" / old.name), prior)
                # Reset only this unit test's own temporary environment.
                import shutil
                shutil.rmtree(self.final)
                shutil.rmtree(self.root / "evidence-staging")

    def test_interruption_after_atomic_publish_is_successfully_recoverable(self):
        attempt = self.ready()
        def fail(step):
            if step == "after_publish":
                raise RuntimeError("interrupted")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.seal(attempt, fail)
        safe.validate_cleanup()
        before = safe.files(self.final)
        with self.assertRaisesRegex(RuntimeError, "existing_evidence_refused"):
            safe.begin()
        self.assertEqual(safe.files(self.final), before)

    def test_partial_collection_and_partial_seal_are_retained(self):
        old = safe.begin()
        (old / "payload" / "partial.log").write_text("partial")
        self.seal(self.ready())
        safe.validate_cleanup()
        self.assertEqual((self.final / "partials" / old.name / "payload" / "partial.log").read_text(), "partial")

    def test_missing_required_file_tamper_and_new_unarchived_attempt_fail(self):
        attempt = self.ready()
        (attempt / "payload" / "summary.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "evidence_incomplete"):
            self.seal(attempt)
        self.seal(self.ready())
        (attempt / "payload" / "extra.log").write_text("new")
        with self.assertRaisesRegex(RuntimeError, "partial_evidence_not_preserved"):
            safe.validate_cleanup()
        (self.final / "ACLs.json").write_text("tampered")
        with self.assertRaisesRegex(RuntimeError, "evidence_hash_mismatch"):
            safe.validate(self.final)

    def test_unknown_attempt_or_identity_is_never_adopted(self):
        staging = self.root / "evidence-staging"
        staging.mkdir()
        (staging / "unrecognized").mkdir()
        with self.assertRaises((ValueError, RuntimeError)):
            self.seal(self.ready())

    def test_prepared_receipt_requires_consistent_backup_and_marker(self):
        attempt = self.ready()
        path = attempt / "payload" / "receipt.json"
        value = json.loads(path.read_text())
        value["prepared"] = True
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, "prepared_backup_required"):
            self.seal(attempt)

    def test_interruption_before_or_during_first_identity_write_is_recoverable(self):
        import uuid
        parent = self.root / "evidence-staging"
        parent.mkdir()
        empty = parent / str(uuid.uuid4())
        empty.mkdir()
        partial = parent / str(uuid.uuid4())
        partial.mkdir()
        (partial / "attempt.json").write_bytes(json.dumps(safe.identity(), sort_keys=True).encode()[:20])
        self.seal(self.ready())
        safe.validate_cleanup()
        self.assertTrue((self.final / "partials" / empty.name).is_dir())
        self.assertEqual((self.final / "partials" / partial.name / "attempt.json").read_bytes(),
                         (partial / "attempt.json").read_bytes())

    def test_attempt_parameter_cannot_escape(self):
        for name in ("../other", "C:/outside", "00000000-0000-0000-0000-000000000000/../other"):
            with self.assertRaises((ValueError, RuntimeError)):
                safe.attempt_path(name)


class MutexTests(unittest.TestCase):
    def test_atomic_create_only_claim_releases_in_reverse_order(self):
        api = mock.Mock()
        api.create_new.side_effect = [10, 20]
        api.owner.return_value = (os.getpid(), 123)
        with safe.exclusive_worker(api) as check:
            check()
        self.assertEqual(api.create_new.call_args_list, [mock.call(safe.LEASE), mock.call(safe.MUTEX)])
        self.assertEqual(api.release.call_args_list, [mock.call(20), mock.call(10)])

    def test_existing_mutex_fails_without_wait_or_takeover(self):
        api = mock.Mock()
        api.create_new.side_effect = [10, RuntimeError("exists")]
        with self.assertRaisesRegex(RuntimeError, "exists"):
            with safe.exclusive_worker(api):
                self.fail("must not start")
        api.release.assert_called_once_with(10)
        api.owner.assert_not_called()

    def test_missing_or_lost_owner_never_acquires(self):
        for owner in ((0, 0), (99999, 123)):
            api = mock.Mock()
            api.owner.return_value = owner
            with self.assertRaisesRegex(RuntimeError, "expected_worker_lost_mutex"):
                safe.assert_owner(os.getpid(), api)
            api.create_new.assert_not_called()
            api.release.assert_not_called()

    def test_readonly_query_failure_is_fail_closed(self):
        api = mock.Mock()
        api.owner.side_effect = RuntimeError("mutex_owner_query_failed")
        with self.assertRaisesRegex(RuntimeError, "query_failed"):
            safe.assert_owner(os.getpid(), api)
        api.create_new.assert_not_called()

    def test_actual_native_owner_query_with_disposable_nonproduction_mutex(self):
        import uuid
        api = safe.MutexAPI()
        name = "Local\\FPF-SCM-unit-" + str(uuid.uuid4())
        handle = api.create_new(name)
        try:
            self.assertEqual(api.owner(name), (os.getpid(), __import__("threading").get_native_id()))
            with self.assertRaisesRegex(RuntimeError, "exists"):
                api.create_new(name)
        finally:
            api.release(handle)


if __name__ == "__main__":
    unittest.main()
