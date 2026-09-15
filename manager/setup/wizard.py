"""PyQt first-run setup wizard.

The widget layer intentionally stays thin: validation, review data, and file
writes live in manager.setup.controller so they can be tested without a desktop.
"""

from PyQt5 import QtCore, QtWidgets

from .. import device_import
from config.status import INVALID
from .controller import SetupController
from .model import DeviceSetup, ERPNextSetup, SetupConfiguration, SyncSetup


class SetupWizard(QtWidgets.QWizard):
    def __init__(self, parent=None, controller=None):
        super().__init__(parent)
        self.controller = controller or SetupController()
        self.setup_config = self.controller.load_existing_setup_config()
        self.setup_result = None
        self.setWindowTitle(self._window_title())
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.addPage(WelcomePage())
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

    def _window_title(self):
        status = getattr(self.parent(), "configuration_status", None) if hasattr(self, "parent") else None
        if status is not None and status.state == INVALID:
            return "Repair Configuration"
        if self.controller.paths.get_config_path().is_file():
            return "Edit Configuration"
        return "Biometric Attendance Sync Setup"

    def collect_pages(self):
        self.setup_config.erpnext = self.erp_page.to_model()
        self.setup_config.devices = self.devices_page.to_models()
        self.setup_config.sync = self.sync_page.to_model()
        return self.setup_config


class WelcomePage(QtWidgets.QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome")
        self.setSubTitle("Set up Biometric Attendance Sync for your ERPNext site.")
        layout = QtWidgets.QVBoxLayout(self)
        message = QtWidgets.QLabel(
            "This guided setup helps you connect ERPNext, add attendance devices, choose sync settings, and confirm automatic synchronization is running."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

    def initializePage(self):
        self.wizard().setButtonText(QtWidgets.QWizard.NextButton, "Get Started")

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
            QtWidgets.QMessageBox.warning(self, "Configuration", str(exc))
            return False
        return True

    def test_connection(self):
        config = self.wizard_ref.collect_pages()
        result = self.wizard_ref.controller.test_erpnext(config)
        self.wizard_ref.setup_config.erpnext_tested = True
        self.wizard_ref.setup_config.erpnext_ok = bool(result.ok)
        self.test_result.setText(result.message)


class DevicesPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Attendance Devices")
        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Name", "Device ID", "IP / Hostname", "Port", "Enabled", "Password", "Clear After Fetch", "Test Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_button = QtWidgets.QPushButton("Add Device")
        self.edit_button = QtWidgets.QPushButton("Edit Device")
        self.remove_button = QtWidgets.QPushButton("Remove Device")
        self.test_button = QtWidgets.QPushButton("Test Selected Device")
        self.import_button = QtWidgets.QPushButton("Import Devices")
        self.export_button = QtWidgets.QPushButton("Export Devices")
        self.template_button = QtWidgets.QPushButton("Save Template")
        self.result = QtWidgets.QLabel("")
        self.add_button.clicked.connect(self.add_device_dialog)
        self.edit_button.clicked.connect(self.edit_selected_device)
        self.remove_button.clicked.connect(self.remove_selected_device)
        self.test_button.clicked.connect(self.test_selected_device)
        self.import_button.clicked.connect(self.import_devices)
        self.export_button.clicked.connect(self.export_devices)
        self.template_button.clicked.connect(self.save_template)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.test_button)
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

    def add_device(self, device):
        row = self.table.rowCount()
        self.table.insertRow(row)
        test_state = self.wizard_ref.setup_config.device_test_states.get(device.device_id, "Not tested")
        values = [device.name, device.device_id, device.ip, str(device.port), device.enabled, "" if device.password in (None, "") else str(device.password), device.clear_from_device_on_fetch, test_state]
        for column, value in enumerate(values):
            if column in (4, 6):
                widget = QtWidgets.QCheckBox()
                widget.setChecked(bool(value))
                self.table.setCellWidget(row, column, widget)
            elif column == 5:
                widget = QtWidgets.QLineEdit(value)
                widget.setEchoMode(QtWidgets.QLineEdit.Password)
                self.table.setCellWidget(row, column, widget)
            else:
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    def add_device_dialog(self):
        dialog = DeviceDialog(DeviceSetup(), self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.add_device(dialog.to_model())

    def edit_selected_device(self):
        row = self.table.currentRow()
        if row < 0:
            self.result.setText("Select a device to edit.")
            return
        dialog = DeviceDialog(self._model_from_row(row), self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._set_row_from_model(row, dialog.to_model())

    def remove_selected_device(self):
        row = self.table.currentRow()
        if row >= 0:
            device_id = self._item_text(row, 1) or "this device"
            answer = QtWidgets.QMessageBox.question(
                self,
                "Remove Device",
                "Remove device " + device_id + " from the configuration?",
            )
            if answer == QtWidgets.QMessageBox.Yes:
                self.table.removeRow(row)

    def to_models(self):
        devices = []
        existing_by_id = {
            device.device_id: device
            for device in self.wizard_ref.setup_config.devices
        }
        for row in range(self.table.rowCount()):
            password_widget = self.table.cellWidget(row, 5)
            enabled_widget = self.table.cellWidget(row, 4)
            clear_widget = self.table.cellWidget(row, 6)
            device_id = self._item_text(row, 1)
            existing_device = existing_by_id.get(device_id)
            devices.append(DeviceSetup(
                name=self._item_text(row, 0),
                device_id=device_id,
                ip=self._item_text(row, 2),
                port=int(self._item_text(row, 3) or "4370"),
                enabled=enabled_widget.isChecked() if enabled_widget else True,
                password=password_widget.text() if password_widget else "",
                has_existing_password=bool(existing_device and existing_device.has_existing_password),
                clear_from_device_on_fetch=clear_widget.isChecked() if clear_widget else False,
            ))
        return devices

    def validatePage(self):
        config = self.wizard_ref.collect_pages()
        try:
            self.wizard_ref.controller.validate(config)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Configuration", str(exc))
            return False
        return True

    def test_selected_device(self):
        row = self.table.currentRow()
        if row < 0:
            self.result.setText("Select a device to test.")
            return
        device_id = self._item_text(row, 1)
        try:
            import erpnext_sync as sync_module
            result = self.wizard_ref.controller.test_device(self.wizard_ref.collect_pages(), device_id, sync_module)
            self.result.setText(result.message)
            state = "Connected" if result.ok else "Failed"
            self.wizard_ref.setup_config.device_test_states[device_id] = state
            self.table.setItem(row, 7, QtWidgets.QTableWidgetItem(state))
        except Exception:
            self.result.setText("Device test failed.")
            self.wizard_ref.setup_config.device_test_states[device_id] = "Failed"
            self.table.setItem(row, 7, QtWidgets.QTableWidgetItem("Failed"))

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
        for row in preview.valid_rows:
            self.add_device(row.to_device_setup())
        self.result.setText("Imported: " + str(len(preview.valid_rows)) + " Skipped: 0 Errors: 0")

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
            QtWidgets.QMessageBox.warning(self, "Export Devices", "Could not export devices. " + str(exc))

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
            QtWidgets.QMessageBox.warning(self, "Save Template", "Could not save template. " + str(exc))

    def _item_text(self, row, column):
        item = self.table.item(row, column)
        return item.text() if item else ""

    def _current_device_ids(self):
        return [self._item_text(row, 1) for row in range(self.table.rowCount())]

    def _write_text_file(self, path, contents):
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(contents)

    def _model_from_row(self, row):
        password_widget = self.table.cellWidget(row, 5)
        enabled_widget = self.table.cellWidget(row, 4)
        clear_widget = self.table.cellWidget(row, 6)
        return DeviceSetup(
            name=self._item_text(row, 0),
            device_id=self._item_text(row, 1),
            ip=self._item_text(row, 2),
            port=int(self._item_text(row, 3) or "4370"),
            enabled=enabled_widget.isChecked() if enabled_widget else True,
            password=password_widget.text() if password_widget else "",
            clear_from_device_on_fetch=clear_widget.isChecked() if clear_widget else False,
        )

    def _set_row_from_model(self, row, device):
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(device.name))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(device.device_id))
        self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(device.ip))
        self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(device.port)))
        enabled_widget = self.table.cellWidget(row, 4)
        if enabled_widget:
            enabled_widget.setChecked(device.enabled)
        password_widget = self.table.cellWidget(row, 5)
        if password_widget:
            password_widget.setText("" if device.password in (None, "") else str(device.password))
        clear_widget = self.table.cellWidget(row, 6)
        if clear_widget:
            clear_widget.setChecked(device.clear_from_device_on_fetch)
        self.table.setItem(row, 7, QtWidgets.QTableWidgetItem("Not tested"))


class DeviceDialog(QtWidgets.QDialog):
    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Device")
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
        self.clear_after_fetch = QtWidgets.QCheckBox("Clear attendance after successful fetch")
        self.clear_after_fetch.setChecked(bool(device.clear_from_device_on_fetch))

        form = QtWidgets.QFormLayout()
        form.addRow("Friendly device name", self.name)
        form.addRow("Unique device ID", self.device_id)
        form.addRow("IP / host", self.ip)
        form.addRow("Port", self.port)
        form.addRow("", self.enabled)
        form.addRow("Device password", self.password)
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
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Setup", str(exc))
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
