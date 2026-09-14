import importlib
import sys
import types
import unittest
from unittest import mock

from tests.test_erpnext_sync import load_sync_module


class FakeSMWinservice:
    def __init__(self, args=None):
        self.hWaitStop = "stop-event"


def load_service_module(logs_directory):
    for module_name in ["erpnext_sync_win", "SMWinservice", "servicemanager", "win32event"]:
        sys.modules.pop(module_name, None)

    sync = load_sync_module(logs_directory)
    sys.modules["erpnext_sync"] = sync

    smwinservice_module = types.ModuleType("SMWinservice")
    smwinservice_module.SMWinservice = FakeSMWinservice
    sys.modules["SMWinservice"] = smwinservice_module

    servicemanager_module = types.ModuleType("servicemanager")
    servicemanager_module.LogInfoMsg = mock.Mock()
    servicemanager_module.LogErrorMsg = mock.Mock()
    sys.modules["servicemanager"] = servicemanager_module

    win32event_module = types.ModuleType("win32event")
    win32event_module.WAIT_OBJECT_0 = 0
    win32event_module.WAIT_TIMEOUT = 258
    win32event_module.WaitForSingleObject = mock.Mock(return_value=win32event_module.WAIT_OBJECT_0)
    sys.modules["win32event"] = win32event_module

    return importlib.import_module("erpnext_sync_win"), sync, win32event_module


class WindowsServiceTests(unittest.TestCase):
    def setUp(self):
        self.base_test = __import__("tests.test_erpnext_sync", fromlist=["ERPNextSyncPhaseOneTests"]).ERPNextSyncPhaseOneTests()
        self.base_test._testMethodName = self._testMethodName
        self.base_test.setUp()
        self.logs_directory = self.base_test.logs_directory

    def tearDown(self):
        self.base_test.tearDown()

    def test_service_start_validates_configuration(self):
        service_module, sync, _win32event = load_service_module(self.logs_directory)
        sync.config.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
        sync.validate_runtime_config = mock.Mock(return_value=True)

        service = service_module.PythonCornerExample([])
        service.start()

        sync.validate_runtime_config.assert_called_once_with()
        self.assertTrue(service.isrunning)

    def test_invalid_configuration_fails_clearly_before_loop(self):
        service_module, sync, _win32event = load_service_module(self.logs_directory)
        sync.validate_runtime_config = mock.Mock(side_effect=ValueError("Invalid configuration"))

        service = service_module.PythonCornerExample([])

        with self.assertRaisesRegex(ValueError, "Invalid configuration"):
            service.start()
        service_module.servicemanager.LogErrorMsg.assert_called()

    def test_service_loop_executes_sync_cycle_and_stops_on_signal(self):
        service_module, sync, win32event = load_service_module(self.logs_directory)
        sync.main = mock.Mock()

        service = service_module.PythonCornerExample([])
        service.isrunning = True
        service.main()

        sync.main.assert_called_once_with()
        win32event.WaitForSingleObject.assert_called_once_with(service.hWaitStop, service_module.SERVICE_CHECK_INTERVAL_MS)
        self.assertFalse(service.isrunning)

    def test_unexpected_sync_cycle_exception_does_not_kill_loop(self):
        service_module, sync, win32event = load_service_module(self.logs_directory)
        sync.main = mock.Mock(side_effect=[RuntimeError("boom"), None])
        win32event.WaitForSingleObject.side_effect = [win32event.WAIT_TIMEOUT, win32event.WAIT_OBJECT_0]

        service = service_module.PythonCornerExample([])
        service.isrunning = True
        service.main()

        self.assertEqual(sync.main.call_count, 2)
        self.assertFalse(service.isrunning)

    def test_stop_marks_service_for_shutdown(self):
        service_module, _sync, _win32event = load_service_module(self.logs_directory)

        service = service_module.PythonCornerExample([])
        service.isrunning = True
        service.stop()

        self.assertFalse(service.isrunning)

    def test_unconfigured_source_service_starts_idle(self):
        service_module, sync, _win32event = load_service_module(self.logs_directory)
        sync.config.CONFIG_SOURCE = "defaults"
        sync.validate_runtime_config = mock.Mock()

        service = service_module.PythonCornerExample([])
        service.start()

        sync.validate_runtime_config.assert_not_called()
        self.assertTrue(service.isrunning)


if __name__ == "__main__":
    unittest.main()
