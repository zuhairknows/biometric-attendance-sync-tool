import shutil
import logging
import types
import unittest
from pathlib import Path
from unittest import mock

from manager import diagnostics


class FakeConnection:
    def __init__(self):
        self.disconnected = False

    def get_serialnumber(self):
        return "SERIAL"

    def disconnect(self):
        self.disconnected = True


class FakeZK:
    should_fail = False
    connections = []

    def __init__(self, ip, port=4370, timeout=10, password=0):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.password = password

    def connect(self):
        if FakeZK.should_fail:
            raise RuntimeError("device offline")
        connection = FakeConnection()
        FakeZK.connections.append(connection)
        return connection


class FakeSyncModule:
    DEFAULT_ZK_PORT = 4370

    def __init__(self, config):
        self.config = config
        self.main = mock.Mock()

    def validate_runtime_config(self):
        if getattr(self.config, "invalid", False):
            raise ValueError("Invalid configuration:\n- ERPNEXT_API_KEY is required.")
        return True

    @staticmethod
    def normalize_device_config(device):
        normalized = dict(device)
        normalized["ip"] = normalized.get("ip") or normalized.get("host")
        normalized["port"] = int(normalized.get("port", 4370))
        normalized["password"] = int(normalized.get("password", 0))
        if not normalized.get("ip"):
            raise ValueError("ip missing")
        return normalized


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class ManagerDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.logs_directory = Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)
        self.config = types.SimpleNamespace(
            LOGS_DIRECTORY=str(self.logs_directory),
            ERPNEXT_URL="https://erp.example.test",
            ERPNEXT_API_KEY="key",
            ERPNEXT_API_SECRET="secret",
            ERPNEXT_REQUEST_TIMEOUT=12,
            devices=[{"device_id": "FP1_DEVICE_01", "ip": "10.0.0.20", "port": 4371, "password": 0}],
        )
        FakeZK.should_fail = False
        FakeZK.connections = []

    def tearDown(self):
        logger = logging.getLogger("manager")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)

    def test_invalid_config_does_not_crash_manager_logic(self):
        self.config.invalid = True
        result = diagnostics.validate_configuration(FakeSyncModule(self.config))

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Configuration problem:")
        self.assertEqual(result.details, ["- ERPNEXT_API_KEY is required."])

    def test_erpnext_test_success(self):
        request_func = mock.Mock(return_value=FakeResponse(200))

        result = diagnostics.test_erpnext_connection(self.config, request_func=request_func)

        self.assertTrue(result.ok)
        request_func.assert_called_once_with(
            "GET",
            "https://erp.example.test/api/method/frappe.auth.get_logged_user",
            headers={"Authorization": "token key:secret", "Accept": "application/json"},
            timeout=12,
        )

    def test_erpnext_authentication_failure(self):
        result = diagnostics.test_erpnext_connection(self.config, request_func=mock.Mock(return_value=FakeResponse(401)))

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "ERPNext authentication failed.")

    def test_erpnext_unreachable(self):
        request_func = mock.Mock(side_effect=diagnostics.requests.exceptions.ConnectionError())

        result = diagnostics.test_erpnext_connection(self.config, request_func=request_func)

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "ERPNext unreachable.")

    def test_device_test_success(self):
        result = diagnostics.test_devices(self.config, FakeSyncModule(self.config), FakeZK)

        self.assertTrue(result.ok)
        self.assertEqual(result.details, ["FP1_DEVICE_01 (10.0.0.20:4371) - Connected"])
        self.assertTrue(FakeZK.connections[0].disconnected)

    def test_device_test_failure(self):
        FakeZK.should_fail = True

        result = diagnostics.test_devices(self.config, FakeSyncModule(self.config), FakeZK)

        self.assertFalse(result.ok)
        self.assertEqual(result.details, ["FP1_DEVICE_01 (10.0.0.20:4371) - Connection failed"])

    def test_run_sync_invokes_exactly_one_cycle(self):
        sync_module = FakeSyncModule(self.config)

        result = diagnostics.run_one_sync(sync_module)

        self.assertTrue(result.ok)
        sync_module.main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
