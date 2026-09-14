"""PyQt first-run setup wizard.

The widget layer intentionally stays thin: validation, review data, and file
writes live in manager.setup.controller so they can be tested without a desktop.
"""

from PyQt5 import QtCore, QtWidgets

from .controller import SetupController
from .model import DeviceSetup, ERPNextSetup, SetupConfiguration, SyncSetup


class SetupWizard(QtWidgets.QWizard):
    def __init__(self, parent=None, controller=None):
        super().__init__(parent)
        self.controller = controller or SetupController()
        self.setup_config = SetupConfiguration(devices=[DeviceSetup()])
        self.setWindowTitle("Biometric Attendance Sync Setup")
        self.setWizardStyle(QtWidgets.QWizard.ModernStyle)
        self.addPage(WelcomePage())
        self.erp_page = ERPNextPage(self)
        self.devices_page = DevicesPage(self)
        self.sync_page = SyncPage(self)
        self.review_page = ReviewPage(self)
        self.finish_page = FinishPage(self)
        self.addPage(self.erp_page)
        self.addPage(self.devices_page)
        self.addPage(self.sync_page)
        self.addPage(self.review_page)
        self.addPage(self.finish_page)

    def collect_pages(self):
        self.setup_config.erpnext = self.erp_page.to_model()
        self.setup_config.devices = self.devices_page.to_models()
        self.setup_config.sync = self.sync_page.to_model()
        return self.setup_config


class WelcomePage(QtWidgets.QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Biometric Attendance Sync")
        self.setSubTitle("Connect ZKTeco attendance devices with ERPNext / Frappe HRMS.")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("This setup will create the commercial configuration file."))


class ERPNextPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("ERPNext Connection")
        self.url = QtWidgets.QLineEdit()
        self.api_key = QtWidgets.QLineEdit()
        self.api_secret = QtWidgets.QLineEdit()
        self.api_secret.setEchoMode(QtWidgets.QLineEdit.Password)
        self.verify_ssl = QtWidgets.QCheckBox("Verify SSL")
        self.verify_ssl.setChecked(True)
        self.timeout = QtWidgets.QSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setValue(30)
        self.test_button = QtWidgets.QPushButton("Test Connection")
        self.test_result = QtWidgets.QLabel("")
        self.test_button.clicked.connect(self.test_connection)

        form = QtWidgets.QFormLayout(self)
        form.addRow("ERPNext URL", self.url)
        form.addRow("API Key / API User", self.api_key)
        form.addRow("API Secret", self.api_secret)
        form.addRow("", self.verify_ssl)
        form.addRow("Request Timeout", self.timeout)
        form.addRow(self.test_button, self.test_result)

    def to_model(self):
        return ERPNextSetup(
            url=self.url.text(),
            api_key=self.api_key.text(),
            api_secret=self.api_secret.text(),
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
        self.test_result.setText(result.message)


class DevicesPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Attendance Devices")
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Device ID", "IP / Hostname", "Port", "Enabled", "Password"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_button = QtWidgets.QPushButton("Add Device")
        self.remove_button = QtWidgets.QPushButton("Remove Device")
        self.test_button = QtWidgets.QPushButton("Test Connection")
        self.result = QtWidgets.QLabel("")
        self.add_button.clicked.connect(lambda: self.add_device(DeviceSetup()))
        self.remove_button.clicked.connect(self.remove_selected_device)
        self.test_button.clicked.connect(self.test_devices)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        buttons.addWidget(self.result)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.add_device(DeviceSetup())

    def add_device(self, device):
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [device.name, device.device_id, device.ip, str(device.port), device.enabled, str(device.password)]
        for column, value in enumerate(values):
            if column == 4:
                widget = QtWidgets.QCheckBox()
                widget.setChecked(bool(value))
                self.table.setCellWidget(row, column, widget)
            elif column == 5:
                widget = QtWidgets.QLineEdit(value)
                widget.setEchoMode(QtWidgets.QLineEdit.Password)
                self.table.setCellWidget(row, column, widget)
            else:
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    def remove_selected_device(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def to_models(self):
        devices = []
        for row in range(self.table.rowCount()):
            password_widget = self.table.cellWidget(row, 5)
            enabled_widget = self.table.cellWidget(row, 4)
            devices.append(DeviceSetup(
                name=self._item_text(row, 0),
                device_id=self._item_text(row, 1),
                ip=self._item_text(row, 2),
                port=int(self._item_text(row, 3) or "4370"),
                enabled=enabled_widget.isChecked() if enabled_widget else True,
                password=int(password_widget.text() or "0") if password_widget else 0,
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

    def test_devices(self):
        try:
            import erpnext_sync as sync_module
            result = self.wizard_ref.controller.test_devices(self.wizard_ref.collect_pages(), sync_module)
            self.result.setText(result.message)
        except Exception:
            self.result.setText("Device test failed.")

    def _item_text(self, row, column):
        item = self.table.item(row, column)
        return item.text() if item else ""


class SyncPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Synchronization")
        self.start_date = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.start_date.setCalendarPopup(True)
        self.frequency = QtWidgets.QSpinBox()
        self.frequency.setRange(1, 1440)
        self.frequency.setValue(60)
        form = QtWidgets.QFormLayout(self)
        form.addRow("Attendance Import Start Date", self.start_date)
        form.addRow(QtWidgets.QLabel("Attendance records before this date will not be imported."))
        form.addRow("Synchronization Interval", self.frequency)

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
            "API Key / User: " + summary["api_key"],
            "Verify SSL: " + str(summary["verify_ssl"]),
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
            )
        self.summary.setPlainText("\n".join(lines))


class FinishPage(QtWidgets.QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.wizard_ref = wizard
        self.setTitle("Validate & Finish")
        self.message = QtWidgets.QLabel("Click Finish to validate and save configuration.")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.message)

    def validatePage(self):
        try:
            target = self.wizard_ref.controller.write_config_atomic(self.wizard_ref.collect_pages())
            self.message.setText("Setup completed successfully. Configuration saved to " + str(target))
            return True
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Configuration", str(exc))
            return False
