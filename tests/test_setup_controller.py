import json
import logging
import os
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from config.loader import load_config
from config.schema import validate_runtime_config
from config.secrets import SecretStore
from config.status import CONFIGURED, INVALID, LEGACY_CONFIGURED, UNCONFIGURED, get_configuration_status, is_configured
from manager.setup.controller import SetupController, SetupServiceError
from manager.setup.model import DeviceSetup, ERPNextSetup, SetupConfiguration, SyncSetup
from manager.setup.validation import safe_review_summary
from manager.service_controller import ActionResult, ServiceStatus
from tests.test_config_loader import valid_json_config
from tests.test_config_loader import FakeProtector


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

    def get_backups_dir(self):
        return self.root / "backups"

    def get_diagnostics_dir(self):
        return self.root / "diagnostics"

    def ensure_runtime_directories(self):
        for folder in [self.root, self.get_logs_dir(), self.get_state_path().parent, self.get_retry_dir(), self.get_secrets_dir(), self.get_backups_dir(), self.get_diagnostics_dir()]:
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

    def test_protected_commercial_config_is_configured_when_secrets_load(self):
        store = SecretStore(self.paths.get_secrets_dir(), protector=FakeProtector())
        store.set_secret("erpnext/api_key", "key")
        store.set_secret("erpnext/api_secret", "secret")
        config = valid_json_config()
        config["erpnext"].pop("api_key")
        config["erpnext"].pop("api_secret")
        config["erpnext"]["api_key_ref"] = "erpnext/api_key"
        config["erpnext"]["api_secret_ref"] = "erpnext/api_secret"
        self.paths.get_config_path().write_text(json.dumps(config), encoding="utf-8")

        with mock.patch("config.schema._default_secret_store", return_value=store):
            status = get_configuration_status(paths_module=self.paths)

        self.assertEqual(status.state, CONFIGURED)

    def test_missing_secret_ref_makes_commercial_config_invalid_without_legacy_fallback(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        legacy.ERPNEXT_API_KEY = "key"
        legacy.ERPNEXT_API_SECRET = "secret"
        legacy.PULL_FREQUENCY = 60
        legacy.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
        sys.modules["local_config"] = legacy
        store = SecretStore(self.paths.get_secrets_dir(), protector=FakeProtector())
        config = valid_json_config()
        config["erpnext"].pop("api_secret")
        config["erpnext"]["api_secret_ref"] = "erpnext/api_secret"
        self.paths.get_config_path().write_text(json.dumps(config), encoding="utf-8")

        with mock.patch("config.schema._default_secret_store", return_value=store):
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
        self.secret_store = SecretStore(self.paths.get_secrets_dir(), protector=FakeProtector())
        self.controller = SetupController(paths_module=self.paths, secret_store=self.secret_store)

    def tearDown(self):
        logger = logging.getLogger("manager")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
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

    def test_fresh_install_blank_api_key_is_invalid(self):
        setup = valid_setup_config()
        setup.erpnext.api_key = ""

        with self.assertRaisesRegex(ValueError, "erpnext.api_key"):
            self.controller.validate_erpnext(setup)

    def test_fresh_install_blank_api_secret_is_invalid(self):
        setup = valid_setup_config()
        setup.erpnext.api_secret = ""

        with self.assertRaisesRegex(ValueError, "erpnext.api_secret"):
            self.controller.validate_erpnext(setup)

    def test_existing_plaintext_api_key_blank_edit_is_valid(self):
        existing = valid_json_config()
        self.paths.get_config_path().write_text(json.dumps(existing), encoding="utf-8")
        setup = self.controller.load_existing_setup_config()
        setup.erpnext.api_secret = "replacement-secret"

        self.controller.validate_erpnext(setup)

        self.assertEqual(setup.erpnext.api_key, "")
        self.assertTrue(setup.erpnext.has_existing_api_key)

    def test_existing_plaintext_api_secret_blank_edit_is_valid(self):
        existing = valid_json_config()
        self.paths.get_config_path().write_text(json.dumps(existing), encoding="utf-8")
        setup = self.controller.load_existing_setup_config()
        setup.erpnext.api_key = "replacement-key"

        self.controller.validate_erpnext(setup)

        self.assertEqual(setup.erpnext.api_secret, "")
        self.assertTrue(setup.erpnext.has_existing_api_secret)

    def test_existing_protected_api_key_ref_blank_edit_is_valid(self):
        self.controller.write_config_atomic(valid_setup_config())
        setup = self.controller.load_existing_setup_config()
        setup.erpnext.api_secret = "replacement-secret"

        self.controller.validate_erpnext(setup)

        self.assertEqual(setup.erpnext.api_key, "")
        self.assertTrue(setup.erpnext.has_existing_api_key)

    def test_existing_protected_api_secret_ref_blank_edit_is_valid(self):
        self.controller.write_config_atomic(valid_setup_config())
        setup = self.controller.load_existing_setup_config()
        setup.erpnext.api_key = "replacement-key"

        self.controller.validate_erpnext(setup)

        self.assertEqual(setup.erpnext.api_secret, "")
        self.assertTrue(setup.erpnext.has_existing_api_secret)

    def test_missing_existing_credential_with_blank_field_is_invalid(self):
        setup = valid_setup_config()
        setup.erpnext.api_key = ""
        setup.erpnext.has_existing_api_key = False

        with self.assertRaisesRegex(ValueError, "erpnext.api_key"):
            self.controller.validate_erpnext(setup)

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
        self.assertIn("api_key_ref", saved["erpnext"])
        self.assertIn("api_secret_ref", saved["erpnext"])
        self.assertNotIn("api_key", saved["erpnext"])
        self.assertNotIn("api_secret", saved["erpnext"])

    def test_wizard_save_does_not_store_plaintext_secrets_in_config_or_secret_files(self):
        target = self.controller.write_config_atomic(valid_setup_config())

        config_text = target.read_text(encoding="utf-8")
        secret_text = "\n".join(path.read_text(encoding="latin-1") for path in self.paths.get_secrets_dir().rglob("*.secret"))
        self.assertNotIn('"key"', config_text)
        self.assertNotIn('"secret"', config_text)
        self.assertNotIn('"password": 1234', config_text)
        self.assertNotIn("key", secret_text)
        self.assertNotIn("secret", secret_text)
        self.assertNotIn("1234", secret_text)

    def test_wizard_saved_refs_load_as_runtime_configuration(self):
        self.controller.write_config_atomic(valid_setup_config())

        runtime_config = load_config(paths_module=self.paths, secret_store=self.secret_store)

        self.assertEqual(runtime_config.ERPNEXT_API_KEY, "key")
        self.assertEqual(runtime_config.ERPNEXT_API_SECRET, "secret")
        self.assertEqual(runtime_config.devices[0]["password"], "1234")
        self.assertTrue(validate_runtime_config(runtime_config))

    def test_blank_edit_keeps_existing_protected_secret(self):
        self.controller.write_config_atomic(valid_setup_config())
        setup = valid_setup_config()
        setup.erpnext.api_key = ""
        setup.erpnext.api_secret = ""
        setup.devices[0].password = ""

        self.controller.write_config_atomic(setup)
        runtime_config = load_config(paths_module=self.paths, secret_store=self.secret_store)

        self.assertEqual(runtime_config.ERPNEXT_API_KEY, "key")
        self.assertEqual(runtime_config.ERPNEXT_API_SECRET, "secret")
        self.assertEqual(runtime_config.devices[0]["password"], "1234")

    def test_new_secret_value_replaces_existing_protected_secret(self):
        self.controller.write_config_atomic(valid_setup_config())
        setup = valid_setup_config()
        setup.erpnext.api_secret = "new-secret"
        setup.devices[0].password = 4321

        self.controller.write_config_atomic(setup)
        runtime_config = load_config(paths_module=self.paths, secret_store=self.secret_store)

        self.assertEqual(runtime_config.ERPNEXT_API_SECRET, "new-secret")
        self.assertEqual(runtime_config.devices[0]["password"], "4321")

    def test_resaving_plaintext_commercial_config_converts_to_secret_refs(self):
        self.paths.get_config_path().write_text(json.dumps(valid_json_config()), encoding="utf-8")

        self.controller.write_config_atomic(valid_setup_config())
        saved = json.loads(self.paths.get_config_path().read_text(encoding="utf-8"))

        self.assertIn("api_key_ref", saved["erpnext"])
        self.assertIn("api_secret_ref", saved["erpnext"])
        self.assertNotIn("api_key", saved["erpnext"])
        self.assertNotIn("api_secret", saved["erpnext"])

    def test_blank_edit_resaves_plaintext_commercial_config_as_protected_refs(self):
        existing = valid_json_config()
        existing["devices"][0]["password"] = 1234
        self.paths.get_config_path().write_text(json.dumps(existing), encoding="utf-8")
        setup = self.controller.load_existing_setup_config()

        self.controller.validate_erpnext(setup)

        self.controller.write_config_atomic(setup)
        saved = json.loads(self.paths.get_config_path().read_text(encoding="utf-8"))
        runtime_config = load_config(paths_module=self.paths, secret_store=self.secret_store)

        self.assertEqual(saved["erpnext"]["api_key_ref"], "erpnext/api_key")
        self.assertEqual(saved["erpnext"]["api_secret_ref"], "erpnext/api_secret")
        self.assertEqual(saved["devices"][0]["password_ref"], "devices/DEVICE_01/password")
        self.assertEqual(runtime_config.ERPNEXT_API_KEY, "key")
        self.assertEqual(runtime_config.ERPNEXT_API_SECRET, "secret")
        self.assertEqual(runtime_config.devices[0]["password"], "1234")

    def test_existing_protected_device_password_blank_edit_is_preserved(self):
        self.controller.write_config_atomic(valid_setup_config())
        setup = self.controller.load_existing_setup_config()

        self.controller.write_config_atomic(setup)
        runtime_config = load_config(paths_module=self.paths, secret_store=self.secret_store)

        self.assertEqual(setup.devices[0].password, "")
        self.assertTrue(setup.devices[0].has_existing_password)
        self.assertEqual(runtime_config.devices[0]["password"], "1234")

    def test_loaded_existing_setup_does_not_expose_plaintext_secrets(self):
        existing = valid_json_config()
        existing["devices"][0]["password"] = 1234
        self.paths.get_config_path().write_text(json.dumps(existing), encoding="utf-8")

        setup = self.controller.load_existing_setup_config()

        self.assertEqual(setup.erpnext.api_key, "")
        self.assertEqual(setup.erpnext.api_secret, "")
        self.assertEqual(setup.devices[0].password, "")
        self.assertTrue(setup.erpnext.has_existing_api_key)
        self.assertTrue(setup.erpnext.has_existing_api_secret)
        self.assertTrue(setup.devices[0].has_existing_password)

    def test_password_zero_remains_valid_without_protected_secret(self):
        self.controller.write_config_atomic(valid_setup_config())
        saved = json.loads(self.paths.get_config_path().read_text(encoding="utf-8"))
        zero_password_device = next(device for device in saved["devices"] if device["device_id"] == "DEVICE_02")

        self.assertNotIn("password_ref", zero_password_device)
        self.assertFalse((self.paths.get_secrets_dir() / "devices" / "DEVICE_02" / "password.secret").exists())

    def test_removed_device_password_secret_is_deleted_after_successful_save(self):
        self.controller.write_config_atomic(valid_setup_config())
        self.assertTrue((self.paths.get_secrets_dir() / "devices" / "DEVICE_01" / "password.secret").exists())

        setup = valid_setup_config()
        setup.devices = [setup.devices[1]]
        self.controller.write_config_atomic(setup)

        self.assertFalse((self.paths.get_secrets_dir() / "devices" / "DEVICE_01" / "password.secret").exists())

    def test_failed_save_preserves_existing_config_and_secrets(self):
        self.controller.write_config_atomic(valid_setup_config())
        old_config = self.paths.get_config_path().read_text(encoding="utf-8")
        old_secret = self.secret_store.get_secret("erpnext/api_secret")
        setup = valid_setup_config()
        setup.erpnext.api_secret = "new-secret"

        with mock.patch.object(self.controller, "_replace_config_file", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                self.controller.write_config_atomic(setup)

        self.assertEqual(self.paths.get_config_path().read_text(encoding="utf-8"), old_config)
        self.assertEqual(self.secret_store.get_secret("erpnext/api_secret"), old_secret)

    def test_failed_secret_write_preserves_original_plaintext_config(self):
        original = valid_json_config()
        self.paths.get_config_path().write_text(json.dumps(original), encoding="utf-8")

        with mock.patch.object(self.secret_store, "set_secret", side_effect=OSError("secret write failed")):
            with self.assertRaises(OSError):
                self.controller.write_config_atomic(self.controller.load_existing_setup_config())

        self.assertEqual(json.loads(self.paths.get_config_path().read_text(encoding="utf-8")), original)

    def test_atomic_config_replacement_uses_replace(self):
        with mock.patch.object(self.controller, "_replace_config_file") as replace:
            self.controller.write_config_atomic(valid_setup_config())

        replace.assert_called_once()

    def test_erpnext_connection_success_marks_customer_friendly_success(self):
        response = types.SimpleNamespace(status_code=200)
        controller = SetupController(paths_module=self.paths, secret_store=self.secret_store, request_func=mock.Mock(return_value=response))

        result = controller.test_erpnext(valid_setup_config())

        self.assertTrue(result.ok)
        self.assertEqual(result.title, "ERPNext Connected")
        self.assertIn("successfully", result.message)

    def test_erpnext_connection_failure_is_friendly(self):
        response = types.SimpleNamespace(status_code=401)
        controller = SetupController(paths_module=self.paths, secret_store=self.secret_store, request_func=mock.Mock(return_value=response))

        result = controller.test_erpnext(valid_setup_config())

        self.assertFalse(result.ok)
        self.assertEqual(result.title, "ERPNext Authentication Failed")
        self.assertIn("API Key", result.action)

    def test_api_secret_is_not_displayed_after_save_and_reload(self):
        self.controller.write_config_atomic(valid_setup_config())

        loaded = self.controller.load_existing_setup_config()
        summary = safe_review_summary(loaded)

        self.assertEqual(loaded.erpnext.api_secret, "")
        self.assertNotIn('"secret"', json.dumps(summary).lower())

    def test_add_one_device_validation(self):
        setup = valid_setup_config()
        setup.devices = [setup.devices[0]]

        config_dict = self.controller.validate(setup)

        self.assertEqual(len(config_dict["devices"]), 1)
        self.assertEqual(config_dict["devices"][0]["device_id"], "DEVICE_01")

    def test_device_connection_test_success_for_selected_device(self):
        class FakeConnection:
            def get_serialnumber(self):
                return "SERIAL"

            def disconnect(self):
                pass

        class FakeZK:
            def __init__(self, ip, port=4370, timeout=10, password=0):
                pass

            def connect(self):
                return FakeConnection()

        sync_module = types.SimpleNamespace(
            config=types.SimpleNamespace(LOGS_DIRECTORY=str(self.paths.get_logs_dir())),
            DEFAULT_ZK_PORT=4370,
            normalize_device_config=lambda device: dict(device, ip=device.get("ip"), port=int(device.get("port", 4370)), password=int(device.get("password", 0))),
        )
        controller = SetupController(paths_module=self.paths, secret_store=self.secret_store, zk_class=FakeZK)

        result = controller.test_device(valid_setup_config(), "DEVICE_01", sync_module)

        self.assertTrue(result.ok)
        self.assertEqual(result.details, ["DEVICE_01 (192.0.2.10:4370) - Connected"])

    def test_device_connection_test_failure_for_selected_device(self):
        class FakeZK:
            def __init__(self, ip, port=4370, timeout=10, password=0):
                pass

            def connect(self):
                raise RuntimeError("offline")

        sync_module = types.SimpleNamespace(
            config=types.SimpleNamespace(LOGS_DIRECTORY=str(self.paths.get_logs_dir())),
            DEFAULT_ZK_PORT=4370,
            normalize_device_config=lambda device: dict(device, ip=device.get("ip"), port=int(device.get("port", 4370)), password=int(device.get("password", 0))),
        )
        controller = SetupController(paths_module=self.paths, secret_store=self.secret_store, zk_class=FakeZK)

        result = controller.test_device(valid_setup_config(), "DEVICE_01", sync_module)

        self.assertFalse(result.ok)
        self.assertEqual(result.details, ["DEVICE_01 (192.0.2.10:4370) - Connection failed"])

    def test_review_page_summary_keeps_untested_items_unverified(self):
        setup = valid_setup_config()
        setup.erpnext_tested = True
        setup.erpnext_ok = True
        setup.device_test_states = {"DEVICE_01": "Connected"}

        summary = self.controller.review_summary(setup)

        self.assertTrue(summary["erpnext_ok"])
        self.assertEqual(summary["devices"][0]["test_state"], "Connected")
        self.assertEqual(summary["devices"][1]["test_state"], "Not tested")

    def test_configuration_save_succeeds(self):
        target = self.controller.write_config_atomic(valid_setup_config())

        self.assertTrue(target.is_file())

    def test_complete_setup_restarts_running_service_successfully(self):
        service = FakeServiceController(ServiceStatus(installed=True, state="Running", startup="Automatic"))

        with mock.patch("config.schema._default_secret_store", return_value=self.secret_store):
            result = self.controller.complete_setup(valid_setup_config(), service_controller=service)

        self.assertTrue(result.service_running)
        self.assertEqual(service.restart_calls, 1)

    def test_complete_setup_starts_stopped_service_successfully(self):
        service = FakeServiceController(ServiceStatus(installed=True, state="Stopped", startup="Automatic"))

        with mock.patch("config.schema._default_secret_store", return_value=self.secret_store):
            result = self.controller.complete_setup(valid_setup_config(), service_controller=service)

        self.assertTrue(result.service_running)
        self.assertEqual(service.start_calls, 1)

    def test_service_start_failure_does_not_report_setup_complete(self):
        service = FakeServiceController(ServiceStatus(installed=True, state="Stopped", startup="Automatic"), start_success=False)

        with mock.patch("config.schema._default_secret_store", return_value=self.secret_store):
            with self.assertRaisesRegex(SetupServiceError, "could not be started"):
                self.controller.complete_setup(valid_setup_config(), service_controller=service)

        self.assertTrue(self.paths.get_config_path().is_file())

    def test_service_start_failure_preserves_admin_requirement(self):
        service = FakeServiceController(ServiceStatus(installed=True, state="Stopped", startup="Automatic"), start_success=False, requires_admin=True)

        with mock.patch("config.schema._default_secret_store", return_value=self.secret_store):
            with self.assertRaises(SetupServiceError) as caught:
                self.controller.complete_setup(valid_setup_config(), service_controller=service)

        self.assertTrue(caught.exception.requires_admin)
        self.assertTrue(self.paths.get_config_path().is_file())

    def test_existing_configuration_remains_compatible_after_setup_completion(self):
        self.paths.get_config_path().write_text(json.dumps(valid_json_config()), encoding="utf-8")
        setup = self.controller.load_existing_setup_config()
        setup.erpnext.api_key = "new-key"
        setup.erpnext.api_secret = "new-secret"
        service = FakeServiceController(ServiceStatus(installed=True, state="Stopped", startup="Automatic"))

        with mock.patch("config.schema._default_secret_store", return_value=self.secret_store):
            self.controller.complete_setup(setup, service_controller=service)

        runtime_config = load_config(paths_module=self.paths, secret_store=self.secret_store)
        self.assertEqual(runtime_config.ERPNEXT_API_KEY, "new-key")
        self.assertTrue(validate_runtime_config(runtime_config))


class FakeServiceController:
    def __init__(self, initial_status, start_success=True, requires_admin=False):
        self.status = initial_status
        self.start_success = start_success
        self.requires_admin = requires_admin
        self.start_calls = 0
        self.restart_calls = 0

    def get_status(self):
        return self.status

    def start_service(self):
        self.start_calls += 1
        if self.start_success:
            self.status = ServiceStatus(installed=True, state="Running", startup=self.status.startup)
            return ActionResult(True, "Service started successfully.")
        self.status = ServiceStatus(installed=True, state="Stopped", startup=self.status.startup)
        return ActionResult(False, "Could not start the service.", requires_admin=self.requires_admin)

    def restart_service(self):
        self.restart_calls += 1
        if self.start_success:
            self.status = ServiceStatus(installed=True, state="Running", startup=self.status.startup)
            return ActionResult(True, "Service restarted successfully.")
        self.status = ServiceStatus(installed=True, state="Stopped", startup=self.status.startup)
        return ActionResult(False, "Could not restart the service.", requires_admin=self.requires_admin)


if __name__ == "__main__":
    unittest.main()
