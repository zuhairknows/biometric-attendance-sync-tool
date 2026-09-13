import importlib
import sys
import types
import unittest
from unittest import mock


class FakeServiceFramework:
    def __init__(self, args):
        self.args = args


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


class ServiceEntryDispatchTests(unittest.TestCase):
    def test_import_does_not_require_runtime_config(self):
        service_entry = load_service_entry_with_fakes()

        sys.modules["service_runtime"].prepare_runtime_paths.assert_not_called()
        sys.modules["service_runtime"].load_runtime_config.assert_not_called()

    def test_no_args_selects_scm_dispatcher_path(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["FPF-Biometric-Sync-Service.exe"])

        service_entry.servicemanager.Initialize.assert_called_once_with()
        service_entry.servicemanager.PrepareToHostSingle.assert_called_once_with(service_entry.BiometricAttendanceSyncService)
        service_entry.servicemanager.StartServiceCtrlDispatcher.assert_called_once_with()
        service_entry.win32serviceutil.HandleCommandLine.assert_not_called()

    def test_install_selects_command_line_path(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["FPF-Biometric-Sync-Service.exe", "install"])

        service_entry.win32serviceutil.HandleCommandLine.assert_called_once_with(service_entry.BiometricAttendanceSyncService)
        service_entry.servicemanager.StartServiceCtrlDispatcher.assert_not_called()

    def test_install_does_not_require_runtime_config(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["FPF-Biometric-Sync-Service.exe", "install"])

        sys.modules["service_runtime"].prepare_runtime_paths.assert_not_called()
        sys.modules["service_runtime"].load_runtime_config.assert_not_called()

    def test_debug_selects_command_line_path(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["FPF-Biometric-Sync-Service.exe", "debug"])

        service_entry.win32serviceutil.HandleCommandLine.assert_called_once_with(service_entry.BiometricAttendanceSyncService)
        service_entry.servicemanager.StartServiceCtrlDispatcher.assert_not_called()

    def test_remove_selects_command_line_path(self):
        service_entry = load_service_entry_with_fakes()

        service_entry.run_service_dispatch(["FPF-Biometric-Sync-Service.exe", "remove"])

        service_entry.win32serviceutil.HandleCommandLine.assert_called_once_with(service_entry.BiometricAttendanceSyncService)
        service_entry.servicemanager.StartServiceCtrlDispatcher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
