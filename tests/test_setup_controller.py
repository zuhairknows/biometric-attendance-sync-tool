import json
import os
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from config.loader import load_config
from config.schema import validate_runtime_config
from config.status import CONFIGURED, INVALID, LEGACY_CONFIGURED, UNCONFIGURED, get_configuration_status, is_configured
from manager.setup.controller import SetupController
from manager.setup.model import DeviceSetup, ERPNextSetup, SetupConfiguration, SyncSetup
from manager.setup.validation import safe_review_summary
from tests.test_config_loader import valid_json_config


class FakePaths:
    def __init__(self, root):
        self.root = Path(root)

    def get_config_path(self):
        return self.root / "config.json"

    def get_logs_dir(self):
        return self.root / "logs"

    def get_state_path(self):
        return self.root / "state" / "state.json"

    def get_retry_dir(self):
        return self.root / "retry"

    def get_secrets_dir(self):
        return self.root / "secrets"

    def ensure_runtime_directories(self):
        for folder in [self.root, self.get_logs_dir(), self.get_state_path().parent, self.get_retry_dir(), self.get_secrets_dir()]:
            folder.mkdir(parents=True, exist_ok=True)


def valid_setup_config():
    return SetupConfiguration(
        erpnext=ERPNextSetup(
            url="https://erp.example.test",
            api_key="key",
            api_secret="secret",
            verify_ssl=True,
            request_timeout_seconds=30,
        ),
        devices=[
            DeviceSetup(
                name="Main Office",
                device_id="DEVICE_01",
                ip="192.0.2.10",
                port=4370,
                enabled=True,
                password=1234,
            ),
            DeviceSetup(
                name="Warehouse",
                device_id="DEVICE_02",
                ip="device-two.example.test",
                port=4371,
                enabled=False,
                password=0,
            ),
        ],
        sync=SyncSetup(import_start_date="2026-09-13", pull_frequency_minutes=60),
    )


class ConfigurationStatusTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True)
        self.paths = FakePaths(self.test_dir)
        sys.modules.pop("local_config", None)

    def tearDown(self):
        sys.modules.pop("local_config", None)
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_no_config_is_unconfigured(self):
        status = get_configuration_status(paths_module=self.paths)

        self.assertEqual(status.state, UNCONFIGURED)
        self.assertFalse(status.configured)
        self.assertFalse(is_configured(paths_module=self.paths))

    def test_safe_defaults_do_not_imply_configured(self):
        status = get_configuration_status(paths_module=self.paths)

        self.assertEqual(status.source, "defaults")
        self.assertEqual(status.state, UNCONFIGURED)

    def test_valid_commercial_config_is_configured(self):
        self.paths.get_config_path().write_text(json.dumps(valid_json_config()), encoding="utf-8")

        status = get_configuration_status(paths_module=self.paths)

        self.assertEqual(status.state, CONFIGURED)
        self.assertEqual(status.source, "json")
        self.assertTrue(status.configured)

    def test_valid_legacy_config_is_legacy_configured(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        legacy.ERPNEXT_API_KEY = "key"
        legacy.ERPNEXT_API_SECRET = "secret"
        legacy.PULL_FREQUENCY = 60
        legacy.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
        sys.modules["local_config"] = legacy

        status = get_configuration_status(paths_module=self.paths)

        self.assertEqual(status.state, LEGACY_CONFIGURED)
        self.assertTrue(status.configured)
        self.assertTrue(status.legacy)

    def test_minimal_valid_legacy_config_is_normalized_with_safe_defaults(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        legacy.ERPNEXT_API_KEY = "key"
        legacy.ERPNEXT_API_SECRET = "secret"
        legacy.PULL_FREQUENCY = 60
        legacy.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
        sys.modules["local_config"] = legacy

        runtime_config = load_config(paths_module=self.paths)

        self.assertEqual(runtime_config.CONFIG_SOURCE, "legacy")
        self.assertEqual(runtime_config.LOGS_DIRECTORY, str(self.test_dir / "logs"))
        self.assertEqual(runtime_config.STATE_FILE_PATH, str(self.test_dir / "state" / "state.json"))
        self.assertEqual(runtime_config.RETRY_DIRECTORY, str(self.test_dir / "logs"))
        self.assertTrue(validate_runtime_config(runtime_config))

    def test_missing_required_operational_legacy_field_is_invalid(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        legacy.ERPNEXT_API_KEY = "key"
        legacy.PULL_FREQUENCY = 60
        legacy.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
        sys.modules["local_config"] = legacy

        status = get_configuration_status(paths_module=self.paths)

        self.assertEqual(status.state, INVALID)
        self.assertIn("- ERPNEXT_API_SECRET is required.", status.details)

    def test_missing_logs_directory_does_not_break_legacy_runtime(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        legacy.ERPNEXT_API_KEY = "key"
        legacy.ERPNEXT_API_SECRET = "secret"
        legacy.PULL_FREQUENCY = 60
        legacy.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
        sys.modules["local_config"] = legacy

        status = get_configuration_status(paths_module=self.paths)
        runtime_config = load_config(paths_module=self.paths)

        self.assertEqual(status.state, LEGACY_CONFIGURED)
        self.assertEqual(runtime_config.LOGS_DIRECTORY, str(self.test_dir / "logs"))

    def test_legacy_configured_implies_runtime_configuration_can_load(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        legacy.ERPNEXT_API_KEY = "key"
        legacy.ERPNEXT_API_SECRET = "secret"
        legacy.PULL_FREQUENCY = 60
        legacy.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
        sys.modules["local_config"] = legacy

        status = get_configuration_status(paths_module=self.paths)
        runtime_config = load_config(paths_module=self.paths)

        self.assertEqual(status.state, LEGACY_CONFIGURED)
        self.assertTrue(validate_runtime_config(runtime_config))

    def test_malformed_config_is_invalid(self):
        self.paths.get_config_path().write_text('{"schema_version": 1,', encoding="utf-8")

        status = get_configuration_status(paths_module=self.paths)

        self.assertEqual(status.state, INVALID)
        self.assertEqual(status.source, "json")


class SetupControllerTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True)
        self.paths = FakePaths(self.test_dir)
        self.controller = SetupController(paths_module=self.paths)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_erpnext_form_validation(self):
        setup = valid_setup_config()
        setup.erpnext.url = "not-a-url"

        with self.assertRaisesRegex(ValueError, "erpnext.url"):
            self.controller.validate_erpnext(setup)

    def test_duplicate_device_ids_are_rejected(self):
        setup = valid_setup_config()
        setup.devices[1].device_id = "DEVICE_01"

        with self.assertRaisesRegex(ValueError, "Duplicate device_id"):
            self.controller.validate(setup)

    def test_invalid_device_port_is_rejected(self):
        setup = valid_setup_config()
        setup.devices[0].port = 70000

        with self.assertRaisesRegex(ValueError, "devices\\[0\\].port"):
            self.controller.validate(setup)

    def test_no_enabled_devices_is_allowed(self):
        setup = valid_setup_config()
        for device in setup.devices:
            device.enabled = False

        config_dict = self.controller.validate(setup)

        self.assertEqual(len(config_dict["devices"]), 2)
        self.assertFalse(any(device["enabled"] for device in config_dict["devices"]))

    def test_multiple_devices_are_supported(self):
        config_dict = self.controller.validate(valid_setup_config())

        self.assertEqual([device["device_id"] for device in config_dict["devices"]], ["DEVICE_01", "DEVICE_02"])

    def test_secret_masking_and_review_redaction(self):
        setup = valid_setup_config()

        summary = safe_review_summary(setup)
        rendered = json.dumps(summary)

        self.assertIn("********", rendered)
        self.assertNotIn("SUPER_SECRET_VALUE", rendered)
        self.assertNotIn("1234", rendered)

    def test_secret_values_are_not_in_validation_errors(self):
        setup = valid_setup_config()
        setup.erpnext.api_secret = "SUPER_SECRET_VALUE"
        setup.devices[0].password = 1234
        setup.devices[0].port = 0

        with self.assertRaises(ValueError) as context:
            self.controller.validate(setup)

        message = str(context.exception)
        self.assertNotIn("SUPER_SECRET_VALUE", message)
        self.assertNotIn("1234", message)

    def test_failed_validation_does_not_overwrite_existing_config(self):
        existing = {"schema_version": 1, "existing": True}
        self.paths.get_config_path().write_text(json.dumps(existing), encoding="utf-8")
        setup = valid_setup_config()
        setup.devices[0].port = 0

        with self.assertRaises(ValueError):
            self.controller.write_config_atomic(setup)

        self.assertEqual(json.loads(self.paths.get_config_path().read_text(encoding="utf-8")), existing)

    def test_successful_wizard_writes_valid_config(self):
        target = self.controller.write_config_atomic(valid_setup_config())

        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["erpnext"]["url"], "https://erp.example.test")
        self.assertEqual(len(saved["devices"]), 2)

    def test_atomic_config_replacement_uses_replace(self):
        with mock.patch("manager.setup.controller.os.replace") as replace:
            self.controller.write_config_atomic(valid_setup_config())

        replace.assert_called_once()


if __name__ == "__main__":
    unittest.main()
