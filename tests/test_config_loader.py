import json
import os
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import runtime_paths
from config import paths as config_paths
from config.loader import load_config
from config.schema import ConfigurationError


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


def valid_json_config(**overrides):
    config = {
        "schema_version": 1,
        "erpnext": {
            "url": "https://erp.example.test",
            "api_key": "key",
            "api_secret": "secret",
            "verify_ssl": True,
            "request_timeout_seconds": 30,
        },
        "devices": [
            {
                "device_id": "DEVICE_01",
                "name": "Main Office",
                "ip": "192.0.2.10",
                "port": 4370,
                "enabled": True,
                "clear_from_device_on_fetch": False,
            }
        ],
        "sync": {
            "pull_frequency_minutes": 60,
            "import_start_date": "2026-09-13",
        },
        "logging": {
            "level": "INFO",
            "retention_days": 30,
        },
    }
    for key, value in overrides.items():
        config[key] = value
    return config


class CommercialConfigLoaderTests(unittest.TestCase):
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

    def write_config(self, config):
        self.paths.get_config_path().write_text(json.dumps(config), encoding="utf-8")

    def test_json_config_loads_successfully(self):
        self.write_config(valid_json_config())

        config = load_config(paths_module=self.paths)

        self.assertEqual(config.CONFIG_SOURCE, "json")
        self.assertEqual(config.SCHEMA_VERSION, 1)
        self.assertEqual(config.ERPNEXT_URL, "https://erp.example.test")
        self.assertEqual(config.ERPNEXT_API_KEY, "key")
        self.assertEqual(config.ERPNEXT_API_SECRET, "secret")
        self.assertEqual(config.ERPNEXT_VERIFY_SSL, True)
        self.assertEqual(config.ERPNEXT_REQUEST_TIMEOUT, 30)
        self.assertEqual(config.PULL_FREQUENCY, 60)
        self.assertEqual(config.IMPORT_START_DATE, "20260913")
        self.assertEqual(config.LOGS_DIRECTORY, str(self.test_dir / "logs"))
        self.assertEqual(config.STATE_FILE_PATH, str(self.test_dir / "state" / "state.json"))
        self.assertEqual(config.RETRY_DIRECTORY, str(self.test_dir / "retry"))
        self.assertEqual(config.SECRETS_DIRECTORY, str(self.test_dir / "secrets"))

    def test_missing_config_falls_back_to_local_config(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        legacy.ERPNEXT_API_KEY = "key"
        legacy.ERPNEXT_API_SECRET = "secret"
        legacy.PULL_FREQUENCY = 15
        legacy.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
        sys.modules["local_config"] = legacy

        config = load_config(paths_module=self.paths)

        self.assertEqual(config.CONFIG_SOURCE, "legacy")
        self.assertEqual(config.ERPNEXT_URL, "https://legacy.example.test")
        self.assertEqual(config.LOGS_DIRECTORY, str(self.test_dir / "logs"))
        self.assertEqual(config.STATE_FILE_PATH, str(self.test_dir / "state" / "state.json"))
        self.assertEqual(config.RETRY_DIRECTORY, str(self.test_dir / "logs"))

    def test_defaults_work_when_no_configuration_source_exists(self):
        config = load_config(paths_module=self.paths)

        self.assertEqual(config.CONFIG_SOURCE, "defaults")
        self.assertEqual(config.SCHEMA_VERSION, 1)
        self.assertEqual(config.ERPNEXT_URL, "")
        self.assertEqual(config.devices, [])
        self.assertEqual(config.PULL_FREQUENCY, 60)

    def test_json_takes_precedence_over_legacy_configuration(self):
        legacy = types.ModuleType("local_config")
        legacy.ERPNEXT_URL = "https://legacy.example.test"
        sys.modules["local_config"] = legacy
        self.write_config(valid_json_config())

        config = load_config(paths_module=self.paths)

        self.assertEqual(config.CONFIG_SOURCE, "json")
        self.assertEqual(config.ERPNEXT_URL, "https://erp.example.test")

    def test_malformed_json_raises_clear_error(self):
        self.paths.get_config_path().write_text('{"schema_version": 1,', encoding="utf-8")

        with self.assertRaisesRegex(ConfigurationError, "Malformed JSON configuration"):
            load_config(paths_module=self.paths)

    def test_unsupported_schema_version_is_rejected(self):
        self.write_config(valid_json_config(schema_version=999))

        with self.assertRaisesRegex(ConfigurationError, "Unsupported schema_version 999"):
            load_config(paths_module=self.paths)

    def test_invalid_port_is_rejected(self):
        config = valid_json_config()
        config["devices"][0]["port"] = 70000
        self.write_config(config)

        with self.assertRaisesRegex(ConfigurationError, "devices\\[0\\].port"):
            load_config(paths_module=self.paths)

    def test_duplicate_device_id_is_rejected(self):
        config = valid_json_config()
        config["devices"].append(dict(config["devices"][0], ip="192.0.2.11"))
        self.write_config(config)

        with self.assertRaisesRegex(ConfigurationError, "Duplicate device_id"):
            load_config(paths_module=self.paths)

    def test_invalid_sync_frequency_is_rejected(self):
        config = valid_json_config()
        config["sync"]["pull_frequency_minutes"] = 0
        self.write_config(config)

        with self.assertRaisesRegex(ConfigurationError, "sync.pull_frequency_minutes"):
            load_config(paths_module=self.paths)

    def test_invalid_import_start_date_is_rejected(self):
        config = valid_json_config()
        config["sync"]["import_start_date"] = "13-09-2026"
        self.write_config(config)

        with self.assertRaisesRegex(ConfigurationError, "sync.import_start_date"):
            load_config(paths_module=self.paths)

    def test_secret_values_never_appear_in_validation_errors(self):
        config = valid_json_config()
        config["erpnext"]["api_secret"] = "SUPER_SECRET_VALUE"
        config["devices"][0]["password"] = "DEVICE_SECRET_VALUE"
        config["devices"][0]["port"] = 0
        self.write_config(config)

        with self.assertRaises(ConfigurationError) as context:
            load_config(paths_module=self.paths)

        message = str(context.exception)
        self.assertIn("devices[0].port", message)
        self.assertNotIn("SUPER_SECRET_VALUE", message)
        self.assertNotIn("DEVICE_SECRET_VALUE", message)


class CommercialConfigPathTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_windows_programdata_path_resolution(self):
        self.assertEqual(str(runtime_paths.DEFAULT_PROGRAMDATA_ROOT), r"C:\ProgramData\BiometricAttendanceSync")
        self.assertEqual(config_paths.CONFIG_FILE_NAME, "config.json")

    def test_directory_creation_is_explicit(self):
        programdata = self.test_dir / "ProgramDataRoot"
        absent_legacy = self.test_dir / "absent-legacy"

        with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_PROGRAMDATA": str(programdata)}, clear=True), mock.patch.object(runtime_paths, "LEGACY_PROGRAMDATA_ROOT", absent_legacy):
            self.assertEqual(config_paths.get_config_path(), programdata.resolve() / "config.json")
            self.assertFalse((programdata / "logs").exists())
            self.assertFalse((programdata / "state").exists())
            self.assertFalse((programdata / "retry").exists())
            self.assertFalse((programdata / "secrets").exists())

            created = config_paths.ensure_runtime_directories()

            self.assertTrue(created["logs"].is_dir())
            self.assertTrue(created["state"].is_dir())
            self.assertTrue(created["retry"].is_dir())
            self.assertTrue(created["secrets"].is_dir())


if __name__ == "__main__":
    unittest.main()
