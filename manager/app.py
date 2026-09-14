import datetime
import logging
import sys
import traceback

from PyQt5 import QtCore, QtWidgets

from config.status import CONFIGURED, INVALID, LEGACY_CONFIGURED, UNCONFIGURED, get_configuration_status
from . import diagnostics
from .health import get_health_snapshot
from .paths import get_config_folder, get_logs_folder, open_folder
from .service_controller import ServiceController


APP_NAME = "Biometric Attendance Sync Manager"
APP_LOGGER = logging.getLogger("manager.app")
APP_LOGGER.addHandler(logging.NullHandler())
APP_LOGGER.propagate = False


class Worker(QtCore.QObject):
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


class SyncManagerWindow(QtWidgets.QMainWindow):
    def __init__(self, controller=None, auto_launch_setup=False):
        super().__init__()
        self.controller = controller or ServiceController()
        self.auto_launch_setup = auto_launch_setup
        self.configuration_status = None
        self.active_jobs = []
        self.sync_running = False
        self.device_connection_status = {}
        self.last_service_status = None
        self.config_module = None
        self.sync_module = None
        self.setWindowTitle(APP_NAME)
        self.resize(760, 680)
        self._build_ui()
        self.refresh()
        if self.auto_launch_setup:
            self._maybe_show_first_run_setup()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(APP_NAME)
        title.setObjectName("title")
        self.refresh_label = QtWidgets.QLabel("Last refreshed: never")
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.refresh_label)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self.service_status = self._value_label()
        self.service_startup = self._value_label()
        self.service_recovery = self._value_label()
        self.last_success = self._value_label()
        self.configuration_state = self._value_label()
        self.configuration_source = self._value_label()
        self.messages = QtWidgets.QTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMinimumHeight(120)

        root.addWidget(self._configuration_group())
        root.addWidget(self._service_group())
        root.addWidget(self._erpnext_group())
        root.addWidget(self._devices_group())
        root.addWidget(self._operations_group())
        root.addWidget(self._messages_group())

        self.setStyleSheet("""
            QWidget { font-size: 10pt; }
            QLabel#title { font-size: 17pt; font-weight: 700; }
            QGroupBox { font-weight: 700; margin-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QLabel[state="ok"] { color: #167a37; font-weight: 700; }
            QLabel[state="warning"] { color: #9a6a00; font-weight: 700; }
            QLabel[state="error"] { color: #b3261e; font-weight: 700; }
            QLabel[state="unknown"] { color: #6b7280; font-weight: 700; }
        """)

    def _configuration_group(self):
        group = QtWidgets.QGroupBox("Configuration")
        layout = QtWidgets.QGridLayout(group)
        layout.addWidget(QtWidgets.QLabel("State:"), 0, 0)
        layout.addWidget(self.configuration_state, 0, 1)
        layout.addWidget(QtWidgets.QLabel("Source:"), 1, 0)
        layout.addWidget(self.configuration_source, 1, 1)
        self.setup_button = QtWidgets.QPushButton("First-Run Setup")
        self.setup_button.clicked.connect(self.show_setup_wizard)
        layout.addWidget(self.setup_button, 0, 2, 2, 1)
        return group

    def _service_group(self):
        group = QtWidgets.QGroupBox("Service")
        layout = QtWidgets.QGridLayout(group)
        layout.addWidget(QtWidgets.QLabel("Status:"), 0, 0)
        layout.addWidget(self.service_status, 0, 1)
        layout.addWidget(QtWidgets.QLabel("Startup:"), 1, 0)
        layout.addWidget(self.service_startup, 1, 1)
        layout.addWidget(QtWidgets.QLabel("Recovery:"), 2, 0)
        layout.addWidget(self.service_recovery, 2, 1)
        layout.addWidget(QtWidgets.QLabel("Last Successful Sync:"), 3, 0)
        layout.addWidget(self.last_success, 3, 1)

        self.start_button = QtWidgets.QPushButton("Start")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.restart_button = QtWidgets.QPushButton("Restart")
        self.install_button = QtWidgets.QPushButton("Install Service")
        self.uninstall_button = QtWidgets.QPushButton("Uninstall Service")
        self.delayed_auto_button = QtWidgets.QPushButton("Set Delayed Auto Start")
        self.recovery_button = QtWidgets.QPushButton("Configure Recovery")
        self.start_button.clicked.connect(lambda: self._run_service_action("Starting service...", self.controller.start_service))
        self.stop_button.clicked.connect(self._confirm_stop)
        self.restart_button.clicked.connect(lambda: self._run_service_action("Restarting service...", self.controller.restart_service))
        self.install_button.clicked.connect(lambda: self._run_service_action("Installing service...", self.controller.install_service))
        self.uninstall_button.clicked.connect(self._confirm_uninstall)
        self.delayed_auto_button.clicked.connect(lambda: self._run_service_action("Setting delayed auto start...", self.controller.set_delayed_auto_start))
        self.recovery_button.clicked.connect(lambda: self._run_service_action("Configuring service recovery...", self.controller.configure_recovery))

        buttons = QtWidgets.QHBoxLayout()
        for button in [self.start_button, self.stop_button, self.restart_button, self.install_button, self.uninstall_button]:
            buttons.addWidget(button)
        layout.addLayout(buttons, 4, 0, 1, 2)
        advanced_buttons = QtWidgets.QHBoxLayout()
        for button in [self.delayed_auto_button, self.recovery_button]:
            advanced_buttons.addWidget(button)
        layout.addLayout(advanced_buttons, 5, 0, 1, 2)
        return group

    def _erpnext_group(self):
        group = QtWidgets.QGroupBox("ERPNext")
        layout = QtWidgets.QHBoxLayout(group)
        self.erp_status = self._value_label("Unknown", "unknown")
        self.erp_button = QtWidgets.QPushButton("Test ERPNext")
        self.erp_button.clicked.connect(lambda: self._run_diagnostic("Testing ERPNext...", diagnostics.test_erpnext_connection, self.erp_button))
        layout.addWidget(QtWidgets.QLabel("Status:"))
        layout.addWidget(self.erp_status)
        layout.addStretch(1)
        layout.addWidget(self.erp_button)
        return group

    def _devices_group(self):
        group = QtWidgets.QGroupBox("Devices")
        layout = QtWidgets.QVBoxLayout(group)
        self.device_table = QtWidgets.QTableWidget(0, 5)
        self.device_table.setHorizontalHeaderLabels(["Device", "Address", "Status", "Last Pull", "Last Push"])
        self.device_table.horizontalHeader().setStretchLastSection(True)
        self.device_table.verticalHeader().setVisible(False)
        self.device_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.device_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.device_button = QtWidgets.QPushButton("Test Devices")
        self.device_button.clicked.connect(lambda: self._run_diagnostic("Testing devices...", diagnostics.test_devices, self.device_button))
        layout.addWidget(self.device_table)
        layout.addWidget(self.device_button, alignment=QtCore.Qt.AlignRight)
        return group

    def _operations_group(self):
        group = QtWidgets.QGroupBox("Operations")
        layout = QtWidgets.QHBoxLayout(group)
        self.sync_button = QtWidgets.QPushButton("Run Sync Now")
        self.validate_button = QtWidgets.QPushButton("Validate Configuration")
        self.logs_button = QtWidgets.QPushButton("Open Logs")
        self.config_button = QtWidgets.QPushButton("Open Config Folder")
        self.sync_button.clicked.connect(self._run_sync_now)
        self.validate_button.clicked.connect(lambda: self._run_diagnostic("Validating configuration...", diagnostics.validate_configuration, self.validate_button))
        self.logs_button.clicked.connect(lambda: self._safe_open_folder(get_logs_folder(self.config_module)))
        self.config_button.clicked.connect(lambda: self._safe_open_folder(get_config_folder()))
        for button in [self.sync_button, self.validate_button, self.logs_button, self.config_button]:
            layout.addWidget(button)
        return group

    def _messages_group(self):
        group = QtWidgets.QGroupBox("Messages / Health")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(self.messages)
        return group

    def refresh(self):
        self._refresh_configuration_status()
        self._load_sync_module_safely()
        status = self.controller.get_status()
        self.last_service_status = status
        self._set_state(self.service_status, status.state, self._state_for_service(status.state))
        self._set_state(self.service_startup, status.startup, "unknown")
        self._set_state(self.service_recovery, status.recovery, "unknown")

        if self.sync_module:
            health = get_health_snapshot(self.config_module, self.sync_module)
        else:
            health = None
        self.last_success.setText(health.last_successful_sync if health else "Unknown")
        self._populate_devices(health.devices if health else [])
        self.refresh_label.setText("Last refreshed: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if health and health.warnings:
            self._append_message("Warnings:\n" + "\n".join("- " + warning for warning in health.warnings))
        elif self.sync_module:
            self._append_message("Health refreshed.")
        self._apply_button_policy(status)

    def _load_sync_module_safely(self):
        if self.configuration_status and self.configuration_status.state == UNCONFIGURED:
            self.sync_module = None
            self.config_module = None
            self._append_message("Product is not configured. Complete first-run setup.")
            return
        try:
            config_folder = get_config_folder()
            if config_folder.exists() and str(config_folder) not in sys.path:
                # Packaged service mode keeps credentials outside the program files.
                sys.path.insert(0, str(config_folder))
            import erpnext_sync
            self.sync_module = erpnext_sync
            self.config_module = erpnext_sync.config
        except Exception:
            self.sync_module = None
            self.config_module = None
            self._append_message("Configuration could not be loaded. The manager can still control the Windows service.")

    def _refresh_configuration_status(self):
        self.configuration_status = get_configuration_status()
        state = self.configuration_status.state
        if state == CONFIGURED:
            label = "Configured"
            style = "ok"
        elif state == LEGACY_CONFIGURED:
            label = "Configured"
            style = "warning"
        elif state == INVALID:
            label = "Invalid"
            style = "error"
        else:
            label = "Not Configured"
            style = "warning"
        self._set_state(self.configuration_state, label, style)
        self._set_state(self.configuration_source, self.configuration_status.message, style)

    def _maybe_show_first_run_setup(self):
        if self.configuration_status and self.configuration_status.state == UNCONFIGURED:
            self.show_setup_wizard()

    def show_setup_wizard(self):
        try:
            from .setup.wizard import SetupWizard
            wizard = SetupWizard(self)
            if wizard.exec_() == QtWidgets.QDialog.Accepted:
                self._append_message("Setup completed successfully.")
                self.refresh()
        except Exception:
            APP_LOGGER.exception("Could not open first-run setup wizard")
            self._append_message("Could not open first-run setup wizard. Check manager.log.")

    def _populate_devices(self, devices):
        self.device_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            values = [
                device.device_id,
                str(device.ip) + ":" + str(device.port) if device.ip else "",
                self.device_connection_status.get(device.device_id, "Unknown"),
                device.last_pull or "Never",
                device.last_push or "Never",
            ]
            for column, value in enumerate(values):
                if column == 2:
                    self.device_table.setItem(row, column, self._device_status_item(value))
                else:
                    self.device_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        self.device_table.resizeColumnsToContents()

    def _run_service_action(self, running_message, callback):
        self._set_busy(True)
        self._append_message(running_message)
        self._run_worker(callback, self._handle_action_result)

    def _run_diagnostic(self, running_message, callback, button):
        button.setEnabled(False)
        self._append_message(running_message)
        self._run_worker(callback, lambda result: self._handle_diagnostic_result(result, button))

    def _run_sync_now(self):
        if self.configuration_status and not self.configuration_status.configured:
            self._append_message("Complete first-run setup before running sync.")
            return
        if self.last_service_status and self.last_service_status.state == "Running":
            self._append_message("Stop the Windows service before running a manual sync.")
            return
        if self.sync_running:
            self._append_message("Manual sync is already running.")
            return
        self.sync_running = True
        self.sync_button.setEnabled(False)
        self._append_message("Running manual sync...")
        self._run_worker(diagnostics.run_one_sync, lambda result: self._handle_diagnostic_result(result, self.sync_button, is_sync=True))

    def _run_worker(self, callback, finished_callback):
        thread = QtCore.QThread(self)
        worker = Worker(callback)
        job = {"thread": thread, "worker": worker, "callback": finished_callback}
        self.active_jobs.append(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda checked=False, finished_job=job: self._forget_job(finished_job))
        thread.start()

    @QtCore.pyqtSlot(object)
    def _handle_worker_finished(self, result):
        worker = self.sender()
        job = self._find_job_by_worker(worker)
        if job is None:
            APP_LOGGER.error("Background worker finished without an active job reference")
            self._recover_controls_after_handler_failure()
            return

        try:
            job["callback"](result)
        except Exception:
            APP_LOGGER.exception("Background result handler failed")
            diagnostics._get_logger(self.config_module).exception("Background result handler failed")
            self._append_message("Operation finished, but the manager could not update the result. Check manager.log.")
            self._recover_controls_after_handler_failure()

    def _handle_action_result(self, result):
        if isinstance(result, Exception):
            self._append_message("Action failed.")
        else:
            self._append_message(result.message)
        self.refresh()

    def _handle_diagnostic_result(self, result, button, is_sync=False):
        if isinstance(result, Exception):
            self._append_message("Operation failed.")
            detail = traceback.format_exception_only(type(result), result)[-1].strip()
            self._append_message(detail)
        else:
            self._append_message(result.message)
            for line in result.details:
                self._append_message("- " + line)
            if button is self.erp_button:
                self._set_state(self.erp_status, "Connected" if result.ok else "Failed", result.status)
            if button is self.device_button:
                self._store_device_test_results(result.details)
        if is_sync:
            self.sync_running = False
        button.setEnabled(True)
        self.refresh()

    def _confirm_uninstall(self):
        answer = QtWidgets.QMessageBox.question(
            self,
            "Uninstall Service",
            "Uninstall ERPNext Biometric Push Service?\n\nThis stops automatic attendance synchronization until the service is installed again.",
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self._run_service_action("Uninstalling service...", self.controller.uninstall_service)

    def _confirm_stop(self):
        answer = QtWidgets.QMessageBox.question(
            self,
            "Stop Service",
            "Stop ERPNext Biometric Push Service?\n\nAutomatic attendance synchronization will pause until it is started again.",
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self._run_service_action("Stopping service...", self.controller.stop_service)

    def _safe_open_folder(self, folder):
        try:
            open_folder(folder)
            self._append_message("Opened " + str(folder))
        except Exception:
            self._append_message("Could not open folder: " + str(folder))

    def _set_busy(self, busy):
        if busy:
            for button in self._service_action_buttons():
                button.setEnabled(False)
            return
        self._apply_button_policy(self.last_service_status)

    def _recover_controls_after_handler_failure(self):
        self._set_busy(False)
        self.sync_running = False
        for button in [self.erp_button, self.device_button, self.validate_button, self.sync_button]:
            button.setEnabled(True)
        self._apply_button_policy(self.last_service_status)

    def _append_message(self, message):
        self.messages.append(str(message))

    def _set_state(self, label, text, state):
        label.setText(text)
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)

    def _value_label(self, text="Unknown", state="unknown"):
        label = QtWidgets.QLabel(text)
        label.setProperty("state", state)
        return label

    def _state_for_service(self, state):
        if state == "Running":
            return "ok"
        if state == "Stopped":
            return "error"
        if state == "Not Installed":
            return "unknown"
        return "warning"

    def _find_job_by_worker(self, worker):
        for job in self.active_jobs:
            if job["worker"] is worker:
                return job
        return None

    def _forget_job(self, job):
        if job in self.active_jobs:
            self.active_jobs.remove(job)

    def _store_device_test_results(self, details):
        for detail in details:
            device_id, status = self._parse_device_result(detail)
            if not device_id:
                continue
            self.device_connection_status[device_id] = status

    def _device_status_item(self, status):
        item = QtWidgets.QTableWidgetItem(status)
        if status == "Connected":
            item.setForeground(QtCore.Qt.darkGreen)
        elif status == "Failed":
            item.setForeground(QtCore.Qt.red)
        elif status == "Unknown":
            item.setForeground(QtCore.Qt.gray)
        return item

    def _parse_device_result(self, detail):
        if " - Connected" in detail:
            return detail.split(" ", 1)[0], "Connected"
        if " - Connection failed" in detail:
            return detail.split(" ", 1)[0], "Failed"
        return "", ""

    def _service_action_buttons(self):
        return [
            self.start_button,
            self.stop_button,
            self.restart_button,
            self.install_button,
            self.uninstall_button,
            self.delayed_auto_button,
            self.recovery_button,
        ]

    def _apply_button_policy(self, status):
        if status is None:
            return
        installed = bool(status.installed)
        running = status.state == "Running"
        stopped = status.state == "Stopped"
        configured = True if self.configuration_status is None else self.configuration_status.configured
        self.start_button.setEnabled(configured and installed and not running)
        self.stop_button.setEnabled(installed and not stopped)
        self.restart_button.setEnabled(installed)
        self.install_button.setEnabled(not installed)
        self.uninstall_button.setEnabled(installed)
        self.delayed_auto_button.setEnabled(installed)
        self.recovery_button.setEnabled(installed)
        self.sync_button.setEnabled(configured and (not running) and (not self.sync_running))
        if not configured:
            self._set_tooltip(self.sync_button, "Complete first-run setup before running sync.")
        else:
            self._set_tooltip(self.sync_button, "Stop the Windows service before running a manual sync." if running else "")

    def _set_tooltip(self, widget, message):
        if hasattr(widget, "setToolTip"):
            widget.setToolTip(message)


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = SyncManagerWindow(auto_launch_setup=True)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
