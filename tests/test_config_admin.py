import json
import shutil
import sys
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from config.loader import load_config
from config.secrets import SecretStore
from config.status import CONFIGURED, LEGACY_CONFIGURED, UNCONFIGURED, get_configuration_status
from manager import config_admin
from manager.service_controller import ActionResult, ServiceStatus
from manager.setup.controller import SetupController
from tests.test_config_loader import FakeProtector, valid_json_config
from tests.test_setup_controller import FakePaths, valid_setup_config


class FakeServiceController:
    def __init__(self, state="Stopped"):
        self.status = ServiceStatus(installed=True, state=state, startup="Automatic")
        self.stop_calls = 0

    def get_status(self):
        return self.status

    def stop_service(self):
        self.stop_calls += 1
        self.status = ServiceStatus(installed=True, state="Stopped", startup="Automatic")
        return ActionResult(True, "Service stopped.")


class ConfigAdminTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True)
        self.paths = FakePaths(self.test_dir)
        self.secret_store = SecretStore(self.paths.get_secrets_dir(), protector=FakeProtector())
        self.controller = SetupController(paths_module=self.paths, secret_store=self.secret_store)
        sys.modules.pop("local_config", None)

    def tearDown(self):
        sys.modules.pop("local_config", None)
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_configuration_summary_uses_normalized_commercial_runtime_without_secrets(self):
        self.controller.write_config_atomic(valid_setup_config())

        with mock.patch("config.schema._default_secret_store", return_value=self.secret_store):
            status = get_configuration_status(paths_module=self.paths)
            summary = config_admin.get_configuration_summary(paths_module=self.paths, status=status)

        self.assertEqual(summary["configuration_state"], CONFIGURED)
        self.assertEqual(summary["erpnext_url"], "https://erp.example.test")
        self.assertEqual(summary["enabled_devices"], 1)
        self.assertEqual(summary["total_devices"], 2)
        self.assertTrue(summary["credentials_configured"])
        rendered = json.dumps(summary)
        self.assertNotIn('"api_secret"', rendered)
        self.assertNotIn("secret", summary["configuration_source"].lower())
        self.assertNotIn("1234", rendered)

    def test_configuration_summary_uses_normalized_legacy_runtime(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        legacy.ERPNEXT_API_KEY = "legacy-key"
        legacy.ERPNEXT_API_SECRET = "legacy-secret"
        legacy.PULL_FREQUENCY = 30
        legacy.IMPORT_START_DATE = "20260901"
        legacy.devices = [{"device_id": "DEVICE_01", "name": "Door", "ip": "192.0.2.10"}]
        sys.modules["local_config"] = legacy

        status = get_configuration_status(paths_module=self.paths)
        summary = config_admin.get_configuration_summary(paths_module=self.paths, status=status)

        self.assertEqual(status.state, LEGACY_CONFIGURED)
        self.assertEqual(summary["erpnext_url"], "https://legacy.example.test")
        self.assertEqual(summary["sync_interval_minutes"], 30)
        self.assertEqual(summary["import_start_date"], "2026-09-01")
        self.assertEqual(summary["total_devices"], 1)
        self.assertTrue(summary["credentials_configured"])

    def test_backup_contains_config_and_protected_secrets_but_not_logs_or_retry(self):
        self.controller.write_config_atomic(valid_setup_config())
        self.paths.get_logs_dir().mkdir(parents=True, exist_ok=True)
        self.paths.get_retry_dir().mkdir(parents=True, exist_ok=True)
        (self.paths.get_logs_dir() / "sync.log").write_text("log data", encoding="utf-8")
        (self.paths.get_retry_dir() / "retry.json").write_text("retry data", encoding="utf-8")

        archive_path = config_admin.create_configuration_backup(paths_module=self.paths, timestamp="20260914-120000")

        with zipfile.ZipFile(archive_path, "r") as archive:
            names = set(archive.namelist())
            archive_text = "\n".join(
                archive.read(name).decode("latin-1")
                for name in names
                if not name.endswith("/")
            )
        self.assertIn("config.json", names)
        self.assertIn("metadata.json", names)
        self.assertIn("secrets/erpnext/api_key.secret", names)
        self.assertFalse(any(name.startswith("logs/") for name in names))
        self.assertFalse(any(name.startswith("retry/") for name in names))
        self.assertNotIn('"api_secret": "secret"', archive_text)
        self.assertNotIn('"password": 1234', archive_text)

    def test_restore_valid_backup_replaces_configuration_and_preserves_runtime_load(self):
        self.controller.write_config_atomic(valid_setup_config())
        archive_path = config_admin.create_configuration_backup(paths_module=self.paths, timestamp="20260914-120000")
        replacement = valid_setup_config()
        replacement.erpnext.url = "https://changed.example.test"
        self.controller.write_config_atomic(replacement)

        with mock.patch("config.schema._default_secret_store", return_value=self.secret_store):
            restored_path = config_admin.restore_configuration_backup(archive_path, paths_module=self.paths)
            runtime = load_config(paths_module=self.paths, secret_store=self.secret_store)

        self.assertEqual(restored_path, self.paths.get_config_path())
        self.assertEqual(runtime.ERPNEXT_URL, "https://erp.example.test")
        self.assertEqual(runtime.ERPNEXT_API_SECRET, "secret")

    def test_failed_restore_preserves_previous_configuration(self):
        self.paths.get_config_path().write_text(json.dumps(valid_json_config()), encoding="utf-8")
        previous = self.paths.get_config_path().read_text(encoding="utf-8")
        archive_path = self.test_dir / "bad-backup.zip"
        invalid_config = valid_json_config()
        invalid_config["erpnext"]["url"] = ""
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("config.json", json.dumps(invalid_config))

        with self.assertRaises(Exception):
            config_admin.restore_configuration_backup(archive_path, paths_module=self.paths)

        self.assertEqual(self.paths.get_config_path().read_text(encoding="utf-8"), previous)

    def test_reset_stops_running_service_and_removes_only_commercial_configuration(self):
        self.controller.write_config_atomic(valid_setup_config())
        self.paths.get_logs_dir().mkdir(parents=True, exist_ok=True)
        log_path = self.paths.get_logs_dir() / "manager.log"
        log_path.write_text("keep", encoding="utf-8")
        service = FakeServiceController(state="Running")

        backup_path = config_admin.reset_commercial_configuration(paths_module=self.paths, service_controller=service)
        status = get_configuration_status(paths_module=self.paths)

        self.assertEqual(service.stop_calls, 1)
        self.assertTrue(backup_path.is_file())
        self.assertFalse(self.paths.get_config_path().exists())
        self.assertFalse(any(self.paths.get_secrets_dir().rglob("*.secret")))
        self.assertEqual(log_path.read_text(encoding="utf-8"), "keep")
        self.assertEqual(status.state, UNCONFIGURED)

    def test_diagnostics_report_is_sanitized(self):
        self.controller.write_config_atomic(valid_setup_config())

        with mock.patch("config.schema._default_secret_store", return_value=self.secret_store):
            report_path = config_admin.export_diagnostics_report(paths_module=self.paths)

        report_text = report_path.read_text(encoding="utf-8")
        self.assertIn("configuration", report_text)
        self.assertIn("DEVICE_01", report_text)
        self.assertNotIn('"api_secret":', report_text)
        self.assertNotIn("1234", report_text)

    def test_change_summary_reports_safe_edits_and_not_secret_values(self):
        self.controller.write_config_atomic(valid_setup_config())
        setup = self.controller.load_existing_setup_config()
        setup.erpnext.url = "https://new.example.test"
        setup.erpnext.api_secret = "NEW_SUPER_SECRET"
        setup.devices = setup.devices[:1]

        changes = config_admin.build_change_summary(setup, paths_module=self.paths)
        rendered = "\n".join(changes)

        self.assertIn("ERPNext URL:", rendered)
        self.assertIn("Device removed: DEVICE_02", rendered)
        self.assertIn("API secret will be replaced", rendered)
        self.assertNotIn("NEW_SUPER_SECRET", rendered)


if __name__ == "__main__":
    unittest.main()
