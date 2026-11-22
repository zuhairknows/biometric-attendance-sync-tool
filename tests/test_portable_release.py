"""Offline hardening regressions. No real SCM, ACL, credentials or destinations."""
import ast
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from deployment import portable_release as release
from durable_startup import destination_key
from sync_ledger import Ledger

def setUpModule():
    from windows_powershell_environment import native_modules
    unittest.enterModuleContext(native_modules())


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "deployment/Install-FPFBiometricSync.ps1"


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fpf-portable-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "app.py").write_text("# synthetic\n")
        (self.source / "docs").mkdir()
        self.requirements = (ROOT / "docs/requirements-windows311-accepted.txt").read_bytes()
        (self.source / "docs/requirements-windows311-accepted.txt").write_bytes(self.requirements)
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        rows = []
        for name, version in release.pins(self.requirements).items():
            filename = name.replace("-", "_") + "-" + version + "-py3-none-any.whl"
            path = self.wheels / filename
            with zipfile.ZipFile(path, "w") as wheel:
                wheel.writestr(name.replace("-", "_") + "-" + version + ".dist-info/METADATA",
                               "Metadata-Version: 2.1\nName: " + name + "\nVersion: " + version + "\n")
            rows.append(release.digest(path.read_bytes()) + "  " + filename)
        self.manifest = self.wheels / "SHA256SUMS.txt"
        self.manifest.write_text("\n".join(rows) + "\n")
        self.approved = release.digest(self.manifest.read_bytes())

    def build(self, destination, names=None, dirty=False):
        names = names or ["app.py", "docs/requirements-windows311-accepted.txt"]
        def git(root, *args):
            if "ls-files" in args:
                return ("\0".join(names) + "\0").encode()
            if "rev-parse" in args:
                return b"a" * 40 + b"\n"
            if "show" in args:
                return (self.source / args[-1].split(":", 1)[1]).read_bytes()
            if "status" in args:
                return b" M app.py\n" if dirty else b""
            raise AssertionError(args)
        with mock.patch.object(release, "git_bytes", side_effect=git):
            return release.build(self.source, self.wheels, self.approved, "2.0.0", destination)

    def test_byte_reproducible_versioned_package_and_offline_lock(self):
        a, b = self.root / "a.zip", self.root / "b.zip"
        self.build(a); self.build(b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        extracted = self.root / "extracted"
        with zipfile.ZipFile(a) as archive:
            archive.extractall(extracted)  # Only this test's generated synthetic archive.
        info = release.verify_tree(extracted)
        self.assertFalse(info["uncommitted_candidate"])
        lock = (extracted / "wheels/requirements.lock").read_text()
        self.assertEqual(len(lock.splitlines()), 19)
        self.assertEqual(lock.count("--hash=sha256:"), 19)

    def test_existing_package_never_overwritten(self):
        path = self.root / "one.zip"
        self.build(path)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "output_exists"):
            self.build(path)
        self.assertEqual(before, path.read_bytes())

    def test_dirty_candidate_marked(self):
        path = self.root / "candidate.zip"
        self.assertTrue(self.build(path, dirty=True)["uncommitted_candidate"])

    def test_missing_extra_corrupt_and_unapproved_wheels_fail(self):
        sample = next(self.wheels.glob("*.whl"))
        original = sample.read_bytes()
        sample.write_bytes(original + b"corrupt")
        with self.assertRaisesRegex(ValueError, "wheel_hash_mismatch"):
            release.wheel_payloads(self.wheels, self.approved, self.requirements)
        sample.write_bytes(original)
        (self.wheels / "extra.whl").write_bytes(b"unapproved")
        with self.assertRaisesRegex(ValueError, "missing_or_extra_wheel"):
            release.wheel_payloads(self.wheels, self.approved, self.requirements)
        with self.assertRaisesRegex(ValueError, "unapproved_wheel_manifest"):
            release.wheel_payloads(self.wheels, "0" * 64, self.requirements)

    def test_changed_pin_and_duplicate_pin_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate_pin"):
            release.pins(self.requirements + self.requirements)
        changed = self.requirements.replace(b"pyzk==0.9", b"pyzk==0.8")
        with self.assertRaisesRegex(ValueError, "wheel_pin_mismatch"):
            release.wheel_payloads(self.wheels, self.approved, changed)

    def test_private_state_cannot_enter_release(self):
        for name in ("local_config.py", "ledger.sqlite3", ".env", "sync.sqlite3-wal", "sync.sqlite3.activation.json"):
            (self.source / name).write_text("SYNTHETIC PRIVATE")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "private_release_input"):
                self.build(self.root / (name.replace(".", "_") + ".zip"),
                           names=["app.py", name, "docs/requirements-windows311-accepted.txt"])

    def test_windows_archive_paths_fail_closed(self):
        for name in ("../escape", "/absolute", "C:/evil", "a\\b", "a//b",
                     "a/../b", "file:stream", "CON.txt", "a/LPT1", "a. /b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                release.safe_name(name)

    def test_extracted_tampering_and_extra_files_rejected(self):
        path = self.root / "bundle.zip"
        self.build(path)
        output = self.root / "output"
        with zipfile.ZipFile(path) as archive:
            archive.extractall(output)
        (output / "unexpected.py").write_text("synthetic")
        with self.assertRaisesRegex(ValueError, "release_file_set"):
            release.verify_tree(output)
        (output / "unexpected.py").unlink()
        (output / "code/app.py").write_text("changed")
        with self.assertRaisesRegex(ValueError, "release_hash"):
            release.verify_tree(output)


class ConfigStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fpf-state-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "ledger.sqlite3"
        self.config = self.root / "local_config.py"
        self.values = dict(ERPNEXT_URL="https://synthetic.example.test", ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY="synthetic-key", ERPNEXT_API_SECRET="synthetic-secret",
            DURABLE_SYNC_ENABLED=True, HISTORICAL_RECOVERY_ENABLED=False,
            HISTORICAL_RECOVERY_COORDINATION=False, DURABLE_MONITOR_READONLY_SID=None,
            DURABLE_DB_PATH=str(self.database), LOGS_DIRECTORY=str(self.root / "logs"),
            IMPORT_START_DATE="20260101", devices=[dict(device_id="SYNTHETIC", ip="127.0.0.1",
            port=18879, password=0, clear_from_device_on_fetch=False)])
        self.write_config()

    def write_config(self):
        self.config.write_text("\n".join(k + " = " + repr(v) for k, v in self.values.items()))

    def bind(self):
        with Ledger(self.database) as ledger:
            ledger.db.execute("PRAGMA application_id=1234")
            ledger.db.commit()
        self.marker = Path(str(self.database) + ".activation.json")
        self.marker.write_text(json.dumps(dict(schema="durable-activation-v1", ledger_cookie=1234,
            destination_sha256=destination_key(self.values["ERPNEXT_URL"], 15),
            cutover_time="2026-01-01 00:00:00", review_reference="SYNTHETIC")))

    def test_strict_defaults_and_recovery_enable_refused(self):
        self.assertIsNone(release.config_summary(self.config)["monitor_sid"])
        for field in ("HISTORICAL_RECOVERY_ENABLED", "HISTORICAL_RECOVERY_COORDINATION"):
            self.values[field] = True
            self.write_config()
            with self.assertRaisesRegex(ValueError, "recovery_or_durable_gate"):
                release.configuration(self.config)
            self.values[field] = False

    def test_dynamic_duplicate_and_device_deletion_refused(self):
        with self.config.open("a") as stream:
            stream.write("\nimport os\n")
        with self.assertRaisesRegex(ValueError, "config_requires_literal_review"):
            release.configuration(self.config)
        self.write_config()
        with self.config.open("a") as stream:
            stream.write("\nDURABLE_SYNC_ENABLED=True\n")
        with self.assertRaisesRegex(ValueError, "config_ambiguous"):
            release.configuration(self.config)
        self.values["devices"][0]["clear_from_device_on_fetch"] = True
        self.write_config()
        with self.assertRaisesRegex(ValueError, "device_deletion_forbidden"):
            release.configuration(self.config)

    def test_sync_policy_settings_are_checked_before_installation(self):
        good = dict(SHIFT_SYNC_RETRY_GRACE_HOURS=2, SHIFT_SYNC_DEVICE_STALE_HOURS=12,
                    SHIFT_SYNC_FUTURE_TOLERANCE_MINUTES=10, SHIFT_SYNC_IGNORED_DEVICES=["SYNTHETIC"],
                    DURABLE_CONFIRM_READS_VERIFIED=True, DURABLE_SERVER_REVIEW_REFERENCE="SYNTHETIC REVIEW",
                    DURABLE_RESEND_VERIFIED_MISSING=True, DURABLE_RESEND_SETTLE_SECONDS=600)
        self.values.update(good)
        self.write_config()
        self.assertEqual(release.configuration(self.config)["SHIFT_SYNC_IGNORED_DEVICES"], ["SYNTHETIC"])
        bad = [(dict(SHIFT_SYNC_RETRY_GRACE_HOURS=-1), "shift_sync_setting_invalid"),
               (dict(SHIFT_SYNC_DEVICE_STALE_HOURS=True), "shift_sync_setting_invalid"),
               (dict(SHIFT_SYNC_FUTURE_TOLERANCE_MINUTES="10"), "shift_sync_setting_invalid"),
               (dict(SHIFT_SYNC_IGNORED_DEVICES=["TYPO_DEVICE"]), "shift_sync_ignored_device_unknown"),
               (dict(SHIFT_SYNC_IGNORED_DEVICES="SYNTHETIC"), "shift_sync_ignored_device_unknown"),
               (dict(DURABLE_CONFIRM_READS_VERIFIED=False), "resend_requires_verified_reads"),
               (dict(DURABLE_SERVER_REVIEW_REFERENCE=" "), "resend_requires_verified_reads"),
               (dict(DURABLE_RESEND_VERIFIED_MISSING="yes"), "resend_requires_verified_reads"),
               (dict(DURABLE_RESEND_SETTLE_SECONDS=59), "resend_settle_invalid"),
               (dict(UNMATCHED_RECHECK_DAYS=-1), "unmatched_recheck_invalid"),
               (dict(UNMATCHED_RECHECK_HOURS=0), "unmatched_recheck_invalid"),
               (dict(UNMATCHED_RECHECK_DAYS=True), "unmatched_recheck_invalid")]
        for change, error in bad:
            with self.subTest(change=change):
                self.values.update(good, **change)
                self.write_config()
                with self.assertRaisesRegex(ValueError, error):
                    release.configuration(self.config)

    def test_placeholder_template_fails_before_installation(self):
        with self.assertRaises(ValueError):
            release.configuration(ROOT / "deployment/local_config.py.template")

    def test_backup_preserves_latest_state_activation_and_source(self):
        self.bind()
        with Ledger(self.database) as ledger:
            ledger.db.execute("INSERT INTO device_state(device_id,fetch_ok) VALUES('SYNTHETIC',1)")
            ledger.db.execute("INSERT INTO shift_state VALUES('synthetic-shift','2026-01-02 12:00:00')")
            ledger.db.commit()
        before, marker = self.database.read_bytes(), self.marker.read_bytes()
        output = self.root / "current.sqlite3"
        result = release.backup(ROOT, self.config, output)
        self.assertEqual(result["database_sha256"], release.digest(output.read_bytes()))
        self.assertEqual(output.with_suffix(".activation.json").read_bytes(), marker)
        self.assertEqual(self.database.read_bytes(), before)
        with Ledger(output, readonly=True) as saved:
            self.assertEqual(saved.db.execute("SELECT COUNT(*) FROM device_state").fetchone()[0], 1)
            self.assertEqual(saved.shift_watermark("synthetic-shift"), dt.datetime(2026, 1, 2, 12))
        with self.assertRaisesRegex(ValueError, "backup_destination"):
            release.backup(ROOT, self.config, output)

    def test_intents_are_reported_without_modification(self):
        self.bind()
        with Ledger(self.database) as ledger:
            ledger.db.execute("CREATE TABLE sr_intents(identity TEXT)")
            ledger.db.execute("INSERT INTO sr_intents VALUES('synthetic')")
            ledger.db.commit()
        before = self.database.read_bytes()
        self.assertEqual(release.readonly_state(ROOT, self.config)["recovery_intents"], 1)
        self.assertEqual(self.database.read_bytes(), before)

    def test_network_is_explicit_get_and_rejects_redirect(self):
        connection = mock.Mock()
        connection.getresponse.return_value.status = 302
        with mock.patch.object(release.socket, "create_connection") as tcp, \
             mock.patch.object(release.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(ValueError, "erpnext_readonly_probe_failed"):
                release.network(self.config)
        self.assertEqual(tcp.call_count, 1)
        self.assertEqual(connection.request.call_args.args[:2], ("GET", "/api/method/ping"))
        connection.close.assert_called_once()

    def test_health_requires_fresh_logs_and_no_critical(self):
        logs = Path(self.values["LOGS_DIRECTORY"]); logs.mkdir()
        events = [dict(event="durable_capture", device_id="SYNTHETIC"),
                  dict(event="durable_health"), dict(event="durable_delivery", http=200, state="synced")]
        file = logs / "logs.log"
        file.write_text("".join("2026-10-24 12:00:01,000\tINFO\t" + json.dumps(e) + "\n" for e in events))
        with mock.patch.object(release, "readonly_state", return_value={"diagnostics": {}}):
            result = release.health(ROOT, self.config, "2026-10-24T12:00:00")
            self.assertTrue(result["healthy"]); self.assertTrue(result["delivery_observed"])
            self.assertFalse(release.health(ROOT, self.config, "2026-10-24T12:01:00")["healthy"])
            (logs / "error.log").write_text("2026-10-24 12:00:02,000\tCRITICAL\t" +
                                         json.dumps(dict(event="durable_critical")) + "\n")
            with self.assertRaisesRegex(ValueError, "critical_startup_or_storage_failure"):
                release.health(ROOT, self.config, "2026-10-24T12:00:00")


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell parser/function tests")
class DriverTests(unittest.TestCase):
    def powershell(self, names, body):
        # Extract helper definitions only. Never execute driver stages or native SCM.
        script = "$ErrorActionPreference=\'Stop\'\n$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('" + str(DRIVER) + "',[ref]$t,[ref]$e)\n"
        script += "if($e.Count){throw 'parse errors'}\n$names=" + "@(" + ",".join("'" + n + "'" for n in names) + ")\n"
        script += "foreach($n in $ast.FindAll({param($x)$x -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){if($n.Name -in $names){. ([scriptblock]::Create($n.Extent.Text))}}\n"
        script += body
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_privileged_scripts_parse(self):
        self.powershell([], "\n".join(
            "$null=[Management.Automation.Language.Parser]::ParseFile('" + str(path) +
            "',[ref]$t,[ref]$e);if($e.Count){throw 'parse errors'}"
            for path in (ROOT / "deployment").glob("*.ps1")))

    def test_path_boundaries(self):
        self.powershell(["Require", "Absolute", "Below"], r"""
if(-not (Below 'C:\synthetic\child' 'C:\synthetic')){throw 'child'}
if(Below 'C:\synthetic-other' 'C:\synthetic'){throw 'prefix collision'}
$failed=$false;try{Absolute '\\server\share'}catch{$failed=$true};if(-not $failed){throw 'UNC accepted'}
""")

    def test_failed_stop_cannot_switch_or_kill_worker(self):
        self.powershell(["Require", "Stop-Owned"], r"""
function Service {[pscustomobject]@{PathName='synthetic';StartName='LocalSystem';ProcessId=0;State='Running'}}
function Invoke-CimMethod {return @{ReturnValue=5}}
function Set-Image {throw 'UNEXPECTED SWITCH'}
function Stop-Process {throw 'UNEXPECTED KILL'}
$failed=$false
try{Stop-Owned ([pscustomobject]@{command='synthetic'})}catch{
if($_.Exception.Message -ne 'stop_request_failed'){throw};$failed=$true}
if(-not $failed){throw 'failed stop accepted'}
""")

    def test_unknown_registration_cannot_switch(self):
        self.powershell(["Require", "Set-Image"], r"""
function Service {[pscustomobject]@{PathName='different';StartName='LocalSystem';State='Stopped'}}
function Invoke-CimMethod {throw 'UNEXPECTED MUTATION'}
$failed=$false;try{Set-Image 'synthetic' 'replacement'}catch{
if($_.Exception.Message -ne 'service_switch_precondition'){throw};$failed=$true}
if(-not $failed){throw 'unknown service accepted'}
""")

    def test_core_drift_and_configuration_drift_refuse_rollback(self):
        self.powershell(["Require", "Same-Core"], r"""
$a=[pscustomobject]@{config_sha256='same';code_hashes=[pscustomobject]@{'sync_ledger.py'='a';'durable_sync.py'='b'}}
$b=[pscustomobject]@{config_sha256='same';code_hashes=[pscustomobject]@{'durable_sync.py'='b';'sync_ledger.py'='a'}}
Same-Core $a $b
$b.code_hashes.'sync_ledger.py'='changed'
$failed=$false;try{Same-Core $a $b}catch{$failed=$true};if(-not $failed){throw 'core change accepted'}
$b.code_hashes.'sync_ledger.py'='a';$b.config_sha256='different'
$failed=$false;try{Same-Core $a $b}catch{$failed=$true};if(-not $failed){throw 'config change accepted'}
""")

    def test_python_metadata_missing_is_not_excluded(self):
        self.powershell(["Require", "Python-Inventory"], r"""
function Get-CimInstance {return [pscustomobject]@{ExecutablePath=$null;CommandLine=$null}}
$failed=$false;try{Python-Inventory}catch{
if($_.Exception.Message -ne 'python_process_metadata_unavailable'){throw};$failed=$true}
if(-not $failed){throw 'unreadable process accepted'}
""")


    def test_credentials_copied_only_after_empty_file_protection(self):
        with tempfile.TemporaryDirectory(prefix="fpf-copy-test-") as tmp:
            source, target = Path(tmp) / "source.py", Path(tmp) / "target.py"
            source.write_text("SYNTHETIC ONLY")
            self.powershell(["Copy-PrivateConfig"], r"""
function Private-Config {}
function Protect([string]$Path) {
    if([IO.File]::ReadAllText($Path) -ne ''){throw 'credentials exposed before protection'}
}
Copy-PrivateConfig 'SOURCE' 'TARGET'
""".replace("SOURCE", str(source)).replace("TARGET", str(target)))
            self.assertEqual(target.read_text(), "SYNTHETIC ONLY")

    def test_failed_protection_never_copies_credentials(self):
        with tempfile.TemporaryDirectory(prefix="fpf-copy-fail-test-") as tmp:
            source, target = Path(tmp) / "source.py", Path(tmp) / "target.py"
            source.write_text("SYNTHETIC ONLY")
            self.powershell(["Copy-PrivateConfig"], r"""
function Private-Config {}
function Protect {throw 'protection failed'}
$failed=$false
try{Copy-PrivateConfig 'SOURCE' 'TARGET'}catch{$failed=$true}
if(-not $failed){throw 'protection failure ignored'}
""".replace("SOURCE", str(source)).replace("TARGET", str(target)))
            self.assertEqual(target.read_bytes(), b"")


    def test_receipt_updates_its_own_path_during_rollback(self):
        with tempfile.TemporaryDirectory(prefix="fpf-receipt-test-") as tmp:
            one, two, wrong = Path(tmp) / "old.json", Path(tmp) / "new.json", Path(tmp) / "wrong.json"
            body = r"""
$Receipt='WRONG'
$a=[pscustomobject]@{receipt_path='ONE';state='OLD'}
$b=[pscustomobject]@{receipt_path='TWO';state='NEW'}
Save $a;Save $b
$a.state='RESUMED';Save $a
"""
            self.powershell(["Save"], body.replace("WRONG", str(wrong)).replace("ONE", str(one)).replace("TWO", str(two)))
            self.assertEqual(json.loads(one.read_text())["state"], "RESUMED")
            self.assertEqual(json.loads(two.read_text())["state"], "NEW")
            self.assertFalse(wrong.exists())

    def test_native_argument_round_trip_with_spaces_quotes_and_slash(self):
        body = r"""
$result=Native 'PYTHON' @('-I','-B','-c','import json,sys; print(json.dumps(sys.argv[1:]))','with space','a"b','C:\tail\')
$values=$result|ConvertFrom-Json
if($values[0] -cne 'with space' -or $values[1] -cne 'a"b' -or $values[2] -cne 'C:\tail\'){throw 'native quoting failure'}
"""
        self.powershell(["Require", "Windows-Argument", "Native"], body.replace("PYTHON", sys.executable))


    def test_creation_owner_probe_removes_only_its_empty_file(self):
        with tempfile.TemporaryDirectory(prefix="fpf-owner-test-") as tmp:
            child = Path(tmp) / "child"; child.mkdir()
            body = r"""
$Trusted=@([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-32-544','S-1-5-18')
Assert-CreationOwner 'CHILD' 'ROOT'
"""
            self.powershell(["Require", "Absolute", "Below", "Assert-CreationOwner"],
                            body.replace("CHILD", str(child)).replace("ROOT", tmp))
            self.assertEqual(list(child.iterdir()), [])

    def test_untrusted_default_owner_fails_without_policy_changes(self):
        with tempfile.TemporaryDirectory(prefix="fpf-owner-fail-") as tmp:
            child = Path(tmp) / "child"; child.mkdir()
            body = r"""
$Trusted=@()
$failed=$false
try{Assert-CreationOwner 'CHILD' 'ROOT'}catch{
if($_.Exception.Message -ne 'elevated_default_owner_requires_review'){throw};$failed=$true}
if(-not $failed){throw 'untrusted owner accepted'}
"""
            self.powershell(["Require", "Absolute", "Below", "Assert-CreationOwner"],
                            body.replace("CHILD", str(child)).replace("ROOT", tmp))
            files=list(child.iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"")


    def test_native_retains_fixed_helper_failure_code(self):
        body = r"""
$failed=$false
try{Native 'PYTHON' @('-I','-B','-c','import json,sys; print(json.dumps(dict(status="FAIL",code="recovery_or_durable_gate")),file=sys.stderr);sys.exit(1)')}catch{
if($_.Exception.Message -cne 'deployment_check_failed: recovery_or_durable_gate'){throw};$failed=$true}
if(-not $failed){throw 'failure ignored'}
"""
        self.powershell(["Require", "Windows-Argument", "Native"], body.replace("PYTHON", sys.executable))

    def test_native_does_not_expose_raw_private_stderr(self):
        body = r"""
$failed=$false
try{Native 'PYTHON' @('-I','-B','-c','import sys;print("SYNTHETIC_PRIVATE_EXCEPTION",file=sys.stderr);sys.exit(1)')}catch{
if($_.Exception.Message -cne 'native_failed_exit_1'){throw};$failed=$true}
if(-not $failed){throw 'failure ignored'}
"""
        self.powershell(["Require", "Windows-Argument", "Native"], body.replace("PYTHON", sys.executable))


if __name__ == "__main__":
    unittest.main()
