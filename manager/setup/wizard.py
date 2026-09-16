"""PyQt first-run setup wizard.

The widget layer intentionally stays thin: validation, review data, and file
writes live in manager.setup.controller so they can be tested without a desktop.
"""

import logging
import re

from PyQt5 import QtCore, QtWidgets

from .. import device_import
from .. import support
from ..device_management import (
    CONNECTED,
    DISABLED,
    NOT_TESTED,
    UNAVAILABLE,
    bulk_test_summary,
    display_connectivity_status,
    format_device_timestamp,
    friendly_device_failure_message,
    validate_device_form_values,
)
from config.status import INVALID
from .controller import SetupController, SetupPostSaveError, SetupServiceError
from .model import DeviceSetup, ERPNextSetup, SetupConfiguration, SyncSetup


WIZARD_LOGGER = logging.getLogger("manager.setup.wizard")
WIZARD_LOGGER.addHandler(logging.NullHandler())

FRESH_SETUP = "fresh_setup"
EDIT_CONFIGURATION = "edit_configuration"
REPAIR_CONFIGURATION = "repair_configuration"


class WizardWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(object)

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    @QtCore.pyqtSlot()
    def run(self):
        try:
            self.finished.emit(self.callback())
        except Exception as exc:
            self.finished.emit(exc)


class SetupWizard(QtWidgets.QWizard):
    def __init__(self, parent=None, controller=None):
        super().__init__(parent)
        self.controller = controller or SetupController()
        self.setup_config = self.controller.load_existing_setup_config()
        self.mode = self._detect_mode()
        self.edit_mode = self.mode in (EDIT_CONFIGURATION, REPAIR_CONFIGURATION)
        self.setup_result = None
        self.setWindowTitle(self._window_title())
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.addPage(WelcomePage(mode=self.mode))
        self.erp_page = ERPNextPage(self)
        self.devices_page = DevicesPage(self)
        self.sync_page = SyncPage(self)
        self.review_page = ReviewPage(self)
        self.save_page = SaveConfigurationPage(self)
        self.completion_page = CompletionPage(self)
        self.addPage(self.erp_page)
        self.addPage(self.devices_page)
        self.addPage(self.sync_page)
        self.addPage(self.review_page)
        self.addPage(self.save_page)
        self.addPage(self.completion_page)

    def _detect_mode(self):
        status = getattr(self.parent(), "configuration_status", None) if hasattr(self, "parent") else None
        if status is not None and status.state == INVALID:
            return REPAIR_CONFIGURATION
        if self.controller.paths.get_config_path().is_file():
            return EDIT_CONFIGURATION
        return FRESH_SETUP

    def _window_title(self):
        if self.mode == REPAIR_CONFIGURATION:
            return "Repair Configuration"
        if self.mode == EDIT_CONFIGURATION:
            return "Edit Configuration"
        return "Biometric Attendance Sync Setup"

    def use_standard_next_button(self):
        self.setButtonText(QtWidgets.QWizard.NextButton, "Next")

    def save_button_text(self):
        return "Save Changes" if self.mode in (EDIT_CONFIGURATION, REPAIR_CONFIGURATION) else "Save Configuration"

    def collect_pages(self):
        self.setup_config.erpnext = self.erp_page.to_model()
        self.setup_config.devices = self.devices_page.to_models()
        self.setup_config.sync = self.sync_page.to_model()
        return self.setup_config


class WelcomePage(QtWidgets.QWizardPage):
    def __init__(self, mode=FRESH_SETUP):
        super().__init__()
        self.mode = mode
        self.setTitle("Welcome")
        self.setSubTitle("Set up Biometric Attendance Sync for your ERPNext site.")
        layout = QtWidgets.QVBoxLayout(self)
        message = QtWidgets.QLabel(
            "This guided setup helps you connect ERPNext, add attendance devices, choose sync settings, and confirm automatic synchronization is running."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

    def initializePage(self):
        self.wizard().setButtonText(QtWidgets.QWizard.NextButton, "Get Started" if self.mode == FRESH_SETUP else "Next")

    def cleanupPage(self):
        self.wizard().setButtonText(QtWidgets.QWizard.NextButton, "Next")


class ERPNextPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("ERPNext Connection")
        self.url = QtWidgets.QLineEdit()
        self.api_key = QtWidgets.QLineEdit()
        self.api_secret = QtWidgets.QLineEdit()
        self.api_key_hint = QtWidgets.QLabel("Leave blank to keep the existing credential." if wizard.setup_config.erpnext.has_existing_api_key else "")
        self.api_secret_hint = QtWidgets.QLabel("Leave blank to keep the existing credential." if wizard.setup_config.erpnext.has_existing_api_secret else "")
        self.url.setText(wizard.setup_config.erpnext.url)
        self.api_key.setText(wizard.setup_config.erpnext.api_key)
        self.api_secret.setText(wizard.setup_config.erpnext.api_secret)
        self.api_secret.setEchoMode(QtWidgets.QLineEdit.Password)
        self.verify_ssl = QtWidgets.QCheckBox("Verify SSL")
        self.verify_ssl.setChecked(wizard.setup_config.erpnext.verify_ssl)
        self.timeout = QtWidgets.QSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setValue(wizard.setup_config.erpnext.request_timeout_seconds)
        self.test_button = QtWidgets.QPushButton("Test Connection")
        self.test_result = QtWidgets.QLabel("")
        self.test_button.clicked.connect(self.test_connection)

        form = QtWidgets.QFormLayout(self)
        form.addRow("ERPNext URL", self.url)
        form.addRow("API Key", self.api_key)
        if wizard.setup_config.erpnext.has_existing_api_key:
            form.addRow("", self.api_key_hint)
        form.addRow("API Secret", self.api_secret)
        if wizard.setup_config.erpnext.has_existing_api_secret:
            form.addRow("", self.api_secret_hint)
        form.addRow("", self.verify_ssl)
        form.addRow(self.test_button, self.test_result)

    def initializePage(self):
        self.wizard_ref.use_standard_next_button()

    def to_model(self):
        return ERPNextSetup(
            url=self.url.text(),
            api_key=self.api_key.text(),
            api_secret=self.api_secret.text(),
            has_existing_api_key=self.wizard_ref.setup_config.erpnext.has_existing_api_key,
            has_existing_api_secret=self.wizard_ref.setup_config.erpnext.has_existing_api_secret,
            verify_ssl=self.verify_ssl.isChecked(),
            request_timeout_seconds=self.timeout.value(),
        )

    def validatePage(self):
        config = self.wizard_ref.collect_pages()
        try:
            self.wizard_ref.controller.validate_erpnext(config)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Configuration", support.configuration_invalid_message([str(exc)]).compact())
            return False
        return True

    def test_connection(self):
        config = self.wizard_ref.collect_pages()
        result = self.wizard_ref.controller.test_erpnext(config)
        self.wizard_ref.setup_config.erpnext_tested = True
        self.wizard_ref.setup_config.erpnext_ok = bool(result.ok)
        self.test_result.setText(result.message)


class DevicesPage(QtWidgets.QWizardPage):
    COL_NAME = 0
    COL_ID = 1
    COL_HOST = 2
    COL_PORT = 3
    COL_ENABLED = 4
    COL_STATUS = 5
    COL_LAST_PULL = 6
    COL_LAST_PUSH = 7
    COL_PASSWORD = 8
    COL_CLEAR = 9

    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Attendance Devices")
        self.device_test_running = False
        self.active_jobs = []
        self.health_by_device = self._load_device_health()
        self.table = QtWidgets.QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Device Name",
            "Device ID",
            "Host / IP",
            "Port",
            "Enabled",
            "Connectivity Status",
            "Last Pull",
            "Last Push",
            "Password",
            "Clear After Fetch",
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self._configure_table_header()
        self._suspend_item_updates = False
        if hasattr(self.table, "setEditTriggers"):
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        if hasattr(self.table, "setSelectionBehavior"):
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        if hasattr(self.table, "setSortingEnabled"):
            self.table.setSortingEnabled(True)
        if hasattr(self.table, "hideColumn"):
            self.table.hideColumn(self.COL_PASSWORD)
        self.add_button = QtWidgets.QPushButton("Add Device")
        self.edit_button = QtWidgets.QPushButton("Edit Device")
        self.remove_button = QtWidgets.QPushButton("Remove Device")
        self.test_button = QtWidgets.QPushButton("Test Selected")
        self.test_all_button = QtWidgets.QPushButton("Test All Enabled")
        self.import_button = QtWidgets.QPushButton("Import Devices")
        self.export_button = QtWidgets.QPushButton("Export Devices")
        self.template_button = QtWidgets.QPushButton("Save Template")
        self.result = QtWidgets.QLabel("")
        self.add_button.clicked.connect(self.add_device_dialog)
        self.edit_button.clicked.connect(self.edit_selected_device)
        self.remove_button.clicked.connect(self.remove_selected_device)
        self.test_button.clicked.connect(self.test_selected_device)
        self.test_all_button.clicked.connect(self.test_all_enabled_devices)
        self.import_button.clicked.connect(self.import_devices)
        self.export_button.clicked.connect(self.export_devices)
        self.template_button.clicked.connect(self.save_template)
        if hasattr(self.table, "itemSelectionChanged"):
            self.table.itemSelectionChanged.connect(self._update_action_state)
        if hasattr(self.table, "itemChanged"):
            self.table.itemChanged.connect(self._handle_item_changed)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.test_all_button)
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.template_button)
        buttons.addStretch(1)
        buttons.addWidget(self.result)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table)
        if any(device.has_existing_password for device in self.wizard_ref.setup_config.devices):
            layout.addWidget(QtWidgets.QLabel("Leave blank to keep the existing device password."))
        layout.addLayout(buttons)
        for device in self.wizard_ref.setup_config.devices:
            self.add_device(device)
        self._update_action_state()

    def initializePage(self):
        self.wizard_ref.use_standard_next_button()

    def add_device(self, device):
        with self._sorting_suspended():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._set_row_from_model(row, device, update_actions=False)
        self._update_action_state()

    def add_device_dialog(self):
        dialog = DeviceDialog(DeviceSetup(), self, existing_device_ids=self._current_device_ids())
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.add_device(dialog.to_model())

    def edit_selected_device(self):
        row = self.table.currentRow()
        if row < 0:
            self.result.setText("Select a device to edit.")
            return
        current_id = self._device_id_for_row(row)
        dialog = DeviceDialog(
            self._model_from_row(row),
            self,
            existing_device_ids=self._current_device_ids(exclude_row=row),
            original_device_id=current_id,
        )
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._set_row_from_model(row, dialog.to_model())

    def remove_selected_device(self):
        row = self.table.currentRow()
        if row >= 0:
            device_id = self._device_id_for_row(row) or "this device"
            name = self._item_text(row, self.COL_NAME) or "Unnamed device"
            host = self._item_text(row, self.COL_HOST)
            port = self._item_text(row, self.COL_PORT)
            answer = QtWidgets.QMessageBox.question(
                self,
                "Remove Biometric Device",
                "Remove biometric device?\n\n"
                + "Device: "
                + name
                + "\nDevice ID: "
                + device_id
                + "\nHost: "
                + host
                + ":"
                + port
                + "\n\nThis removes the device from synchronization configuration. It does not delete attendance records from ERPNext or from the biometric device.",
            )
            if answer == QtWidgets.QMessageBox.Yes:
                self.table.removeRow(row)
                self._update_action_state()

    def to_models(self):
        devices = []
        existing_by_id = {
            device.device_id: device
            for device in self.wizard_ref.setup_config.devices
        }
        for row in range(self.table.rowCount()):
            device_id = self._device_id_for_row(row)
            existing_device = existing_by_id.get(device_id)
            devices.append(DeviceSetup(
                name=self._item_text(row, self.COL_NAME),
                device_id=device_id,
                ip=self._item_text(row, self.COL_HOST),
                port=int(self._item_text(row, self.COL_PORT) or "4370"),
                enabled=self._checked(row, self.COL_ENABLED),
                password=self._item_text(row, self.COL_PASSWORD),
                has_existing_password=bool(existing_device and existing_device.has_existing_password),
                clear_from_device_on_fetch=self._checked(row, self.COL_CLEAR),
            ))
        return devices

    def validatePage(self):
        config = self.wizard_ref.collect_pages()
        try:
            self.wizard_ref.controller.validate(config)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Configuration", support.configuration_invalid_message([str(exc)]).compact())
            return False
        return True

    def test_selected_device(self):
        row = self.table.currentRow()
        if row < 0:
            self.result.setText("Select a device to test.")
            return
        if self.device_test_running:
            self.result.setText("A device test is already running.")
            return
        if not self._row_enabled(row):
            self.result.setText("Disabled devices are skipped. Enable the device before testing it.")
            self._set_row_status(row, DISABLED)
            return
        device_id = self._device_id_for_row(row)
        self._set_testing_state(True, "Testing " + device_id + "...")
        config = self.wizard_ref.collect_pages()

        def run_test():
            import erpnext_sync as sync_module
            return self.wizard_ref.controller.test_device(config, device_id, sync_module)

        self._run_worker(run_test, lambda result: self._finish_selected_device_test(device_id, result))

    def test_all_enabled_devices(self):
        if self.device_test_running:
            self.result.setText("A device test is already running.")
            return
        enabled_count = 0
        for row in range(self.table.rowCount()):
            if self._row_enabled(row):
                enabled_count += 1
                self._set_row_status(row, NOT_TESTED)
            else:
                self._set_row_status(row, DISABLED)
        if not enabled_count:
            self.result.setText("No enabled devices to test.")
            return
        self._set_testing_state(True, "Testing enabled devices...")
        config = self.wizard_ref.collect_pages()

        def run_test():
            import erpnext_sync as sync_module
            return self.wizard_ref.controller.test_devices(config, sync_module)

        self._run_worker(run_test, self._finish_all_device_tests)

    def import_devices(self):
        file_dialog = getattr(QtWidgets, "QFileDialog", None)
        if file_dialog is None:
            self.result.setText("Import requires selecting a CSV file.")
            return
        path, _selected_filter = file_dialog.getOpenFileName(self, "Import Devices", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            preview = device_import.parse_csv_file(path, existing_device_ids=self._current_device_ids())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Import Devices", str(exc))
            return
        dialog = DeviceImportPreviewDialog(preview, self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        if not preview.can_apply:
            QtWidgets.QMessageBox.warning(self, "Import Devices", "Fix import errors before importing devices.")
            return
        with self._sorting_suspended():
            for row in preview.valid_rows:
                table_row = self.table.rowCount()
                self.table.insertRow(table_row)
                self._set_row_from_model(table_row, row.to_device_setup(), update_actions=False)
        self.result.setText("Imported: " + str(len(preview.valid_rows)) + " Skipped: 0 Errors: 0")
        self._update_action_state()

    def export_devices(self):
        file_dialog = getattr(QtWidgets, "QFileDialog", None)
        if file_dialog is None:
            self.result.setText("Export requires selecting a destination file.")
            return
        default_name = "Biometric-Devices-" + QtCore.QDate.currentDate().toString("yyyyMMdd") + ".csv"
        path, _selected_filter = file_dialog.getSaveFileName(self, "Export Devices", default_name, "CSV files (*.csv)")
        if not path:
            return
        try:
            self._write_text_file(path, device_import.export_devices_csv(self.to_models()))
            self.result.setText("Devices exported.")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Export Devices", support.file_operation_failed_message("exported").compact())

    def save_template(self):
        file_dialog = getattr(QtWidgets, "QFileDialog", None)
        if file_dialog is None:
            self.result.setText("Template export requires selecting a destination file.")
            return
        path, _selected_filter = file_dialog.getSaveFileName(self, "Save Device Import Template", "Biometric-Device-Import-Template.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            self._write_text_file(path, device_import.template_csv())
            self.result.setText("Template saved.")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Save Template", support.file_operation_failed_message("saved").compact())

    def _item_text(self, row, column):
        item = self.table.item(row, column)
        return item.text() if item else ""

    def _device_id_for_row(self, row):
        item = self.table.item(row, self.COL_ID)
        if item is None:
            return ""
        device_id = item.data(QtCore.Qt.UserRole)
        return str(device_id or item.text() or "").strip()

    def _current_device_ids(self, exclude_row=None):
        return [
            self._device_id_for_row(row)
            for row in range(self.table.rowCount())
            if row != exclude_row
        ]

    def _write_text_file(self, path, contents):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(contents)

    def _model_from_row(self, row):
        device_id = self._device_id_for_row(row)
        existing = {
            device.device_id: device
            for device in self.wizard_ref.setup_config.devices
        }.get(device_id)
        return DeviceSetup(
            name=self._item_text(row, self.COL_NAME),
            device_id=device_id,
            ip=self._item_text(row, self.COL_HOST),
            port=int(self._item_text(row, self.COL_PORT) or "4370"),
            enabled=self._checked(row, self.COL_ENABLED),
            password=self._item_text(row, self.COL_PASSWORD),
            has_existing_password=bool(existing and existing.has_existing_password),
            clear_from_device_on_fetch=self._checked(row, self.COL_CLEAR),
        )

    def _set_row_from_model(self, row, device, update_actions=True):
        with self._sorting_suspended():
            self._suspend_item_updates = True
            try:
                device_id = str(device.device_id or "").strip()
                health = self.health_by_device.get(device_id)
                self.table.setItem(row, self.COL_NAME, self._text_item(device.name, device_id))
                self.table.setItem(row, self.COL_ID, self._text_item(device_id, device_id))
                self.table.setItem(row, self.COL_HOST, self._text_item(device.ip, device_id))
                self.table.setItem(row, self.COL_PORT, self._text_item(str(device.port), device_id))
                self.table.setItem(row, self.COL_ENABLED, self._check_item(device.enabled, device_id))
                self.table.setItem(row, self.COL_PASSWORD, self._text_item("" if device.password in (None, "") else str(device.password), device_id))
                self.table.setItem(row, self.COL_CLEAR, self._check_item(device.clear_from_device_on_fetch, device_id))
                self.table.setItem(row, self.COL_LAST_PULL, self._text_item(format_device_timestamp(getattr(health, "last_pull", "")), device_id))
                self.table.setItem(row, self.COL_LAST_PUSH, self._text_item(format_device_timestamp(getattr(health, "last_push", "")), device_id))
                self.table.setItem(row, self.COL_STATUS, self._text_item(display_connectivity_status(device.enabled, NOT_TESTED), device_id))
            finally:
                self._suspend_item_updates = False
        if update_actions:
            self._update_action_state()

    def _load_device_health(self):
        parent = self.wizard_ref.parent() if hasattr(self.wizard_ref, "parent") else None
        snapshot = getattr(parent, "last_health_snapshot", None)
        devices = getattr(snapshot, "devices", []) if snapshot is not None else []
        return {
            str(getattr(device, "device_id", "") or ""): device
            for device in devices
            if str(getattr(device, "device_id", "") or "")
        }

    def _row_enabled(self, row):
        return self._checked(row, self.COL_ENABLED)

    def _enabled_changed(self, row):
        if row < self.table.rowCount():
            self._set_row_status(row, display_connectivity_status(self._row_enabled(row), self._item_text(row, self.COL_STATUS)))
        self._update_action_state()

    def _handle_item_changed(self, item):
        if self._suspend_item_updates or item is None or item.column() != self.COL_ENABLED:
            return
        self._enabled_changed(item.row())

    def _selected_row(self):
        row = self.table.currentRow()
        return row if row is not None and row >= 0 else -1

    def _update_action_state(self):
        row = self._selected_row()
        has_selection = row >= 0
        self.edit_button.setEnabled(has_selection and not self.device_test_running)
        self.remove_button.setEnabled(has_selection and not self.device_test_running)
        self.test_button.setEnabled(has_selection and self._row_enabled(row) and not self.device_test_running)
        self.test_all_button.setEnabled(not self.device_test_running)

    def _set_row_status(self, row, status):
        device_id = self._device_id_for_row(row)
        self.table.setItem(row, self.COL_STATUS, self._text_item(status, device_id))

    def _set_testing_state(self, running, message):
        self.device_test_running = running
        self.result.setText(message)
        self._update_action_state()

    def _run_worker(self, callback, finished_callback):
        thread = QtCore.QThread(self)
        worker = WizardWorker(callback)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(finished_callback)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._forget_job(thread, worker))
        self.active_jobs.append((thread, worker))
        thread.start()

    def _forget_job(self, thread, worker):
        self.active_jobs = [job for job in self.active_jobs if job != (thread, worker)]

    def _finish_selected_device_test(self, device_id, result):
        row = self._find_row_by_device_id(device_id)
        if isinstance(result, Exception):
            status = UNAVAILABLE
            message = "Device is unavailable. Open Diagnostics or Logs for technical details."
        else:
            status = CONNECTED if result.ok else UNAVAILABLE
            message = result.message if result.ok else friendly_device_failure_message(result)
        self.wizard_ref.setup_config.device_test_states[device_id] = status
        if row >= 0:
            self._set_row_status(row, status)
        self._set_testing_state(False, message)

    def _finish_all_device_tests(self, result):
        disabled_count = 0
        statuses = []
        for row in range(self.table.rowCount()):
            if not self._row_enabled(row):
                disabled_count += 1
                self._set_row_status(row, DISABLED)
                statuses.append(DISABLED)
        if isinstance(result, Exception):
            for row in range(self.table.rowCount()):
                if self._row_enabled(row):
                    device_id = self._device_id_for_row(row)
                    self.wizard_ref.setup_config.device_test_states[device_id] = UNAVAILABLE
                    self._set_row_status(row, UNAVAILABLE)
                    statuses.append(UNAVAILABLE)
            self._set_testing_state(False, bulk_test_summary(statuses) + ". Open Diagnostics or Logs for technical details.")
            return
        status_by_device = self._status_by_device_from_bulk_result(result)
        for row in range(self.table.rowCount()):
            if not self._row_enabled(row):
                continue
            device_id = self._device_id_for_row(row)
            status = status_by_device.get(device_id, UNAVAILABLE)
            self.wizard_ref.setup_config.device_test_states[device_id] = status
            self._set_row_status(row, status)
            statuses.append(status)
        while statuses.count(DISABLED) < disabled_count:
            statuses.append(DISABLED)
        self._set_testing_state(False, bulk_test_summary(statuses))

    def _status_by_device_from_bulk_result(self, result):
        entries = self._bulk_detail_entries(result)
        status_by_device = {}
        device_ids = [
            self._device_id_for_row(row)
            for row in range(self.table.rowCount())
            if self._row_enabled(row)
        ]
        for entry in entries:
            text = str(entry or "")
            for device_id in device_ids:
                if device_id not in status_by_device and _detail_mentions_device(device_id, text):
                    status_by_device[device_id] = _status_from_device_detail(text)
                    break
        return status_by_device

    def _bulk_detail_entries(self, result):
        details = getattr(result, "details", None)
        if isinstance(details, (list, tuple)):
            return [str(detail) for detail in details]
        if details:
            return str(details).splitlines()
        message = getattr(result, "message", "")
        return str(message).splitlines() if message else []

    def _find_row_by_device_id(self, device_id):
        for row in range(self.table.rowCount()):
            if self._device_id_for_row(row) == device_id:
                return row
        return -1

    def _text_item(self, value, device_id=""):
        item = QtWidgets.QTableWidgetItem(str(value))
        item.setData(QtCore.Qt.UserRole, str(device_id or ""))
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable & ~QtCore.Qt.ItemIsUserCheckable)
        return item

    def _check_item(self, checked, device_id=""):
        item = self._text_item("Yes" if checked else "No", device_id)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        return item

    def _checked(self, row, column):
        item = self.table.item(row, column)
        if item is None:
            return False
        return item.checkState() == QtCore.Qt.Checked

    def _sorting_suspended(self):
        return _SortingSuspension(self.table)

    def _configure_table_header(self):
        header = self.table.horizontalHeader()
        if hasattr(header, "setStretchLastSection"):
            header.setStretchLastSection(False)
        header_view = getattr(QtWidgets, "QHeaderView", None)
        if header_view is not None and hasattr(header, "setSectionResizeMode"):
            header.setSectionResizeMode(self.COL_NAME, header_view.Stretch)
            for column in [self.COL_ID, self.COL_HOST, self.COL_STATUS, self.COL_LAST_PULL, self.COL_LAST_PUSH, self.COL_CLEAR]:
                header.setSectionResizeMode(column, header_view.Interactive)
            for column in [self.COL_PORT, self.COL_ENABLED]:
                header.setSectionResizeMode(column, header_view.ResizeToContents)
        for column, width in {
            self.COL_NAME: 220,
            self.COL_ID: 140,
            self.COL_HOST: 150,
            self.COL_PORT: 70,
            self.COL_ENABLED: 80,
            self.COL_STATUS: 170,
            self.COL_LAST_PULL: 130,
            self.COL_LAST_PUSH: 130,
            self.COL_CLEAR: 150,
        }.items():
            self.table.setColumnWidth(column, width)


class _SortingSuspension:
    def __init__(self, table):
        self.table = table
        self.enabled = False
        self.section = 0
        self.order = QtCore.Qt.AscendingOrder

    def __enter__(self):
        if hasattr(self.table, "isSortingEnabled"):
            self.enabled = self.table.isSortingEnabled()
        header = self.table.horizontalHeader() if hasattr(self.table, "horizontalHeader") else None
        if header is not None:
            if hasattr(header, "sortIndicatorSection"):
                self.section = header.sortIndicatorSection()
            if hasattr(header, "sortIndicatorOrder"):
                self.order = header.sortIndicatorOrder()
        if hasattr(self.table, "setSortingEnabled"):
            self.table.setSortingEnabled(False)
        return self

    def __exit__(self, exc_type, exc, traceback):
        if hasattr(self.table, "setSortingEnabled"):
            self.table.setSortingEnabled(self.enabled)
        if self.enabled and hasattr(self.table, "sortItems"):
            self.table.sortItems(self.section, self.order)
        return False


class DeviceDialog(QtWidgets.QDialog):
    def __init__(self, device, parent=None, existing_device_ids=None, original_device_id=""):
        super().__init__(parent)
        self.setWindowTitle("Device")
        self.existing_device_ids = existing_device_ids or []
        self.original_device_id = original_device_id
        self.name = QtWidgets.QLineEdit(device.name)
        self.device_id = QtWidgets.QLineEdit(device.device_id)
        self.ip = QtWidgets.QLineEdit(device.ip)
        self.port = QtWidgets.QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(device.port or 4370))
        self.enabled = QtWidgets.QCheckBox("Enabled")
        self.enabled.setChecked(bool(device.enabled))
        self.password = QtWidgets.QLineEdit("" if device.password in (None, "") else str(device.password))
        self.password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.password_hint = QtWidgets.QLabel("Leave blank to keep the existing device password." if device.has_existing_password else "")
        self.clear_after_fetch = QtWidgets.QCheckBox("Clear attendance after successful fetch")
        self.clear_after_fetch.setChecked(bool(device.clear_from_device_on_fetch))

        form = QtWidgets.QFormLayout()
        form.addRow("Friendly device name", self.name)
        form.addRow("Unique device ID", self.device_id)
        form.addRow("IP / host", self.ip)
        form.addRow("Port", self.port)
        form.addRow("", self.enabled)
        form.addRow("Device password", self.password)
        if device.has_existing_password:
            form.addRow("", self.password_hint)
        form.addRow("", self.clear_after_fetch)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def to_model(self):
        return DeviceSetup(
            name=self.name.text(),
            device_id=self.device_id.text(),
            ip=self.ip.text(),
            port=self.port.value(),
            enabled=self.enabled.isChecked(),
            password=self.password.text(),
            clear_from_device_on_fetch=self.clear_after_fetch.isChecked(),
        )

    def accept(self):
        errors = validate_device_form_values(
            self.name.text(),
            self.device_id.text(),
            self.ip.text(),
            self.port.value(),
            self.existing_device_ids,
        )
        if errors:
            QtWidgets.QMessageBox.warning(self, "Device", "\n".join(errors))
            return
        super().accept()


class DeviceImportPreviewDialog(QtWidgets.QDialog):
    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.preview = preview
        self.setWindowTitle("Import Devices Preview")
        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Row", "Device Name", "Host/IP", "Port", "Device ID", "Enabled", "Status", "Message"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._populate_rows()
        self.summary = QtWidgets.QLabel(self._summary_text())
        self.summary.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setText("Import")
            ok_button.setEnabled(preview.can_apply)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(self.summary)
        layout.addWidget(buttons)

    def _populate_rows(self):
        self.table.setRowCount(len(self.preview.rows))
        for table_row, row in enumerate(self.preview.rows):
            values = [
                row.row_number,
                row.name,
                row.host,
                row.port,
                row.device_id,
                "Yes" if row.enabled is True else "No" if row.enabled is False else row.enabled,
                row.status,
                row.message,
            ]
            for column, value in enumerate(values):
                self.table.setItem(table_row, column, QtWidgets.QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()

    def _summary_text(self):
        return (
            "Valid: "
            + str(len(self.preview.valid_rows))
            + " | Conflicts: "
            + str(self.preview.conflict_count)
            + " | Errors: "
            + str(self.preview.invalid_count)
        )


class SyncPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Synchronization")
        self.start_date = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.start_date.setCalendarPopup(True)
        self.frequency = QtWidgets.QSpinBox()
        self.frequency.setRange(1, 1440)
        self.frequency.setValue(wizard.setup_config.sync.pull_frequency_minutes)
        if wizard.setup_config.sync.import_start_date:
            self.start_date.setDate(QtCore.QDate.fromString(wizard.setup_config.sync.import_start_date, "yyyy-MM-dd"))
        form = QtWidgets.QFormLayout(self)
        form.addRow("Import attendance from date", self.start_date)
        help_text = QtWidgets.QLabel("Attendance records before this date will not be imported.")
        help_text.setWordWrap(True)
        form.addRow(help_text)
        form.addRow("Sync interval in minutes", self.frequency)

    def initializePage(self):
        self.wizard_ref.use_standard_next_button()

    def to_model(self):
        return SyncSetup(
            import_start_date=self.start_date.date().toString("yyyy-MM-dd"),
            pull_frequency_minutes=self.frequency.value(),
        )


class ReviewPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Review")
        self.summary = QtWidgets.QTextEdit()
        self.summary.setReadOnly(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.summary)

    def initializePage(self):
        self.wizard_ref.use_standard_next_button()
        summary = self.wizard_ref.controller.review_summary(self.wizard_ref.collect_pages())
        lines = [
            "ERPNext URL: " + summary["erpnext_url"],
            "ERPNext Status: " + _verification_label(summary["erpnext_tested"], summary["erpnext_ok"]),
            "API Key: " + summary["api_key"],
            "Verify SSL: " + _format_bool(summary["verify_ssl"]),
            "Devices: " + str(summary["device_count"]),
            "Enabled Devices: " + str(summary["enabled_device_count"]),
            "Synchronization Interval: " + str(summary["pull_frequency_minutes"]) + " minutes",
            "Attendance Import Start Date: " + summary["import_start_date"],
        ]
        for device in summary["devices"]:
            lines.append(
                "- "
                + device["name"]
                + " / "
                + device["device_id"]
                + " / "
                + device["ip"]
                + ":"
                + str(device["port"])
                + " - "
                + device["test_state"]
            )
        if summary.get("changes"):
            lines.append("")
            lines.append("Changes:")
            lines.extend("- " + change for change in summary["changes"])
        self.summary.setPlainText("\n".join(lines))


class SaveConfigurationPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Save Configuration")
        self.message = QtWidgets.QLabel("Click Next to save configuration securely and start synchronization.")
        self.message.setWordWrap(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.message)

    def initializePage(self):
        self.wizard().setButtonText(QtWidgets.QWizard.NextButton, self.wizard_ref.save_button_text())

    def cleanupPage(self):
        self.wizard().setButtonText(QtWidgets.QWizard.NextButton, "Next")

    def validatePage(self):
        try:
            parent = self.wizard_ref.parent()
            service_controller = getattr(parent, "controller", None)
            self.wizard_ref.setup_result = self.wizard_ref.controller.complete_setup(
                self.wizard_ref.collect_pages(),
                service_controller=service_controller,
            )
            self.message.setText("Configuration saved securely. Synchronization service is running.")
            return True
        except SetupServiceError as exc:
            WIZARD_LOGGER.exception("Setup saved configuration but could not start synchronization service")
            if getattr(exc, "requires_admin", False):
                title = "Administrator Permission Required"
                message = (
                    "Configuration saved securely, but Windows did not allow the synchronization service to be started or restarted.\n\n"
                    "Open the Manager as Administrator and try again."
                )
            else:
                title = "Synchronization Service Could Not Start"
                message = (
                    "Configuration saved securely, but the synchronization service could not be started or restarted.\n\n"
                    "Open Diagnostics or Logs for technical details, then start the service from the Manager."
                )
            QtWidgets.QMessageBox.warning(self, title, message)
            return False
        except SetupPostSaveError:
            WIZARD_LOGGER.exception("Setup saved configuration but could not confirm final setup state")
            QtWidgets.QMessageBox.warning(
                self,
                "Setup Could Not Be Completed",
                "Configuration saved securely, but setup could not confirm the final running state.\n\nOpen Diagnostics or Logs for technical details.",
            )
            return False
        except Exception as exc:
            WIZARD_LOGGER.exception("Setup validation or save failed")
            QtWidgets.QMessageBox.warning(self, "Configuration Invalid", _configuration_validation_message(exc))
            return False


class CompletionPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Setup Complete")
        self.message = QtWidgets.QLabel("")
        self.message.setWordWrap(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.message)

    def initializePage(self):
        self.wizard().setButtonText(QtWidgets.QWizard.FinishButton, "Open Manager")
        result = self.wizard_ref.setup_result
        if result is None:
            self.message.setText("Setup has not finished yet.")
            return
        lines = [
            "ERPNext connection configured",
            "Biometric devices configured",
            "Configuration saved securely",
            "Synchronization service running",
        ]
        self.message.setText("\n".join(lines))


def _verification_label(tested, ok):
    if not tested:
        return "Not tested"
    return "Verified" if ok else "Test failed"


def _format_bool(value):
    return "Enabled" if value else "Disabled"


def _configuration_validation_message(exc):
    details = [line.strip() for line in str(exc).splitlines() if line.strip()]
    message = support.configuration_invalid_message(details)
    lines = [message.message]
    if details:
        lines.append("")
        lines.append("Details:")
        lines.extend(details)
    if message.action:
        lines.append("")
        lines.append(message.action)
    return "\n".join(lines)


def _detail_mentions_device(device_id, detail):
    if not device_id:
        return False
    pattern = r"(^|[^A-Za-z0-9_])" + re.escape(str(device_id)) + r"([^A-Za-z0-9_]|$)"
    return re.search(pattern, str(detail or "")) is not None


def _status_from_device_detail(detail):
    text = str(detail or "").lower()
    failure_markers = ["failed", "unavailable", "error", "timed out", "timeout", "could not", "can't reach", "cannot reach"]
    if any(marker in text for marker in failure_markers):
        return UNAVAILABLE
    if "connected" in text:
        return CONNECTED
    return UNAVAILABLE
