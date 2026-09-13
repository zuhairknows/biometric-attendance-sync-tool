import importlib
import logging
import shutil
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock


class FakeBoundSignal:
    def __init__(self, owner):
        self.owner = owner
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            receiver = getattr(callback, "__self__", None)
            old_sender = getattr(receiver, "_sender", None) if receiver is not None else None
            if receiver is not None:
                receiver._sender = self.owner
            try:
                callback(*args)
            finally:
                if receiver is not None:
                    receiver._sender = old_sender


class FakeSignal:
    def __init__(self, *args):
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        signal = instance.__dict__.get(self.name)
        if signal is None:
            signal = FakeBoundSignal(instance)
            instance.__dict__[self.name] = signal
        return signal


class FakeQObject:
    def __init__(self, *args, **kwargs):
        self._sender = None

    def moveToThread(self, thread):
        self.thread = thread

    def deleteLater(self, *args):
        pass

    def sender(self):
        return self._sender


class FakeQThread(FakeQObject):
    started = FakeSignal()
    finished = FakeSignal()

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self.started.emit)
        self._thread.start()

    def quit(self, *args):
        self.finished.emit()

    def wait(self, timeout=1000):
        if self._thread:
            self._thread.join(timeout / 1000)


class FakeWidget(FakeQObject):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.enabled = True
        self.text_value = ""
        self.properties = {}
        self._style = types.SimpleNamespace(unpolish=lambda _widget: None, polish=lambda _widget: None)

    def setEnabled(self, enabled):
        self.enabled = enabled

    def isEnabled(self):
        return self.enabled

    def setText(self, text):
        self.text_value = str(text)

    def text(self):
        return self.text_value

    def setObjectName(self, name):
        self.object_name = name

    def setProperty(self, key, value):
        self.properties[key] = value

    def style(self):
        return self._style

    def setStyleSheet(self, stylesheet):
        self.stylesheet = stylesheet

    def setToolTip(self, tooltip):
        self.tooltip = tooltip

    def toolTip(self):
        return getattr(self, "tooltip", "")

    def close(self):
        pass

    def show(self):
        pass


class FakeQMainWindow(FakeWidget):
    def setWindowTitle(self, title):
        self.title = title

    def resize(self, width, height):
        self.size = (width, height)

    def setCentralWidget(self, widget):
        self.central = widget


class FakeLayout:
    def __init__(self, *args, **kwargs):
        self.items = []

    def setContentsMargins(self, *args):
        pass

    def setSpacing(self, *args):
        pass

    def addWidget(self, widget, *args, **kwargs):
        self.items.append(widget)

    def addLayout(self, layout, *args, **kwargs):
        self.items.append(layout)

    def addStretch(self, *args):
        pass


class FakeButton(FakeWidget):
    def __init__(self, text="", *args, **kwargs):
        super().__init__()
        self.text_value = text
        self.clicked = FakeBoundSignal(self)


class FakeTextEdit(FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.lines = []

    def setReadOnly(self, value):
        self.read_only = value

    def setMinimumHeight(self, value):
        self.minimum_height = value

    def append(self, text):
        self.lines.append(str(text))


class FakeHeader:
    def setStretchLastSection(self, value):
        self.stretch_last = value

    def setVisible(self, value):
        self.visible = value


class FakeTableItem:
    def __init__(self, text):
        self.text_value = str(text)
        self.foreground = None

    def text(self):
        return self.text_value

    def setForeground(self, color):
        self.foreground = color


class FakeTable(FakeWidget):
    def __init__(self, rows=0, columns=0, *args, **kwargs):
        super().__init__()
        self.rows = rows
        self.columns = columns
        self.items = {}
        self.horizontal = FakeHeader()
        self.vertical = FakeHeader()

    def setHorizontalHeaderLabels(self, labels):
        self.labels = labels

    def horizontalHeader(self):
        return self.horizontal

    def verticalHeader(self):
        return self.vertical

    def setEditTriggers(self, value):
        self.edit_triggers = value

    def setSelectionBehavior(self, value):
        self.selection_behavior = value

    def setRowCount(self, rows):
        self.rows = rows
        self.items = {key: value for key, value in self.items.items() if key[0] < rows}

    def rowCount(self):
        return self.rows

    def setItem(self, row, column, item):
        self.items[(row, column)] = item

    def item(self, row, column):
        return self.items.get((row, column))

    def resizeColumnsToContents(self):
        pass


class FakeQApplication:
    _instance = None

    def __init__(self, *args, **kwargs):
        FakeQApplication._instance = self

    @staticmethod
    def instance():
        return FakeQApplication._instance

    def processEvents(self):
        pass

    def exec_(self):
        return 0


def pyqt_slot(*args, **kwargs):
    def decorator(function):
        return function
    return decorator


def install_fake_pyqt():
    qtcore = types.ModuleType("PyQt5.QtCore")
    qtcore.QObject = FakeQObject
    qtcore.QThread = FakeQThread
    qtcore.pyqtSignal = FakeSignal
    qtcore.pyqtSlot = pyqt_slot
    qtcore.Qt = types.SimpleNamespace(AlignRight=1, darkGreen="darkGreen", red="red", gray="gray")

    qtwidgets = types.ModuleType("PyQt5.QtWidgets")
    qtwidgets.QApplication = FakeQApplication
    qtwidgets.QMainWindow = FakeQMainWindow
    qtwidgets.QWidget = FakeWidget
    qtwidgets.QVBoxLayout = FakeLayout
    qtwidgets.QHBoxLayout = FakeLayout
    qtwidgets.QGridLayout = FakeLayout
    qtwidgets.QLabel = FakeWidget
    qtwidgets.QGroupBox = FakeWidget
    qtwidgets.QPushButton = FakeButton
    qtwidgets.QTextEdit = FakeTextEdit
    qtwidgets.QTableWidget = FakeTable
    qtwidgets.QTableWidgetItem = FakeTableItem
    qtwidgets.QAbstractItemView = types.SimpleNamespace(NoEditTriggers=1, SelectRows=2)
    qtwidgets.QMessageBox = types.SimpleNamespace(Yes=1, question=lambda *args, **kwargs: 1)

    pyqt = types.ModuleType("PyQt5")
    pyqt.QtCore = qtcore
    pyqt.QtWidgets = qtwidgets
    sys.modules["PyQt5"] = pyqt
    sys.modules["PyQt5.QtCore"] = qtcore
    sys.modules["PyQt5.QtWidgets"] = qtwidgets


def wait_until(condition, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


install_fake_pyqt()
app_module = importlib.import_module("manager.app")
from manager.diagnostics import DiagnosticResult
from manager.health import DeviceHealth
from manager.service_controller import ServiceStatus


class FakeController:
    def __init__(self, status=None):
        self.status = status or ServiceStatus(installed=False, state="Not Installed", startup="Not Installed")

    def get_status(self):
        return self.status


class TestableSyncManagerWindow(app_module.SyncManagerWindow):
    logs_directory = ".test-logs"

    def _load_sync_module_safely(self):
        self.config_module = types.SimpleNamespace(LOGS_DIRECTORY=self.logs_directory)
        self.sync_module = types.SimpleNamespace(config=self.config_module)

    def refresh(self):
        self._load_sync_module_safely()
        status = self.controller.get_status()
        self.last_service_status = status
        self._set_state(self.service_status, status.state, self._state_for_service(status.state))
        self._set_state(self.service_startup, status.startup, "unknown")
        self.last_success.setText("2026-09-12 14:15:26")
        self._populate_devices([
            DeviceHealth("FP1_DEVICE_01", "10.0.0.20", 4371),
            DeviceHealth("FP1_DEVICE_02", "10.0.0.21", 4371),
        ])
        self.refresh_label.setText("Last refreshed: test")
        self._apply_button_policy(status)


class ManagerAppWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.logs_directory = Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)
        TestableSyncManagerWindow.logs_directory = str(self.logs_directory)
        self.window = TestableSyncManagerWindow(controller=FakeController())

    def tearDown(self):
        for job in list(self.window.active_jobs):
            job["thread"].quit()
            job["thread"].wait(1000)
        self.window.close()
        logger = logging.getLogger("manager")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)

    def test_worker_remains_referenced_while_background_job_is_running(self):
        release = threading.Event()
        started = threading.Event()

        def blocking_callback():
            started.set()
            release.wait(1)
            return "done"

        self.window._run_worker(blocking_callback, lambda _result: None)

        self.assertTrue(wait_until(started.is_set))
        self.assertEqual(len(self.window.active_jobs), 1)
        self.assertIsNotNone(self.window.active_jobs[0]["worker"])

        release.set()
        self.assertTrue(wait_until(lambda: len(self.window.active_jobs) == 0))

    def test_worker_reference_is_removed_after_thread_finishes(self):
        self.window._run_worker(lambda: "done", lambda _result: None)

        self.assertTrue(wait_until(lambda: len(self.window.active_jobs) == 0))

    def test_successful_diagnostic_calls_completion_handler(self):
        handler = mock.Mock()

        self.window._run_worker(lambda: "ok", handler)

        self.assertTrue(wait_until(lambda: handler.call_count == 1))
        handler.assert_called_once_with("ok")
        self.assertTrue(wait_until(lambda: len(self.window.active_jobs) == 0))

    def test_raised_diagnostic_exception_reenables_button(self):
        release = threading.Event()
        started = threading.Event()

        def failing_callback():
            started.set()
            release.wait(1)
            raise RuntimeError("diagnostic failed")

        self.window._run_diagnostic("Testing failure...", failing_callback, self.window.validate_button)

        self.assertTrue(wait_until(started.is_set))
        self.assertFalse(self.window.validate_button.isEnabled())
        release.set()
        self.assertTrue(wait_until(lambda: self.window.validate_button.isEnabled()))
        self.assertEqual(len(self.window.active_jobs), 0)

    def test_result_handler_failure_restores_controls_and_cleans_up(self):
        self.window.validate_button.setEnabled(False)

        def failing_handler(_result):
            raise RuntimeError("handler failed")

        fake_file_logger = mock.Mock()
        with mock.patch.object(app_module.APP_LOGGER, "exception") as app_log, mock.patch.object(app_module.diagnostics, "_get_logger", return_value=fake_file_logger):
            self.window._run_worker(lambda: "ok", failing_handler)

            self.assertTrue(wait_until(lambda: self.window.validate_button.isEnabled()))
            app_log.assert_called_once()
            fake_file_logger.exception.assert_called_once()
        self.assertEqual(len(self.window.active_jobs), 0)

    def test_multiple_sequential_diagnostics_work_without_stale_jobs(self):
        result = DiagnosticResult(True, "ok", "Configuration is valid.")

        self.window._run_diagnostic("First...", lambda: result, self.window.validate_button)
        self.assertTrue(wait_until(lambda: self.window.validate_button.isEnabled() and len(self.window.active_jobs) == 0))

        self.window._run_diagnostic("Second...", lambda: result, self.window.validate_button)
        self.assertTrue(wait_until(lambda: self.window.validate_button.isEnabled() and len(self.window.active_jobs) == 0))

        self.assertEqual(len(self.window.active_jobs), 0)

    def test_initial_device_status_is_unknown(self):
        self.assertEqual(self.window.device_table.item(0, 2).text(), "Unknown")
        self.assertEqual(self.window.device_table.item(1, 2).text(), "Unknown")

    def test_successful_device_test_changes_row_to_connected(self):
        result = DiagnosticResult(
            True,
            "ok",
            "All devices connected.",
            ["FP1_DEVICE_01 (10.0.0.20:4371) - Connected"],
        )

        self.window._run_diagnostic("Testing devices...", lambda: result, self.window.device_button)
        self.assertTrue(wait_until(lambda: self.window.device_button.isEnabled() and len(self.window.active_jobs) == 0))

        self.assertEqual(self.window.device_table.item(0, 2).text(), "Connected")
        self.assertEqual(self.window.device_table.item(0, 2).foreground, "darkGreen")
        self.assertEqual(self.window.device_table.item(1, 2).text(), "Unknown")

    def test_failed_device_test_changes_row_to_failed(self):
        result = DiagnosticResult(
            False,
            "warning",
            "One or more devices failed.",
            ["FP1_DEVICE_01 (10.0.0.20:4371) - Connection failed"],
        )

        self.window._run_diagnostic("Testing devices...", lambda: result, self.window.device_button)
        self.assertTrue(wait_until(lambda: self.window.device_button.isEnabled() and len(self.window.active_jobs) == 0))

        self.assertEqual(self.window.device_table.item(0, 2).text(), "Failed")
        self.assertEqual(self.window.device_table.item(0, 2).foreground, "red")

    def test_refresh_preserves_device_diagnostic_status(self):
        self.window.device_connection_status["FP1_DEVICE_01"] = "Connected"
        self.window.device_connection_status["FP1_DEVICE_02"] = "Failed"

        self.window.refresh()

        self.assertEqual(self.window.device_table.item(0, 2).text(), "Connected")
        self.assertEqual(self.window.device_table.item(1, 2).text(), "Failed")

    def test_second_device_test_replaces_prior_status(self):
        self.window.device_connection_status["FP1_DEVICE_01"] = "Failed"
        result = DiagnosticResult(
            True,
            "ok",
            "All devices connected.",
            ["FP1_DEVICE_01 (10.0.0.20:4371) - Connected"],
        )

        self.window._run_diagnostic("Testing devices...", lambda: result, self.window.device_button)
        self.assertTrue(wait_until(lambda: self.window.device_button.isEnabled() and len(self.window.active_jobs) == 0))

        self.assertEqual(self.window.device_table.item(0, 2).text(), "Connected")
        self.assertEqual(self.window.device_connection_status["FP1_DEVICE_01"], "Connected")

    def test_device_diagnostic_updates_table_statuses(self):
        result = DiagnosticResult(
            False,
            "warning",
            "One or more devices failed.",
            [
                "FP1_DEVICE_01 (10.0.0.20:4371) - Connected",
                "FP1_DEVICE_02 (10.0.0.21:4371) - Connection failed",
            ],
        )

        self.window._run_diagnostic("Testing devices...", lambda: result, self.window.device_button)
        self.assertTrue(wait_until(lambda: self.window.device_button.isEnabled() and len(self.window.active_jobs) == 0))

        self.assertEqual(self.window.device_table.item(0, 2).text(), "Connected")
        self.assertEqual(self.window.device_table.item(1, 2).text(), "Failed")

    def test_service_buttons_disable_by_not_installed_state(self):
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertFalse(self.window.stop_button.isEnabled())
        self.assertFalse(self.window.restart_button.isEnabled())
        self.assertTrue(self.window.install_button.isEnabled())
        self.assertFalse(self.window.uninstall_button.isEnabled())

    def test_run_sync_now_disabled_while_service_running(self):
        controller = FakeController(ServiceStatus(installed=True, state="Running", startup="Automatic"))
        window = TestableSyncManagerWindow(controller=controller)
        try:
            self.assertFalse(window.sync_button.isEnabled())
            self.assertEqual(window.sync_button.toolTip(), "Stop the Windows service before running a manual sync.")
            window._run_sync_now()
            self.assertIn("Stop the Windows service before running a manual sync.", window.messages.lines[-1])
        finally:
            window.close()

    def test_run_sync_now_enabled_while_service_stopped(self):
        controller = FakeController(ServiceStatus(installed=True, state="Stopped", startup="Automatic"))
        window = TestableSyncManagerWindow(controller=controller)
        try:
            self.assertTrue(window.sync_button.isEnabled())
            self.assertEqual(window.sync_button.toolTip(), "")
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
