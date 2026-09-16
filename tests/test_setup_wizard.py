import os
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from manager.setup.model import DeviceSetup, ERPNextSetup, SetupConfiguration, SyncSetup
from tests.test_setup_controller import FakePaths


QtCore = None
QtWidgets = None
wizard_module = None
_APP = None


def setUpModule():
    global QtCore, QtWidgets, wizard_module, _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for module_name in ["PyQt5.QtWidgets", "PyQt5.QtCore", "PyQt5"]:
        module = sys.modules.get(module_name)
        if module is not None and getattr(module, "__file__", None) is None:
            sys.modules.pop(module_name, None)

    from PyQt5 import QtCore as real_qtcore
    from PyQt5 import QtWidgets as real_qtwidgets
    from manager.setup import wizard as real_wizard_module

    QtCore = real_qtcore
    QtWidgets = real_qtwidgets
    wizard_module = real_wizard_module
    _APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def twelve_devices():
    devices = []
    for index in range(12):
        devices.append(DeviceSetup(
            name="Site A - Attendance Terminal - " + str(12 - index).zfill(2),
            device_id="DEVICE_" + str(index + 1).zfill(2),
            ip="192.0.2." + str(20 + index),
            port=4370 + index,
            enabled=index % 2 == 0,
            password=0,
            clear_from_device_on_fetch=index % 3 == 0,
        ))
    return devices


class FakeController:
    def __init__(self, setup_config=None, paths=None):
        self.setup_config = setup_config or SetupConfiguration()
        self.paths = paths or FakePaths(Path.cwd() / ".test-logs" / "wizard")
        self.tested_device_id = None
        self.bulk_result = types.SimpleNamespace(ok=True, message="All devices connected.", details=[])
        self.completed = False

    def load_existing_setup_config(self):
        return self.setup_config

    def test_device(self, _config, device_id, _sync_module):
        self.tested_device_id = device_id
        return types.SimpleNamespace(ok=True, message="Connected", details="")

    def test_devices(self, _config, _sync_module):
        return self.bulk_result

    def review_summary(self, setup_config):
        return {
            "erpnext_url": setup_config.erpnext.url,
            "erpnext_tested": setup_config.erpnext_tested,
            "erpnext_ok": setup_config.erpnext_ok,
            "api_key": "Configured",
            "verify_ssl": setup_config.erpnext.verify_ssl,
            "device_count": len(setup_config.devices),
            "enabled_device_count": len([device for device in setup_config.devices if device.enabled]),
            "pull_frequency_minutes": setup_config.sync.pull_frequency_minutes,
            "import_start_date": setup_config.sync.import_start_date,
            "devices": [
                {
                    "name": device.name,
                    "device_id": device.device_id,
                    "ip": device.ip,
                    "port": device.port,
                    "test_state": setup_config.device_test_states.get(device.device_id, "Not tested"),
                }
                for device in setup_config.devices
            ],
            "changes": [],
        }

    def complete_setup(self, _setup_config, service_controller=None):
        self.completed = True
        return types.SimpleNamespace(service_running=True)


class SetupWizardDeviceTableTests(unittest.TestCase):
    def setUp(self):
        self.paths = FakePaths(Path.cwd() / ".test-logs" / self._testMethodName)
        if self.paths.root.exists():
            shutil.rmtree(self.paths.root)
        self.paths.ensure_runtime_directories()
        self.config = SetupConfiguration(
            erpnext=ERPNextSetup(url="https://erp.example.test", api_key="key", api_secret="secret"),
            devices=twelve_devices(),
            sync=SyncSetup(import_start_date="2026-09-13", pull_frequency_minutes=60),
        )
        self.controller = FakeController(self.config, self.paths)
        self.wizard = wizard_module.SetupWizard(controller=self.controller)
        self.page = self.wizard.devices_page

    def tearDown(self):
        self.wizard.close()
        sys.modules.pop("erpnext_sync", None)
        if self.paths.root.exists():
            shutil.rmtree(self.paths.root)

    def row_map(self):
        rows = {}
        for row in range(self.page.table.rowCount()):
            device_id = self.page._device_id_for_row(row)
            rows[device_id] = {
                "name": self.page._item_text(row, self.page.COL_NAME),
                "ip": self.page._item_text(row, self.page.COL_HOST),
                "port": int(self.page._item_text(row, self.page.COL_PORT)),
                "enabled": self.page._checked(row, self.page.COL_ENABLED),
                "clear": self.page._checked(row, self.page.COL_CLEAR),
            }
        return rows

    def assert_table_matches_models(self):
        rendered = self.row_map()
        self.assertEqual(len(rendered), 12)
        for device in self.config.devices:
            self.assertEqual(rendered[device.device_id]["name"], device.name)
            self.assertEqual(rendered[device.device_id]["ip"], device.ip)
            self.assertEqual(rendered[device.device_id]["port"], device.port)
            self.assertEqual(rendered[device.device_id]["enabled"], device.enabled)
            self.assertEqual(rendered[device.device_id]["clear"], device.clear_from_device_on_fetch)

    def select_device(self, device_id):
        row = self.page._find_row_by_device_id(device_id)
        self.assertGreaterEqual(row, 0)
        self.page.table.setCurrentCell(row, self.page.COL_NAME)
        self.page.table.selectRow(row)
        return row

    def enable_all_rows(self):
        for row in range(self.page.table.rowCount()):
            item = self.page.table.item(row, self.page.COL_ENABLED)
            item.setCheckState(QtCore.Qt.Checked)

    def test_populating_table_with_sorting_enabled_preserves_rows(self):
        self.assertTrue(self.page.table.isSortingEnabled())
        self.assert_table_matches_models()

    def test_sorting_by_device_name_preserves_row_integrity(self):
        self.page.table.sortItems(self.page.COL_NAME, QtCore.Qt.AscendingOrder)
        self.assert_table_matches_models()
        self.page.table.sortItems(self.page.COL_NAME, QtCore.Qt.DescendingOrder)
        self.assert_table_matches_models()

    def test_sorting_by_device_id_preserves_row_integrity(self):
        self.page.table.sortItems(self.page.COL_ID, QtCore.Qt.AscendingOrder)
        self.assert_table_matches_models()

    def test_sorting_by_host_preserves_row_integrity(self):
        self.page.table.sortItems(self.page.COL_HOST, QtCore.Qt.AscendingOrder)
        self.assert_table_matches_models()

    def test_checkable_items_stay_in_enabled_and_clear_columns(self):
        for row in range(self.page.table.rowCount()):
            self.assertIsNone(self.page.table.cellWidget(row, self.page.COL_NAME))
            self.assertIsNone(self.page.table.cellWidget(row, self.page.COL_ENABLED))
            self.assertIsNone(self.page.table.cellWidget(row, self.page.COL_CLEAR))
            self.assertFalse(self.page.table.item(row, self.page.COL_NAME).flags() & QtCore.Qt.ItemIsUserCheckable)
            self.assertTrue(self.page.table.item(row, self.page.COL_ENABLED).flags() & QtCore.Qt.ItemIsUserCheckable)
            self.assertTrue(self.page.table.item(row, self.page.COL_CLEAR).flags() & QtCore.Qt.ItemIsUserCheckable)

    def test_edit_selected_after_sorting_uses_selected_device_identity(self):
        self.page.table.sortItems(self.page.COL_NAME, QtCore.Qt.AscendingOrder)
        self.select_device("DEVICE_07")
        captured = {}

        class FakeDialog:
            def __init__(self, device, *args, **kwargs):
                captured["device"] = device

            def exec_(self):
                return QtWidgets.QDialog.Rejected

        with mock.patch.object(wizard_module, "DeviceDialog", FakeDialog):
            self.page.edit_selected_device()

        self.assertEqual(captured["device"].device_id, "DEVICE_07")

    def test_remove_selected_after_sorting_removes_selected_device_identity(self):
        self.page.table.sortItems(self.page.COL_NAME, QtCore.Qt.AscendingOrder)
        self.select_device("DEVICE_08")

        with mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.Yes):
            self.page.remove_selected_device()

        self.assertNotIn("DEVICE_08", [device.device_id for device in self.page.to_models()])

    def test_test_selected_after_sorting_tests_selected_device_identity(self):
        sys.modules["erpnext_sync"] = types.SimpleNamespace()
        self.page.table.sortItems(self.page.COL_NAME, QtCore.Qt.AscendingOrder)
        self.select_device("DEVICE_09")
        self.page._run_worker = lambda callback, finished_callback: finished_callback(callback())

        self.page.test_selected_device()

        self.assertEqual(self.controller.tested_device_id, "DEVICE_09")
        self.assertEqual(self.wizard.setup_config.device_test_states["DEVICE_09"], "Connected")

    def test_bulk_device_test_parses_mixed_results_per_device(self):
        for row in range(self.page.table.rowCount()):
            enabled_item = self.page.table.item(row, self.page.COL_ENABLED)
            enabled_item.setCheckState(QtCore.Qt.Checked if self.page._device_id_for_row(row) in {"DEVICE_01", "DEVICE_02", "DEVICE_10"} else QtCore.Qt.Unchecked)

        result = types.SimpleNamespace(
            ok=False,
            message="Some devices failed.",
            details=[
                "DEVICE_01 (192.0.2.20:4370) - Connected",
                "DEVICE_02 (192.0.2.21:4371) - Connection failed",
                "DEVICE_10 (192.0.2.29:4379) - Connected",
            ],
        )

        self.page._finish_all_device_tests(result)

        self.assertEqual(self.page._item_text(self.page._find_row_by_device_id("DEVICE_01"), self.page.COL_STATUS), "Connected")
        self.assertEqual(self.page._item_text(self.page._find_row_by_device_id("DEVICE_02"), self.page.COL_STATUS), "Unavailable")
        self.assertEqual(self.page._item_text(self.page._find_row_by_device_id("DEVICE_10"), self.page.COL_STATUS), "Connected")
        self.assertEqual(self.page._item_text(self.page._find_row_by_device_id("DEVICE_11"), self.page.COL_STATUS), "Disabled")
        self.assertIn("Connected: 2", self.page.result.text())
        self.assertIn("Unavailable: 1", self.page.result.text())

    def test_bulk_device_test_marks_all_12_failed_when_details_fail(self):
        self.enable_all_rows()
        result = types.SimpleNamespace(
            ok=False,
            message="All devices failed.",
            details=[
                device.device_id + " (" + device.ip + ":" + str(device.port) + ") - Connection failed"
                for device in self.config.devices
            ],
        )

        self.page._finish_all_device_tests(result)

        for device in self.config.devices:
            row = self.page._find_row_by_device_id(device.device_id)
            self.assertEqual(self.page._item_text(row, self.page.COL_STATUS), "Unavailable")
            self.assertEqual(self.wizard.setup_config.device_test_states[device.device_id], "Unavailable")
        self.assertIn("Unavailable: 12", self.page.result.text())

    def test_bulk_device_test_missing_detail_is_not_assumed_connected(self):
        for row in range(self.page.table.rowCount()):
            enabled_item = self.page.table.item(row, self.page.COL_ENABLED)
            enabled_item.setCheckState(QtCore.Qt.Checked if self.page._device_id_for_row(row) in {"DEVICE_01", "DEVICE_02"} else QtCore.Qt.Unchecked)

        result = types.SimpleNamespace(
            ok=True,
            message="Overall success without full detail.",
            details=["DEVICE_01 (192.0.2.20:4370) - Connected"],
        )

        self.page._finish_all_device_tests(result)

        self.assertEqual(self.page._item_text(self.page._find_row_by_device_id("DEVICE_01"), self.page.COL_STATUS), "Connected")
        self.assertEqual(self.page._item_text(self.page._find_row_by_device_id("DEVICE_02"), self.page.COL_STATUS), "Unavailable")

    def test_connectivity_status_header_is_readable(self):
        self.assertEqual(self.page.table.horizontalHeaderItem(self.page.COL_STATUS).text(), "Connectivity Status")
        self.assertGreaterEqual(self.page.table.columnWidth(self.page.COL_STATUS), 170)


class SetupWizardFooterTests(unittest.TestCase):
    def setUp(self):
        self.paths = FakePaths(Path.cwd() / ".test-logs" / self._testMethodName)
        if self.paths.root.exists():
            shutil.rmtree(self.paths.root)
        self.paths.ensure_runtime_directories()

    def tearDown(self):
        if self.paths.root.exists():
            shutil.rmtree(self.paths.root)

    def test_first_run_welcome_retains_get_started(self):
        wizard = wizard_module.SetupWizard(controller=FakeController(paths=self.paths))
        try:
            wizard.page(0).initializePage()
            self.assertEqual(wizard.buttonText(QtWidgets.QWizard.NextButton), "Get Started")
        finally:
            wizard.close()

    def test_edit_configuration_welcome_uses_next_not_get_started(self):
        self.paths.get_config_path().write_text("{}", encoding="utf-8")
        wizard = wizard_module.SetupWizard(controller=FakeController(paths=self.paths))
        try:
            wizard.page(0).initializePage()
            self.assertEqual(wizard.windowTitle(), "Edit Configuration")
            self.assertEqual(wizard.buttonText(QtWidgets.QWizard.NextButton), "Next")
            self.assertNotEqual(wizard.buttonText(QtWidgets.QWizard.NextButton), "Get Started")
        finally:
            wizard.close()

    def test_edit_configuration_save_page_uses_save_changes(self):
        self.paths.get_config_path().write_text("{}", encoding="utf-8")
        wizard = wizard_module.SetupWizard(controller=FakeController(paths=self.paths))
        try:
            wizard.save_page.initializePage()
            self.assertEqual(wizard.buttonText(QtWidgets.QWizard.NextButton), "Save Changes")
        finally:
            wizard.close()

    def test_edit_configuration_intermediate_pages_never_show_get_started(self):
        self.paths.get_config_path().write_text("{}", encoding="utf-8")
        wizard = wizard_module.SetupWizard(controller=FakeController(paths=self.paths))
        try:
            for page in [wizard.erp_page, wizard.devices_page, wizard.sync_page, wizard.review_page]:
                page.initializePage()
                self.assertEqual(wizard.buttonText(QtWidgets.QWizard.NextButton), "Next")
                self.assertNotEqual(wizard.buttonText(QtWidgets.QWizard.NextButton), "Get Started")
        finally:
            wizard.close()

    def test_first_run_save_page_uses_save_configuration(self):
        wizard = wizard_module.SetupWizard(controller=FakeController(paths=self.paths))
        try:
            wizard.save_page.initializePage()
            self.assertEqual(wizard.buttonText(QtWidgets.QWizard.NextButton), "Save Configuration")
        finally:
            wizard.close()


class SetupWizardSavePageTests(unittest.TestCase):
    def setUp(self):
        self.paths = FakePaths(Path.cwd() / ".test-logs" / self._testMethodName)
        if self.paths.root.exists():
            shutil.rmtree(self.paths.root)
        self.paths.ensure_runtime_directories()
        self.config = SetupConfiguration(
            erpnext=ERPNextSetup(url="https://erp.example.test", api_key="key", api_secret="secret"),
            devices=twelve_devices(),
            sync=SyncSetup(import_start_date="2026-09-13", pull_frequency_minutes=60),
        )

    def tearDown(self):
        if self.paths.root.exists():
            shutil.rmtree(self.paths.root)

    def build_wizard(self, complete_setup):
        controller = FakeController(self.config, self.paths)
        controller.complete_setup = complete_setup
        return wizard_module.SetupWizard(controller=controller)

    def capture_warning(self):
        captured = {}

        def warning(_parent, title, message):
            captured["title"] = title
            captured["message"] = message
            return QtWidgets.QMessageBox.Ok

        return captured, mock.patch.object(QtWidgets.QMessageBox, "warning", side_effect=warning)

    def test_validation_error_uses_configuration_invalid_with_safe_detail(self):
        def fail_validation(_setup_config, service_controller=None):
            raise ValueError("- devices must contain unique device IDs.")

        wizard = self.build_wizard(fail_validation)
        captured, patcher = self.capture_warning()
        try:
            with patcher:
                self.assertFalse(wizard.save_page.validatePage())
            self.assertEqual(captured["title"], "Configuration Invalid")
            self.assertIn("unique device IDs", captured["message"])
            self.assertNotIn("secret", captured["message"].lower())
        finally:
            wizard.close()

    def test_service_failure_does_not_report_configuration_invalid(self):
        def fail_service(_setup_config, service_controller=None):
            raise wizard_module.SetupServiceError("raw service command failed", requires_admin=False)

        wizard = self.build_wizard(fail_service)
        captured, patcher = self.capture_warning()
        try:
            with patcher:
                self.assertFalse(wizard.save_page.validatePage())
            self.assertEqual(captured["title"], "Synchronization Service Could Not Start")
            self.assertIn("Configuration saved securely", captured["message"])
            self.assertNotIn("raw service command failed", captured["message"])
        finally:
            wizard.close()

    def test_admin_service_failure_uses_admin_required_title(self):
        def fail_admin(_setup_config, service_controller=None):
            raise wizard_module.SetupServiceError("access denied", requires_admin=True)

        wizard = self.build_wizard(fail_admin)
        captured, patcher = self.capture_warning()
        try:
            with patcher:
                self.assertFalse(wizard.save_page.validatePage())
            self.assertEqual(captured["title"], "Administrator Permission Required")
            self.assertIn("Open the Manager as Administrator", captured["message"])
        finally:
            wizard.close()

    def test_post_save_failure_uses_setup_could_not_be_completed(self):
        def fail_post_save(_setup_config, service_controller=None):
            raise wizard_module.SetupPostSaveError("status.json reload failed")

        wizard = self.build_wizard(fail_post_save)
        captured, patcher = self.capture_warning()
        try:
            with patcher:
                self.assertFalse(wizard.save_page.validatePage())
            self.assertEqual(captured["title"], "Setup Could Not Be Completed")
            self.assertNotIn("status.json reload failed", captured["message"])
        finally:
            wizard.close()


if __name__ == "__main__":
    unittest.main()
