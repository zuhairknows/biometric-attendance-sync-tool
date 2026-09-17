import importlib
import builtins
import json
import logging
import os
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


class FakeServiceFramework:
    def __init__(self, args):
        self.args = args
        self.reported_statuses = []

    def ReportServiceStatus(self, status):
        self.reported_statuses.append(status)


def load_service_entry_with_fakes():
    for module_name in [
        "service_entry",
        "service_runtime",
        "servicemanager",
        "win32event",
        "win32service",
        "win32serviceutil",
        "erpnext_sync",
    ]:
        sys.modules.pop(module_name, None)

    service_runtime = types.ModuleType("service_runtime")
    service_runtime.prepare_runtime_paths = mock.Mock()
    service_runtime.load_runtime_config = mock.Mock()
    sys.modules["service_runtime"] = service_runtime

    servicemanager = types.ModuleType("servicemanager")
    servicemanager.Initialize = mock.Mock()
    servicemanager.PrepareToHostSingle = mock.Mock()
    servicemanager.StartServiceCtrlDispatcher = mock.Mock()
    servicemanager.LogInfoMsg = mock.Mock()
    servicemanager.LogErrorMsg = mock.Mock()
    sys.modules["servicemanager"] = servicemanager

    win32event = types.ModuleType("win32event")
    win32event.WAIT_OBJECT_0 = 0
    win32event.CreateEvent = mock.Mock(return_value="stop-event")
    win32event.SetEvent = mock.Mock()
    win32event.WaitForSingleObject = mock.Mock(return_value=win32event.WAIT_OBJECT_0)
    sys.modules["win32event"] = win32event

    win32service = types.ModuleType("win32service")
    win32service.SERVICE_STOP_PENDING = 3
    sys.modules["win32service"] = win32service

    win32serviceutil = types.ModuleType("win32serviceutil")
    win32serviceutil.ServiceFramework = FakeServiceFramework
    win32serviceutil.HandleCommandLine = mock.Mock()
    sys.modules["win32serviceutil"] = win32serviceutil

    erpnext_sync = types.ModuleType("erpnext_sync")
    erpnext_sync.validate_runtime_config = mock.Mock()
    erpnext_sync.main = mock.Mock()
    erpnext_sync.info_logger = mock.Mock()
    erpnext_sync.error_logger = mock.Mock()
    sys.modules["erpnext_sync"] = erpnext_sync

    return importlib.import_module("service_entry")


def load_service_entry_with_real_runtime():
    for module_name in [
        "service_entry",
        "service_runtime",
        "servicemanager",
        "win32event",
        "win32service",
        "win32serviceutil",
        "erpnext_sync",
        "local_config",
        "requests",
        "zk",
    ]:
        sys.modules.pop(module_name, None)

    servicemanager = types.ModuleType("servicemanager")
    servicemanager.Initialize = mock.Mock()
    servicemanager.PrepareToHostSingle = mock.Mock()
    servicemanager.StartServiceCtrlDispatcher = mock.Mock()
    servicemanager.LogInfoMsg = mock.Mock()
    servicemanager.LogErrorMsg = mock.Mock()
    sys.modules["servicemanager"] = servicemanager

    win32event = types.ModuleType("win32event")
    win32event.WAIT_OBJECT_0 = 0
    win32event.CreateEvent = mock.Mock(return_value="stop-event")
    win32event.SetEvent = mock.Mock()
    win32event.WaitForSingleObject = mock.Mock(return_value=win32event.WAIT_OBJECT_0)
    sys.modules["win32event"] = win32event

    win32service = types.ModuleType("win32service")
    win32service.SERVICE_STOP_PENDING = 3
    sys.modules["win32service"] = win32service

    win32serviceutil = types.ModuleType("win32serviceutil")
    win32serviceutil.ServiceFramework = FakeServiceFramework
    win32serviceutil.HandleCommandLine = mock.Mock()
    sys.modules["win32serviceutil"] = win32serviceutil

    requests_module = types.ModuleType("requests")
    requests_module.request = mock.Mock()
    sys.modules["requests"] = requests_module

    zk_module = types.ModuleType("zk")
    zk_module.ZK = mock.Mock()
    zk_module.const = types.SimpleNamespace()
    sys.modules["zk"] = zk_module

    return importlib.import_module("service_entry")


class ServiceEntryDispatchTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True)

    def tearDown(self):
        for logger_name in list(logging.root.manager.loggerDict):
            logger = logging.getLogger(logger_name)
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        for module_name in ["service_entry", "service_runtime", "erpnext_sync", "local_config"]:
            sys.modules.pop(module_name, None)
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_import_does_not_require_runtime_config(self):
        service_entry = load_service_entry_with_fakes()

        sys.modules["service_runtime"].prepare_runtime_paths.assert_not_called()
        sys.modules["service_runtime"].load_runtime_config.assert_not_called()

    def test_no_args_selects_scm_dispatcher_path(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["Biometric-Attendance-Sync-Service.exe"])

        service_entry.servicemanager.Initialize.assert_called_once_with()
        service_entry.servicemanager.PrepareToHostSingle.assert_called_once_with(service_entry.BiometricAttendanceSyncService)
        service_entry.servicemanager.StartServiceCtrlDispatcher.assert_called_once_with()
        service_entry.win32serviceutil.HandleCommandLine.assert_not_called()

    def test_install_selects_command_line_path(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["Biometric-Attendance-Sync-Service.exe", "install"])

        service_entry.win32serviceutil.HandleCommandLine.assert_called_once_with(service_entry.BiometricAttendanceSyncService)
        service_entry.servicemanager.StartServiceCtrlDispatcher.assert_not_called()

    def test_install_does_not_require_runtime_config(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["Biometric-Attendance-Sync-Service.exe", "install"])

        sys.modules["service_runtime"].prepare_runtime_paths.assert_not_called()
        sys.modules["service_runtime"].load_runtime_config.assert_not_called()

    def test_debug_selects_command_line_path(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["Biometric-Attendance-Sync-Service.exe", "debug"])

        service_entry.win32serviceutil.HandleCommandLine.assert_called_once_with(service_entry.BiometricAttendanceSyncService)
        service_entry.servicemanager.StartServiceCtrlDispatcher.assert_not_called()

    def test_remove_selects_command_line_path(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["Biometric-Attendance-Sync-Service.exe", "remove"])

        service_entry.win32serviceutil.HandleCommandLine.assert_called_once_with(service_entry.BiometricAttendanceSyncService)

    def test_unconfigured_service_remains_idle(self):
        service_entry = load_service_entry_with_fakes()
        status = types.SimpleNamespace(state="UNCONFIGURED")
        with mock.patch.object(service_entry, "get_configuration_status", return_value=status):
            service = service_entry.BiometricAttendanceSyncService([])
            service.SvcDoRun()

        sys.modules["service_runtime"].load_runtime_config.assert_not_called()
        sys.modules["erpnext_sync"].main.assert_not_called()
        service_entry.servicemanager.LogInfoMsg.assert_any_call("Product is not configured. Complete first-run setup.")
        service_entry.servicemanager.StartServiceCtrlDispatcher.assert_not_called()

    def test_invalid_configuration_service_remains_idle(self):
        service_entry = load_service_entry_with_fakes()
        status = types.SimpleNamespace(state="INVALID")
        with mock.patch.object(service_entry, "get_configuration_status", return_value=status):
            service = service_entry.BiometricAttendanceSyncService([])
            service.SvcDoRun()

        sys.modules["service_runtime"].load_runtime_config.assert_not_called()
        sys.modules["erpnext_sync"].main.assert_not_called()
        service_entry.servicemanager.LogErrorMsg.assert_any_call(
            "Configuration is invalid. Complete setup or repair protected secrets."
        )

    def test_configured_service_passes_stop_callback_to_sync_cycle(self):
        service_entry = load_service_entry_with_fakes()
        status = types.SimpleNamespace(state="CONFIGURED")
        callback_states = []
        sys.modules["erpnext_sync"].main.side_effect = lambda stop_requested=None: callback_states.append(stop_requested())
        with mock.patch.object(service_entry, "get_configuration_status", return_value=status):
            service = service_entry.BiometricAttendanceSyncService([])
            service.SvcDoRun()

        sync_module = sys.modules["erpnext_sync"]
        sync_module.main.assert_called_once_with(stop_requested=mock.ANY)
        self.assertEqual(callback_states, [False])
        service_entry.win32event.WaitForSingleObject.assert_called_once_with(
            service.hWaitStop,
            service_entry.SERVICE_CHECK_INTERVAL_MS,
        )
        self.assertFalse(service.isrunning)

    def test_svc_stop_sets_stop_pending_and_wakes_service_loop(self):
        service_entry = load_service_entry_with_fakes()
        service = service_entry.BiometricAttendanceSyncService([])
        service.isrunning = True

        service.SvcStop()

        self.assertFalse(service.isrunning)
        self.assertEqual(service.reported_statuses, [service_entry.win32service.SERVICE_STOP_PENDING])
        service_entry.win32event.SetEvent.assert_called_once_with(service.hWaitStop)
        self.assertTrue(service.stop_requested())

    def test_packaged_service_commercial_config_does_not_require_local_config(self):
        programdata = self.test_dir / "programdata"
        programdata.mkdir(parents=True)
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
            "sync": {"pull_frequency_minutes": 60, "import_start_date": "2026-09-13"},
            "logging": {"level": "INFO", "retention_days": 30},
        }
        (programdata / "config.json").write_text(json.dumps(config), encoding="utf-8")
        original_cwd = Path.cwd()

        try:
            env = {"BIOMETRIC_SYNC_PROGRAMDATA": str(programdata), "BIOMETRIC_SYNC_CONFIG_DIR": str(programdata / "config")}
            with mock.patch.dict(os.environ, env, clear=True):
                service_entry = load_service_entry_with_real_runtime()
                with mock.patch("importlib.import_module", side_effect=_fail_local_config_import):
                    sync = service_entry.load_sync_runtime()
        finally:
            os.chdir(original_cwd)

        self.assertEqual(sync.config.CONFIG_SOURCE, "json")
        self.assertNotIn("local_config", sys.modules)


def _fail_local_config_import(name, package=None):
    if name == "local_config":
        raise ModuleNotFoundError("No module named 'local_config'", name="local_config")
    return builtins.__import__(name, fromlist=["*"])


if __name__ == "__main__":
    unittest.main()
