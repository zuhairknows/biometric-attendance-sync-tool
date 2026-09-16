import datetime
import logging
import sys
from dataclasses import dataclass, field

from PyQt5 import QtCore, QtWidgets

from config.status import CONFIGURED, INVALID, LEGACY_CONFIGURED, UNCONFIGURED, get_configuration_status
from . import config_admin
from . import diagnostics
from . import support
from .health import get_health_snapshot
from .paths import get_app_data_folder, get_config_folder, get_logs_folder, get_programdata_backups_folder, open_folder
from .service_controller import ServiceController, ServiceStatus


APP_NAME = "Biometric Attendance Sync Manager"
APP_LOGGER = logging.getLogger("manager.app")
APP_LOGGER.addHandler(logging.NullHandler())
APP_LOGGER.propagate = False

HEALTHY = "HEALTHY"
WARNING = "WARNING"
ERROR = "ERROR"
UNKNOWN = "UNKNOWN"

ERP_NOT_TESTED = "not_tested"
ERP_CONNECTED = "connected"
ERP_FAILED = "failed"


@dataclass
class ComponentPresentation:
    text: str
    detail: str = ""
    state: str = "unknown"


@dataclass
class DeviceDashboardSummary:
    configured: int = 0
    enabled: int = 0
    reachable: int = 0
    unavailable: int = 0
    not_tested: int = 0


@dataclass
class DashboardPresentation:
    system_state: str = UNKNOWN
    system_text: str = "Unknown"
    system_detail: str = "System status has not been refreshed yet."
    erpnext: ComponentPresentation = field(default_factory=lambda: ComponentPresentation("Not tested", "Run Test ERPNext to verify connectivity."))
    service: ComponentPresentation = field(default_factory=lambda: ComponentPresentation("Unknown", "Service status has not been refreshed."))
    devices: DeviceDashboardSummary = field(default_factory=DeviceDashboardSummary)
    last_sync_text: str = "No successful synchronization recorded"
    warnings: list = field(default_factory=list)
    action_text: str = ""


@dataclass
class DashboardSnapshot:
    configuration_status: object = None
    service_status: object = None
    health: object = None
    configuration_summary: dict = field(default_factory=dict)
    config_module: object = None
    sync_module: object = None
    messages: list = field(default_factory=list)
    error: object = None


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
        self._setup_auto_launched = False
        self.configuration_status = None
        self.active_jobs = []
        self.sync_running = False
        self.refresh_running = False
        self.device_connection_status = {}
        self.erpnext_connection_state = ERP_NOT_TESTED
        self.erpnext_connection_detail = "Run Test ERPNext to verify connectivity."
        self.last_service_status = None
        self.last_health_snapshot = None
        self.last_configuration_summary = {}
        self.last_dashboard = DashboardPresentation()
        self.config_module = None
        self.sync_module = None
        self.setWindowTitle(APP_NAME)
        self.resize(900, 760)
        self._build_ui()
        self.refresh()
        if self.auto_launch_setup:
            self._maybe_show_first_run_setup()

    def _build_ui(self):
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        central = QtWidgets.QWidget()
        size_policy = getattr(QtWidgets, "QSizePolicy", None)
        if size_policy is not None:
            central.setSizePolicy(size_policy.Ignored, size_policy.Preferred)
        scroll_area.setWidget(central)
        self.scroll_area = scroll_area
        self.setCentralWidget(scroll_area)
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
        self.configuration_erpnext_url = self._value_label()
        self.configuration_ssl = self._value_label()
        self.configuration_devices = self._value_label()
        self.configuration_interval = self._value_label()
        self.configuration_import_start = self._value_label()
        self.configuration_schema = self._value_label()
        self.configuration_updated = self._value_label()
        self.configuration_credentials = self._value_label()
        self.system_status = self._value_label("Unknown", "unknown")
        self.system_detail = QtWidgets.QLabel("System status has not been refreshed yet.")
        self.system_detail.setWordWrap(True)
        self.erp_card_status = self._value_label("Not tested", "unknown")
        self.erp_card_detail = QtWidgets.QLabel("Run Test ERPNext to verify connectivity.")
        self.erp_card_detail.setWordWrap(True)
        self.service_card_status = self._value_label("Unknown", "unknown")
        self.service_card_detail = QtWidgets.QLabel("Service status has not been refreshed.")
        self.service_card_detail.setWordWrap(True)
        self.devices_card_status = self._value_label("0 configured", "unknown")
        self.devices_card_detail = QtWidgets.QLabel("No device status has been refreshed yet.")
        self.devices_card_detail.setWordWrap(True)
        self.last_sync_card_status = self._value_label("No successful synchronization recorded", "unknown")
        self.last_sync_card_detail = QtWidgets.QLabel("")
        self.last_sync_card_detail.setWordWrap(True)
        self.action_required = QtWidgets.QLabel("")
        self.action_required.setWordWrap(True)
        self.messages = QtWidgets.QTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMinimumHeight(120)

        root.addWidget(self._system_status_group())
        root.addWidget(self._dashboard_cards_group())
        root.addWidget(self._primary_actions_group())
        root.addWidget(self._devices_group())
        root.addWidget(self._configuration_group())
        root.addWidget(self._advanced_group())
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

    def _system_status_group(self):
        group = QtWidgets.QGroupBox("System Status")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(self.system_status)
        layout.addWidget(self.system_detail)
        layout.addWidget(self.action_required)
        return group

    def _dashboard_cards_group(self):
        group = QtWidgets.QGroupBox("Operational Dashboard")
        layout = QtWidgets.QGridLayout(group)
        layout.addWidget(self._status_card("ERPNext", self.erp_card_status, self.erp_card_detail), 0, 0)
        layout.addWidget(self._status_card("Synchronization Service", self.service_card_status, self.service_card_detail), 1, 0)
        layout.addWidget(self._status_card("Biometric Devices", self.devices_card_status, self.devices_card_detail), 2, 0)
        layout.addWidget(self._status_card("Last Synchronization", self.last_sync_card_status, self.last_sync_card_detail), 3, 0)
        return group

    def _status_card(self, title, value_label, detail_label):
        group = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(value_label)
        layout.addWidget(detail_label)
        return group

    def _primary_actions_group(self):
        group = QtWidgets.QGroupBox("Primary Actions")
        layout = QtWidgets.QVBoxLayout(group)
        self.sync_button = QtWidgets.QPushButton("Sync Now")
        self.setup_button = QtWidgets.QPushButton("Configure")
        self.primary_refresh_button = QtWidgets.QPushButton("Refresh")
        self.erp_button = QtWidgets.QPushButton("Test ERPNext")
        self.device_button = QtWidgets.QPushButton("Test Devices")
        self.diagnostics_button = QtWidgets.QPushButton("Diagnostics")
        self.logs_button = QtWidgets.QPushButton("Logs")
        self.about_button = QtWidgets.QPushButton("About")
        self.sync_button.clicked.connect(self._run_sync_now)
        self.setup_button.clicked.connect(self.show_setup_wizard)
        self.primary_refresh_button.clicked.connect(self.refresh)
        self.erp_button.clicked.connect(lambda: self._run_diagnostic("Testing ERPNext...", diagnostics.test_erpnext_connection, self.erp_button))
        self.device_button.clicked.connect(lambda: self._run_diagnostic("Testing devices...", diagnostics.test_devices, self.device_button))
        self.diagnostics_button.clicked.connect(self._show_diagnostics)
        self.logs_button.clicked.connect(lambda: self._safe_open_folder(get_logs_folder(self.config_module)))
        self.about_button.clicked.connect(self._show_about)
        layout.addLayout(self._button_grid([
            self.sync_button,
            self.setup_button,
            self.primary_refresh_button,
            self.erp_button,
            self.device_button,
            self.diagnostics_button,
            self.logs_button,
            self.about_button,
        ], columns=2))
        return group

    def _configuration_group(self):
        group = QtWidgets.QGroupBox("Configuration")
        layout = QtWidgets.QGridLayout(group)
        layout.addWidget(QtWidgets.QLabel("State:"), 0, 0)
        layout.addWidget(self.configuration_state, 0, 1)
        layout.addWidget(QtWidgets.QLabel("Source:"), 1, 0)
        layout.addWidget(self.configuration_source, 1, 1)
        layout.addWidget(QtWidgets.QLabel("ERPNext URL:"), 2, 0)
        layout.addWidget(self.configuration_erpnext_url, 2, 1)
        layout.addWidget(QtWidgets.QLabel("SSL Verification:"), 3, 0)
        layout.addWidget(self.configuration_ssl, 3, 1)
        layout.addWidget(QtWidgets.QLabel("Devices:"), 4, 0)
        layout.addWidget(self.configuration_devices, 4, 1)
        layout.addWidget(QtWidgets.QLabel("Synchronization Interval:"), 5, 0)
        layout.addWidget(self.configuration_interval, 5, 1)
        layout.addWidget(QtWidgets.QLabel("Attendance Import Start Date:"), 6, 0)
        layout.addWidget(self.configuration_import_start, 6, 1)
        layout.addWidget(QtWidgets.QLabel("Schema Version:"), 7, 0)
        layout.addWidget(self.configuration_schema, 7, 1)
        layout.addWidget(QtWidgets.QLabel("Last Configuration Update:"), 8, 0)
        layout.addWidget(self.configuration_updated, 8, 1)
        layout.addWidget(QtWidgets.QLabel("ERPNext Credentials:"), 9, 0)
        layout.addWidget(self.configuration_credentials, 9, 1)
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

        layout.addLayout(self._button_grid([
            self.start_button,
            self.stop_button,
            self.restart_button,
            self.install_button,
            self.uninstall_button,
        ], columns=2), 4, 0, 1, 2)
        layout.addLayout(self._button_grid([
            self.delayed_auto_button,
            self.recovery_button,
        ], columns=2), 5, 0, 1, 2)
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
        layout.addWidget(self.device_table)
        return group

    def _advanced_group(self):
        group = QtWidgets.QGroupBox("Advanced / Administration")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(self._service_group())
        self.validate_button = QtWidgets.QPushButton("Validate Configuration")
        self.config_button = QtWidgets.QPushButton("Open Config Folder")
        self.appdata_button = QtWidgets.QPushButton("Open Application Data")
        self.backup_button = QtWidgets.QPushButton("Back Up Configuration")
        self.restore_button = QtWidgets.QPushButton("Restore Configuration")
        self.reset_button = QtWidgets.QPushButton("Reset Configuration")
        self.backup_folder_button = QtWidgets.QPushButton("Open Backup Folder")
        self.validate_button.clicked.connect(lambda: self._run_diagnostic("Validating configuration...", diagnostics.validate_configuration, self.validate_button))
        self.config_button.clicked.connect(lambda: self._safe_open_folder(get_config_folder()))
        self.appdata_button.clicked.connect(lambda: self._safe_open_folder(get_app_data_folder()))
        self.backup_button.clicked.connect(self._backup_configuration)
        self.restore_button.clicked.connect(self._restore_configuration)
        self.reset_button.clicked.connect(self._reset_configuration)
        self.backup_folder_button.clicked.connect(lambda: self._safe_open_folder(get_programdata_backups_folder()))
        layout.addLayout(self._button_grid([
            self.validate_button,
            self.config_button,
            self.appdata_button,
            self.backup_button,
            self.restore_button,
            self.reset_button,
            self.backup_folder_button,
        ], columns=2))
        return group

    def _messages_group(self):
        group = QtWidgets.QGroupBox("Messages / Health")
        layout = QtWidgets.QVBoxLayout(group)
        layout.addWidget(self.messages)
        return group

    def _button_grid(self, buttons, columns=4):
        layout = QtWidgets.QGridLayout()
        for index, button in enumerate(buttons):
            layout.addWidget(button, index // columns, index % columns)
        return layout

    def refresh(self):
        if self.refresh_running:
            self._append_message("Refresh is already running.")
            return
        self.refresh_running = True
        self.refresh_button.setEnabled(False)
        if hasattr(self, "primary_refresh_button"):
            self.primary_refresh_button.setEnabled(False)
        self.refresh_label.setText("Refreshing...")
        self._run_worker(self._collect_refresh_snapshot, self._handle_refresh_result)

    def _collect_refresh_snapshot(self):
        snapshot = DashboardSnapshot()
        try:
            snapshot.configuration_status = get_configuration_status()
        except Exception as exc:
            snapshot.error = exc
            snapshot.messages.append("Configuration status could not be refreshed.")
            snapshot.configuration_status = None

        snapshot.config_module, snapshot.sync_module, runtime_messages = self._load_runtime_modules_for_snapshot(snapshot.configuration_status)
        snapshot.messages.extend(runtime_messages)

        try:
            snapshot.service_status = self.controller.get_status()
        except Exception as exc:
            snapshot.error = exc
            snapshot.messages.append("Synchronization service status could not be refreshed.")
            snapshot.service_status = ServiceStatus(installed=False, state="Unknown", startup="Unknown", recovery="Unknown")

        if snapshot.sync_module:
            try:
                snapshot.health = get_health_snapshot(snapshot.config_module, snapshot.sync_module)
            except Exception as exc:
                snapshot.error = exc
                snapshot.messages.append("Health information could not be refreshed.")
                snapshot.health = None

        try:
            snapshot.configuration_summary = config_admin.get_configuration_summary(
                status=snapshot.configuration_status,
                service_status=snapshot.service_status,
            )
        except Exception as exc:
            snapshot.error = exc
            snapshot.messages.append("Configuration summary could not be refreshed.")
            snapshot.configuration_summary = {}
        return snapshot

    def _load_runtime_modules_for_snapshot(self, configuration_status):
        messages = []
        if configuration_status and configuration_status.state == UNCONFIGURED:
            messages.append("Product is not configured. Complete first-run setup.")
            return None, None, messages
        if configuration_status and configuration_status.state == INVALID:
            messages.append("Configuration is invalid. Complete setup or repair protected secrets.")
            messages.extend(configuration_status.details or [])
            return None, None, messages
        try:
            config_folder = get_config_folder()
            if config_folder.exists() and str(config_folder) not in sys.path:
                # Packaged service mode keeps credentials outside the program files.
                sys.path.insert(0, str(config_folder))
            import erpnext_sync
            return erpnext_sync.config, erpnext_sync, messages
        except Exception:
            messages.append("Configuration could not be loaded. The manager can still control the synchronization service.")
            return None, None, messages

    def _handle_refresh_result(self, result):
        self.refresh_running = False
        self.refresh_button.setEnabled(True)
        if hasattr(self, "primary_refresh_button"):
            self.primary_refresh_button.setEnabled(True)
        if isinstance(result, Exception):
            APP_LOGGER.exception("Refresh worker failed", exc_info=(type(result), result, result.__traceback__))
            snapshot = DashboardSnapshot(
                service_status=ServiceStatus(installed=False, state="Unknown", startup="Unknown", recovery="Unknown"),
                error=result,
                messages=["Refresh failed. The dashboard is showing the last known safe state."],
            )
        else:
            snapshot = result
        self._apply_refresh_snapshot(snapshot)

    def _apply_refresh_snapshot(self, snapshot):
        self.configuration_status = snapshot.configuration_status
        self.last_service_status = snapshot.service_status
        self.last_health_snapshot = snapshot.health
        self.last_configuration_summary = snapshot.configuration_summary or {}
        self.config_module = snapshot.config_module
        self.sync_module = snapshot.sync_module

        self._refresh_configuration_status_from_status(snapshot.configuration_status)
        service_status = snapshot.service_status or ServiceStatus(installed=False, state="Unknown", startup="Unknown", recovery="Unknown")
        self._set_state(self.service_status, service_status.state, self._state_for_service(service_status.state))
        self._set_state(self.service_startup, service_status.startup, "unknown")
        self._set_state(self.service_recovery, service_status.recovery, "unknown")

        health = snapshot.health
        self.last_success.setText(format_sync_timestamp(health.last_successful_sync) if health and health.last_successful_sync else "No successful synchronization recorded")
        self._populate_devices(health.devices if health else [])
        self.refresh_label.setText("Last refreshed: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        for message in snapshot.messages:
            self._append_message(message)
        if health and health.warnings:
            self._append_message("Warnings:\n" + "\n".join("- " + warning for warning in health.warnings))
        elif self.sync_module:
            self._append_message("Health refreshed.")
        self._populate_configuration_summary(service_status, self.last_configuration_summary)
        self._render_dashboard()
        self._apply_button_policy(service_status)
        if self.auto_launch_setup and not self._setup_auto_launched:
            self._setup_auto_launched = True
            self._maybe_show_first_run_setup()

    def _load_sync_module_safely(self):
        if self.configuration_status and self.configuration_status.state == UNCONFIGURED:
            self.sync_module = None
            self.config_module = None
            self._append_message("Product is not configured. Complete first-run setup.")
            return
        if self.configuration_status and self.configuration_status.state == INVALID:
            self.sync_module = None
            self.config_module = None
            self._append_message("Configuration is invalid. Complete setup or repair protected secrets.")
            for detail in self.configuration_status.details:
                self._append_message(detail)
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
        self._refresh_configuration_status_from_status(self.configuration_status)

    def _refresh_configuration_status_from_status(self, status):
        if status is None:
            self._set_state(self.configuration_state, "Unknown", "unknown")
            self._set_state(self.configuration_source, "Configuration status is unavailable.", "unknown")
            self.setup_button.setText("Configure")
            return
        state = status.state
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
        self._set_state(self.configuration_source, status.message, style)
        self.setup_button.setText(self._setup_button_text(state))

    def _maybe_show_first_run_setup(self):
        if self.configuration_status and self.configuration_status.state == UNCONFIGURED:
            self.show_setup_wizard()

    def show_setup_wizard(self):
        try:
            from .setup.wizard import SetupWizard
            wizard = SetupWizard(self)
            if wizard.exec_() == QtWidgets.QDialog.Accepted:
                self._append_message("Setup completed successfully.")
                self._maybe_prompt_restart_after_config_save()
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
        self.sync_button.setText("Syncing...")
        self.erp_button.setEnabled(False)
        self.device_button.setEnabled(False)
        self._append_message("Running manual synchronization...")
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
            self._append_message("Action failed. Open Diagnostics or Logs for technical details.")
        else:
            if getattr(result, "success", False):
                self._append_message(result.message)
            else:
                message = support.service_action_failed_message("complete the requested action", getattr(result, "requires_admin", False), getattr(result, "message", ""))
                self._append_message(message.compact())
        self.refresh()

    def _handle_diagnostic_result(self, result, button, is_sync=False):
        if isinstance(result, Exception):
            self._append_message("Operation failed.")
            APP_LOGGER.exception("Diagnostic operation failed", exc_info=(type(result), result, result.__traceback__))
            diagnostics._get_logger(self.config_module).exception("Diagnostic operation failed")
            self._append_message("Open Diagnostics or Logs for technical details.")
            if button is self.erp_button:
                self.erpnext_connection_state = ERP_FAILED
                self.erpnext_connection_detail = "Connection failed. Open Diagnostics or Logs for technical details."
        else:
            if is_sync and result.ok:
                self._append_message("Synchronization completed successfully.")
            elif is_sync:
                self._append_message("Synchronization could not be completed. Open Diagnostics or Logs for technical details.")
            else:
                self._append_message(self._diagnostic_message_text(result))
            for line in result.details:
                self._append_message("- " + line)
            if button is self.erp_button:
                self.erpnext_connection_state = ERP_CONNECTED if result.ok else ERP_FAILED
                self.erpnext_connection_detail = self._diagnostic_message_text(result)
            if button is self.device_button:
                self._store_device_test_results(result.details)
        if is_sync:
            self.sync_running = False
            self.sync_button.setText("Sync Now")
            self.erp_button.setEnabled(True)
            self.device_button.setEnabled(True)
        button.setEnabled(True)
        self.refresh()

    def _confirm_uninstall(self):
        answer = QtWidgets.QMessageBox.question(
            self,
            "Uninstall Service",
            "Uninstall the Synchronization Service?\n\nThis stops automatic attendance synchronization until the service is installed again.",
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self._run_service_action("Uninstalling service...", self.controller.uninstall_service)

    def _confirm_stop(self):
        answer = QtWidgets.QMessageBox.question(
            self,
            "Stop Service",
            "Stop the Synchronization Service?\n\nAutomatic attendance synchronization will pause until it is started again.",
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self._run_service_action("Stopping service...", self.controller.stop_service)

    def _safe_open_folder(self, folder):
        try:
            open_folder(folder)
            self._append_message("Opened " + str(folder))
        except Exception:
            self._append_message("Could not open folder. Check permissions and try again.")

    def _backup_configuration(self):
        try:
            path = config_admin.create_configuration_backup()
            self._append_message("Configuration backup created: " + str(path))
        except Exception as exc:
            APP_LOGGER.exception("Configuration backup failed")
            self._append_message(support.file_operation_failed_message("backed up").compact())

    def _restore_configuration(self):
        try:
            file_dialog = getattr(QtWidgets, "QFileDialog", None)
            if file_dialog is None:
                self._append_message("Restore requires selecting a backup file.")
                return
            path, _selected_filter = file_dialog.getOpenFileName(self, "Restore Configuration", str(get_programdata_backups_folder()), "Zip files (*.zip)")
            if not path:
                return
            config_admin.restore_configuration_backup(path)
            self._append_message("Configuration restored successfully.")
            self._maybe_prompt_restart_after_config_save()
            self.refresh()
        except Exception:
            APP_LOGGER.exception("Configuration restore failed")
            self._append_message(support.file_operation_failed_message("restored").compact())

    def _reset_configuration(self):
        answer = QtWidgets.QMessageBox.question(
            self,
            "Reset Configuration",
            "This will remove the commercial configuration and protected credentials.\n\nThe synchronization service will stop and the product will return to Not Configured state.\n\nContinue?",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            backup_path = config_admin.reset_commercial_configuration(service_controller=self.controller)
            message = "Configuration reset successfully."
            if backup_path:
                message += " Backup created: " + str(backup_path)
            self._append_message(message)
            self.refresh()
        except Exception:
            APP_LOGGER.exception("Configuration reset failed")
            self._append_message("Configuration could not be reset. Open Diagnostics or Logs for technical details.")

    def _export_diagnostics(self):
        try:
            path = config_admin.export_diagnostics_report(service_status=self.last_service_status)
            self._append_message("Diagnostics exported: " + str(path))
            return path
        except Exception:
            APP_LOGGER.exception("Diagnostics export failed")
            self._append_message("Could not export diagnostics. Check folder permissions and open Logs for technical details.")
            return None

    def _show_diagnostics(self):
        dialog = DiagnosticsDialog(self)
        dialog.exec_()

    def _copy_diagnostics_summary(self):
        text = config_admin.build_diagnostics_summary_text(
            self.configuration_status,
            self.last_service_status,
            self.last_health_snapshot,
            self.last_configuration_summary,
            self.last_dashboard.erpnext.text if self.last_dashboard else "Not tested",
        )
        app = QtWidgets.QApplication.instance()
        clipboard = app.clipboard() if app is not None and hasattr(app, "clipboard") else None
        if clipboard is not None:
            clipboard.setText(text)
            self._append_message("Diagnostics summary copied.")
            return text
        self._append_message("Clipboard is not available. Diagnostics summary was not copied.")
        return text

    def _maybe_prompt_restart_after_config_save(self):
        if not self.last_service_status or self.last_service_status.state != "Running":
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Restart Service",
            "Configuration saved successfully.\n\nRestart the synchronization service now to apply changes?",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            self._append_message("Restart skipped. Restart the service later to apply changes.")
            return
        result = self.controller.restart_service()
        if result.success:
            self._append_message("Service restarted successfully.")
        else:
            self._append_message("Configuration was saved, but the Windows service could not be restarted. Review the service status and logs.")

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

    def _diagnostic_message_text(self, result):
        title = str(getattr(result, "title", "") or "").strip()
        message = str(getattr(result, "message", "") or "").strip()
        action = str(getattr(result, "action", "") or "").strip()
        if not title:
            return message
        text = title + ": " + message
        if action:
            text += " Suggested action: " + action
        return text

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
        if state == "Unknown":
            return "unknown"
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
        commercial_json = bool(self.configuration_status and self.configuration_status.source == "json")
        unconfigured = bool(self.configuration_status and self.configuration_status.state == UNCONFIGURED)
        legacy = bool(self.configuration_status and self.configuration_status.state == LEGACY_CONFIGURED)
        self.backup_button.setEnabled(commercial_json)
        self.restore_button.setEnabled(True)
        self.reset_button.setEnabled(commercial_json or unconfigured)
        if legacy:
            self.reset_button.setEnabled(False)
        if not configured:
            self._set_tooltip(self.sync_button, "Complete first-run setup before running sync.")
        else:
            self._set_tooltip(self.sync_button, "Stop the Windows service before running a manual sync." if running else "")

    def _populate_configuration_summary(self, service_status, summary=None):
        summary = summary or config_admin.get_configuration_summary(status=self.configuration_status, service_status=service_status)
        self.configuration_erpnext_url.setText(str(summary.get("erpnext_url") or "Unknown"))
        self.configuration_ssl.setText(_format_bool(summary.get("verify_ssl")))
        self.configuration_devices.setText(str(summary.get("enabled_devices", 0)) + " enabled / " + str(summary.get("total_devices", 0)) + " total")
        interval = summary.get("sync_interval_minutes")
        self.configuration_interval.setText((str(interval) + " minutes") if interval not in ("", None) else "Unknown")
        self.configuration_import_start.setText(str(summary.get("import_start_date") or "Unknown"))
        self.configuration_schema.setText(str(summary.get("schema_version") or "Unknown"))
        self.configuration_updated.setText(str(summary.get("last_updated_at") or "Unknown"))
        self.configuration_credentials.setText("Configured" if summary.get("credentials_configured") else "Not Configured")

    def _setup_button_text(self, state):
        if state == CONFIGURED:
            return "Edit Configuration"
        if state == INVALID:
            return "Repair Configuration"
        return "Start Setup"

    def _set_tooltip(self, widget, message):
        if hasattr(widget, "setToolTip"):
            widget.setToolTip(message)

    def _render_dashboard(self):
        dashboard = build_dashboard_presentation(
            self.configuration_status,
            self.last_service_status,
            self.last_health_snapshot,
            self.last_configuration_summary,
            self.erpnext_connection_state,
            self.erpnext_connection_detail,
            self.device_connection_status,
        )
        self.last_dashboard = dashboard
        self._set_state(self.system_status, dashboard.system_text, _ui_state_for_system(dashboard.system_state))
        self.system_detail.setText(dashboard.system_detail)
        self.action_required.setText(dashboard.action_text)
        self._set_state(self.erp_card_status, dashboard.erpnext.text, dashboard.erpnext.state)
        self.erp_card_detail.setText(dashboard.erpnext.detail)
        self._set_state(self.service_card_status, dashboard.service.text, dashboard.service.state)
        self.service_card_detail.setText(dashboard.service.detail)
        self._set_state(self.devices_card_status, str(dashboard.devices.configured) + " configured", _device_summary_state(dashboard.devices))
        self.devices_card_detail.setText(
            "Enabled: "
            + str(dashboard.devices.enabled)
            + " | Reachable: "
            + str(dashboard.devices.reachable)
            + " | Unavailable: "
            + str(dashboard.devices.unavailable)
            + " | Not tested: "
            + str(dashboard.devices.not_tested)
        )
        self._set_state(self.last_sync_card_status, dashboard.last_sync_text, "ok" if dashboard.last_sync_text != "No successful synchronization recorded" else "unknown")
        self.last_sync_card_detail.setText("\n".join(dashboard.warnings))

    def _show_about(self):
        version_text = ""
        try:
            from version import PRODUCT_VERSION
            version_text = "\nVersion: " + str(PRODUCT_VERSION)
        except Exception:
            version_text = ""
        QtWidgets.QMessageBox.information(
            self,
            "About",
            "Biometric Attendance Sync"
            + version_text
            + "\n\nSynchronizes attendance from biometric devices to ERPNext.",
        )


class DiagnosticsDialog(QtWidgets.QDialog):
    def __init__(self, manager_window):
        super().__init__(manager_window)
        self.manager_window = manager_window
        self.setWindowTitle("Diagnostics")
        self.summary = QtWidgets.QTextEdit()
        self.summary.setReadOnly(True)
        self._refresh_summary()

        self.validate_button = QtWidgets.QPushButton("Validate Configuration")
        self.erp_button = QtWidgets.QPushButton("Test ERPNext")
        self.devices_button = QtWidgets.QPushButton("Test Devices")
        self.refresh_button = QtWidgets.QPushButton("Refresh Diagnostics")
        self.export_button = QtWidgets.QPushButton("Export Diagnostics")
        self.copy_button = QtWidgets.QPushButton("Copy Diagnostics Summary")
        self.logs_button = QtWidgets.QPushButton("Open Logs")
        self.close_button = QtWidgets.QPushButton("Close")

        self.validate_button.clicked.connect(lambda: manager_window._run_diagnostic("Validating configuration...", diagnostics.validate_configuration, manager_window.validate_button))
        self.erp_button.clicked.connect(lambda: manager_window._run_diagnostic("Testing ERPNext...", diagnostics.test_erpnext_connection, manager_window.erp_button))
        self.devices_button.clicked.connect(lambda: manager_window._run_diagnostic("Testing devices...", diagnostics.test_devices, manager_window.device_button))
        self.refresh_button.clicked.connect(manager_window.refresh)
        self.export_button.clicked.connect(manager_window._export_diagnostics)
        self.copy_button.clicked.connect(manager_window._copy_diagnostics_summary)
        self.logs_button.clicked.connect(lambda: manager_window._safe_open_folder(get_logs_folder(manager_window.config_module)))
        self.close_button.clicked.connect(self.accept)

        buttons = QtWidgets.QHBoxLayout()
        for button in [self.validate_button, self.erp_button, self.devices_button, self.refresh_button, self.export_button, self.copy_button, self.logs_button, self.close_button]:
            buttons.addWidget(button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addLayout(buttons)

    def _refresh_summary(self):
        text = config_admin.build_diagnostics_summary_text(
            self.manager_window.configuration_status,
            self.manager_window.last_service_status,
            self.manager_window.last_health_snapshot,
            self.manager_window.last_configuration_summary,
            self.manager_window.last_dashboard.erpnext.text if self.manager_window.last_dashboard else "Not tested",
        )
        self.summary.setText(text)


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = SyncManagerWindow(auto_launch_setup=True)
    window.show()
    sys.exit(app.exec_())


def _format_bool(value):
    if value is True:
        return "Enabled"
    if value is False:
        return "Disabled"
    return "Unknown"


def build_dashboard_presentation(configuration_status, service_status, health, configuration_summary, erpnext_state, erpnext_detail, device_connection_status):
    configuration_summary = configuration_summary or {}
    device_summary = build_device_dashboard_summary(configuration_summary, health, device_connection_status)
    erpnext = build_erpnext_presentation(configuration_status, erpnext_state, erpnext_detail)
    service = build_service_presentation(service_status)
    warnings = list(getattr(health, "warnings", []) or [])
    last_sync_text = "No successful synchronization recorded"
    if health and getattr(health, "last_successful_sync", ""):
        last_sync_text = format_sync_timestamp(health.last_successful_sync)

    system_state, system_detail = derive_system_state(
        configuration_status,
        service_status,
        warnings,
        erpnext_state,
        device_summary,
    )
    action_text = build_action_text(system_state, warnings, erpnext_state, device_summary)
    return DashboardPresentation(
        system_state=system_state,
        system_text=_system_text(system_state),
        system_detail=system_detail,
        erpnext=erpnext,
        service=service,
        devices=device_summary,
        last_sync_text=last_sync_text,
        warnings=warnings,
        action_text=action_text,
    )


def derive_system_state(configuration_status, service_status, warnings, erpnext_state, device_summary):
    if configuration_status is None:
        return UNKNOWN, "Configuration status is unavailable."
    if configuration_status.state == INVALID:
        return ERROR, "Configuration is invalid. Open Configure to repair it."
    if configuration_status.state == UNCONFIGURED:
        return UNKNOWN, "Configuration is not complete yet."
    if not configuration_status.configured:
        return UNKNOWN, "System status cannot be determined until configuration is complete."
    if service_status is None or service_status.state == "Unknown":
        return UNKNOWN, "Synchronization service status is unavailable."
    if not service_status.installed:
        return ERROR, "Synchronization service is not installed."
    if service_status.state != "Running":
        return ERROR, "Synchronization service is not running."
    if erpnext_state == ERP_FAILED:
        return WARNING, "ERPNext connection test failed. Synchronization service is running, but action may be needed."
    if device_summary.unavailable > 0:
        return WARNING, "One or more biometric devices failed the latest connection test."
    if warnings:
        return WARNING, "Synchronization is running, but there are attendance records that need attention."
    return HEALTHY, "Configuration is valid and synchronization service is running."


def build_erpnext_presentation(configuration_status, erpnext_state, erpnext_detail):
    if configuration_status is None or not configuration_status.configured:
        return ComponentPresentation("Not configured", "Complete configuration before testing ERPNext.", "unknown")
    if erpnext_state == ERP_CONNECTED:
        return ComponentPresentation("Connected", erpnext_detail or "ERPNext connection test succeeded.", "ok")
    if erpnext_state == ERP_FAILED:
        return ComponentPresentation("Connection failed", erpnext_detail or "Run Diagnostics or open Logs for details.", "error")
    return ComponentPresentation("Not tested", "Run Test ERPNext to verify connectivity.", "unknown")


def build_service_presentation(service_status):
    if service_status is None:
        return ComponentPresentation("Unknown", "Synchronization service status has not been refreshed.", "unknown")
    state = service_status.state or "Unknown"
    detail = "Startup: " + str(service_status.startup or "Unknown") + " | Recovery: " + str(service_status.recovery or "Unknown")
    return ComponentPresentation(state, detail, _service_component_state(service_status))


def build_device_dashboard_summary(configuration_summary, health, device_connection_status):
    configured = int(configuration_summary.get("total_devices") or 0)
    enabled = int(configuration_summary.get("enabled_devices") or 0)
    device_ids = []
    if health is not None:
        device_ids = [device.device_id for device in getattr(health, "devices", [])]
        configured = max(configured, len(device_ids))
    reachable = 0
    unavailable = 0
    for device_id in device_ids:
        status = device_connection_status.get(device_id)
        if status == "Connected":
            reachable += 1
        elif status == "Failed":
            unavailable += 1
    not_tested = max(enabled - reachable - unavailable, 0)
    return DeviceDashboardSummary(
        configured=configured,
        enabled=enabled,
        reachable=reachable,
        unavailable=unavailable,
        not_tested=not_tested,
    )


def format_sync_timestamp(value):
    if not value:
        return "No successful synchronization recorded"
    text = str(value)
    try:
        parsed = datetime.datetime.fromisoformat(text)
        return parsed.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return text


def build_action_text(system_state, warnings, erpnext_state, device_summary):
    actions = []
    if system_state == HEALTHY:
        return "No action required."
    if erpnext_state == ERP_FAILED:
        actions.append("Check ERPNext connection.")
    if device_summary.unavailable:
        actions.append("Check unavailable biometric devices.")
    if warnings:
        actions.extend(warnings)
    if not actions and system_state == ERROR:
        actions.append("Open Configure, Diagnostics, or Logs for next steps.")
    if not actions and system_state == UNKNOWN:
        actions.append("Refresh or run tests to verify current status.")
    return "Action needed: " + " ".join(actions)


def _system_text(system_state):
    labels = {
        HEALTHY: "Healthy",
        WARNING: "Warning",
        ERROR: "Error",
        UNKNOWN: "Unknown",
    }
    return labels.get(system_state, "Unknown")


def _ui_state_for_system(system_state):
    if system_state == HEALTHY:
        return "ok"
    if system_state == WARNING:
        return "warning"
    if system_state == ERROR:
        return "error"
    return "unknown"


def _service_component_state(service_status):
    if service_status.state == "Running":
        return "ok"
    if service_status.state in ("Stopped", "Not Installed"):
        return "error"
    if service_status.state == "Unknown":
        return "unknown"
    return "warning"


def _device_summary_state(summary):
    if summary.unavailable:
        return "warning"
    if summary.reachable:
        return "ok"
    return "unknown"


if __name__ == "__main__":
    main()
