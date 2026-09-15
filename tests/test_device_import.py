import json
import shutil
import unittest
from pathlib import Path

from config.loader import load_config
from config.secrets import SecretStore
from manager import device_import
from manager.setup.controller import SetupController
from tests.test_config_loader import FakeProtector
from tests.test_setup_controller import FakePaths, valid_setup_config


class DeviceImportTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True)
        self.paths = FakePaths(self.test_dir)
        self.secret_store = SecretStore(self.paths.get_secrets_dir(), protector=FakeProtector())
        self.controller = SetupController(paths_module=self.paths, secret_store=self.secret_store)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def parse(self, text, existing=None):
        return device_import.parse_csv_text(text, existing_device_ids=existing)

    def test_valid_single_row_csv(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,4370,DEVICE_03,Yes\n")

        self.assertTrue(preview.can_apply)
        self.assertEqual(preview.valid_rows[0].device_id, "DEVICE_03")

    def test_valid_multi_row_csv(self):
        preview = self.parse(
            "Device Name,IP Address,Port,Device ID,Enabled\n"
            "Main,192.0.2.20,4370,DEVICE_03,Yes\n"
            "Warehouse,device.example.test,4371,DEVICE_04,No\n"
        )

        self.assertEqual(len(preview.valid_rows), 2)
        self.assertFalse(preview.valid_rows[1].enabled)

    def test_utf8_bom_csv(self):
        preview = self.parse("\ufeffDevice Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,4370,DEVICE_03,Yes\n")

        self.assertTrue(preview.can_apply)

    def test_device_name_containing_comma(self):
        preview = self.parse('Device Name,IP Address,Port,Device ID,Enabled\n"Main, Entrance",192.0.2.20,4370,DEVICE_03,Yes\n')

        self.assertEqual(preview.valid_rows[0].name, "Main, Entrance")

    def test_missing_required_column(self):
        with self.assertRaisesRegex(device_import.DeviceImportError, "Device ID"):
            self.parse("Device Name,IP Address,Port,Enabled\nMain,192.0.2.20,4370,Yes\n")

    def test_missing_device_name(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\n,192.0.2.20,4370,DEVICE_03,Yes\n")

        self.assertEqual(preview.rows[0].status, device_import.INVALID)
        self.assertIn("Device Name is required", preview.rows[0].message)

    def test_missing_device_id(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,4370,,Yes\n")

        self.assertEqual(preview.rows[0].status, device_import.INVALID)
        self.assertIn("Device ID is required", preview.rows[0].message)

    def test_missing_host_ip(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,,4370,DEVICE_03,Yes\n")

        self.assertEqual(preview.rows[0].status, device_import.INVALID)
        self.assertIn("Host/IP is required", preview.rows[0].message)

    def test_invalid_port_string(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,abc,DEVICE_03,Yes\n")

        self.assertIn("Port must be between 1 and 65535", preview.rows[0].message)

    def test_port_below_valid_range(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,0,DEVICE_03,Yes\n")

        self.assertIn("Port must be between 1 and 65535", preview.rows[0].message)

    def test_port_above_valid_range(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,65536,DEVICE_03,Yes\n")

        self.assertIn("Port must be between 1 and 65535", preview.rows[0].message)

    def test_duplicate_device_id_within_import_file(self):
        preview = self.parse(
            "Device Name,IP Address,Port,Device ID,Enabled\n"
            "Main,192.0.2.20,4370,DEVICE_03,Yes\n"
            "Warehouse,192.0.2.21,4370,DEVICE_03,Yes\n"
        )

        self.assertEqual(preview.rows[1].status, device_import.INVALID)
        self.assertIn("Duplicate Device ID", preview.rows[1].message)

    def test_conflict_with_existing_configured_device_id(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,4370,DEVICE_01,Yes\n", existing={"DEVICE_01"})

        self.assertEqual(preview.rows[0].status, device_import.CONFLICT)
        self.assertIn("already exists", preview.rows[0].message)

    def test_enabled_yes_no_true_false_1_0(self):
        values = ["Yes", "No", "True", "False", "1", "0"]
        parsed = [device_import.parse_enabled(value) for value in values]

        self.assertEqual(parsed, [True, False, True, False, True, False])

    def test_invalid_enabled_value(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,4370,DEVICE_03,Maybe\n")

        self.assertEqual(preview.rows[0].status, device_import.INVALID)
        self.assertIn("Enabled must be", preview.rows[0].message)

    def test_unexpected_column_does_not_alter_config(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled,Notes\nMain,192.0.2.20,4370,DEVICE_03,Yes,Ignore me\n")

        device = preview.valid_rows[0].to_device_setup()
        self.assertFalse(hasattr(device, "notes"))

    def test_sensitive_unexpected_column_is_rejected(self):
        with self.assertRaisesRegex(device_import.DeviceImportError, "sensitive column"):
            self.parse("Device Name,IP Address,Port,Device ID,Enabled,API Secret\nMain,192.0.2.20,4370,DEVICE_03,Yes,nope\n")

    def test_import_preview_contains_row_status_and_message(self):
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nMain,192.0.2.20,4370,DEVICE_03,Yes\n")

        self.assertEqual(preview.rows[0].row_number, 2)
        self.assertEqual(preview.rows[0].status, device_import.VALID)
        self.assertEqual(preview.rows[0].message, "Valid")

    def test_valid_rows_apply_through_existing_controller(self):
        setup = valid_setup_config()
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nNew,192.0.2.22,4370,DEVICE_03,Yes\n")
        setup.devices = device_import.apply_preview_to_devices(setup.devices, preview)

        self.controller.write_config_atomic(setup)
        runtime = load_config(paths_module=self.paths, secret_store=self.secret_store)

        self.assertEqual([device["device_id"] for device in runtime.devices], ["DEVICE_01", "DEVICE_02", "DEVICE_03"])

    def test_invalid_import_does_not_change_existing_configuration(self):
        self.controller.write_config_atomic(valid_setup_config())
        before = json.loads(self.paths.get_config_path().read_text(encoding="utf-8"))
        preview = self.parse("Device Name,IP Address,Port,Device ID,Enabled\nBad,192.0.2.22,0,DEVICE_03,Yes\n")

        with self.assertRaises(device_import.DeviceImportError):
            setup = valid_setup_config()
            setup.devices = device_import.apply_preview_to_devices(setup.devices, preview)
            self.controller.write_config_atomic(setup)

        after = json.loads(self.paths.get_config_path().read_text(encoding="utf-8"))
        self.assertEqual(after, before)

    def test_export_includes_all_device_fields(self):
        output = device_import.export_devices_csv(valid_setup_config().devices)

        self.assertIn("Device Name,IP Address,Port,Device ID,Enabled", output)
        self.assertIn("Main Office,192.0.2.10,4370,DEVICE_01,Yes", output)

    def test_export_does_not_contain_credentials(self):
        output = device_import.export_devices_csv(valid_setup_config().devices)

        self.assertNotIn("api", output.lower())
        self.assertNotIn("secret", output.lower())
        self.assertNotIn("password", output.lower())
        self.assertNotIn("1234", output)

    def test_template_contains_expected_headers(self):
        self.assertEqual(device_import.template_csv(), "Device Name,IP Address,Port,Device ID,Enabled\n")

    def test_host_alias_is_accepted_and_dns_hostnames_are_allowed(self):
        preview = self.parse("Device Name,Host,Port,Device ID,Enabled\nWarehouse,zk.example.test,4370,DEVICE_03,Yes\n")

        self.assertTrue(preview.can_apply)
        self.assertEqual(preview.valid_rows[0].host, "zk.example.test")


if __name__ == "__main__":
    unittest.main()
