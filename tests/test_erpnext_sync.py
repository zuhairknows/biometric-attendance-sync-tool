import datetime
import importlib
import logging
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


class FakePickleDB:
    def __init__(self, path):
        self.path = path
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        pass


class FakeResponse:
    status_code = 200
    _content = b'{"message": {"name": "CHECKIN-0001"}}'


class FakeConnection:
    def __init__(self, attendances):
        self.attendances = attendances

    def disable_device(self):
        return True

    def get_attendance(self):
        return self.attendances

    def enable_device(self):
        return True

    def disconnect(self):
        pass


class FakeAttendance:
    def __init__(self, uid, user_id, timestamp, punch=0, status=1):
        self.uid = uid
        self.user_id = user_id
        self.timestamp = timestamp
        self.punch = punch
        self.status = status


class FakeZK:
    instances = []
    attendances = []

    def __init__(self, ip, port=4370, timeout=30, password=0):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.password = password
        FakeZK.instances.append(self)

    def connect(self):
        return FakeConnection(FakeZK.attendances)


def load_sync_module(logs_directory, import_start_date=None, request_timeout=30):
    for module_name in ["erpnext_sync", "local_config", "requests", "pickledb", "zk"]:
        sys.modules.pop(module_name, None)
    for logger_name in ["error_logger", "info_logger"]:
        logger = logging.getLogger(logger_name)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    config = types.ModuleType("local_config")
    config.ERPNEXT_API_KEY = "key"
    config.ERPNEXT_API_SECRET = "secret"
    config.ERPNEXT_URL = "https://erp.example.test"
    config.ERPNEXT_VERSION = 15
    config.PULL_FREQUENCY = 60
    config.LOGS_DIRECTORY = str(logs_directory)
    config.IMPORT_START_DATE = import_start_date
    config.REQUEST_TIMEOUT = request_timeout
    config.devices = []
    config.allowed_exceptions = [1, 2, 3]
    sys.modules["local_config"] = config

    requests_module = types.ModuleType("requests")
    requests_module.request = mock.Mock(return_value=FakeResponse())
    sys.modules["requests"] = requests_module

    pickledb_module = types.ModuleType("pickledb")
    pickledb_module.PickleDB = FakePickleDB
    sys.modules["pickledb"] = pickledb_module

    FakeZK.instances = []
    FakeZK.attendances = []
    zk_module = types.ModuleType("zk")
    zk_module.ZK = FakeZK
    zk_module.const = types.SimpleNamespace()
    sys.modules["zk"] = zk_module

    return importlib.import_module("erpnext_sync")


class ERPNextSyncPhaseOneTests(unittest.TestCase):
    def setUp(self):
        self.logs_directory = Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)

    def tearDown(self):
        for logger_name in logging.root.manager.loggerDict:
            logger = logging.getLogger(logger_name)
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)

    def test_default_port_4370(self):
        sync = load_sync_module(self.logs_directory)
        device = sync.normalize_device_config({"device_id": "FP1", "ip": "10.0.0.20"})
        sync.get_all_attendance_from_device(
            device["ip"],
            port=device["port"],
            password=device["password"],
            device_id=device["device_id"],
        )
        self.assertEqual(FakeZK.instances[-1].port, 4370)

    def test_custom_port_4371(self):
        sync = load_sync_module(self.logs_directory)
        device = sync.normalize_device_config({"device_id": "FP1", "ip": "10.0.0.20", "port": 4371})
        sync.get_all_attendance_from_device(
            device["ip"],
            port=device["port"],
            password=device["password"],
            device_id=device["device_id"],
        )
        self.assertEqual(FakeZK.instances[-1].port, 4371)

    def test_default_password_0(self):
        sync = load_sync_module(self.logs_directory)
        device = sync.normalize_device_config({"device_id": "FP1", "ip": "10.0.0.20"})
        sync.get_all_attendance_from_device(
            device["ip"],
            port=device["port"],
            password=device["password"],
            device_id=device["device_id"],
        )
        self.assertEqual(FakeZK.instances[-1].password, 0)

    def test_custom_password(self):
        sync = load_sync_module(self.logs_directory)
        device = sync.normalize_device_config({"device_id": "FP1", "ip": "10.0.0.20", "password": 1234})
        sync.get_all_attendance_from_device(
            device["ip"],
            port=device["port"],
            password=device["password"],
            device_id=device["device_id"],
        )
        self.assertEqual(FakeZK.instances[-1].password, 1234)

    def test_missing_latitude_longitude_does_not_crash(self):
        sync = load_sync_module(self.logs_directory)
        sent = []

        def fake_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
            sent.append((user_id, latitude, longitude))
            return 200, "CHECKIN-0001"

        sync.send_to_erpnext = fake_send
        device = {"device_id": "FP1", "ip": "10.0.0.20", "punch_direction": None}
        logs = [{"uid": 1, "user_id": "100", "timestamp": datetime.datetime(2026, 8, 27), "punch": 0, "status": 1}]
        sync.pull_process_and_push_data(device, logs)
        self.assertEqual(sent, [("100", None, None)])

    def test_import_start_date_boundary_imports_midnight_and_later(self):
        sync = load_sync_module(self.logs_directory, import_start_date="20260827")
        sent = []

        def fake_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
            sent.append((user_id, timestamp))
            return 200, "CHECKIN-0001"

        sync.send_to_erpnext = fake_send
        device = {"device_id": "FP1", "ip": "10.0.0.20", "punch_direction": None, "latitude": 0.0, "longitude": 0.0}
        logs = [
            {"uid": 1, "user_id": "before", "timestamp": datetime.datetime(2026, 8, 26, 23, 59), "punch": 0, "status": 1},
            {"uid": 2, "user_id": "midnight", "timestamp": datetime.datetime(2026, 8, 27, 0, 0), "punch": 0, "status": 1},
            {"uid": 3, "user_id": "later", "timestamp": datetime.datetime(2026, 8, 27, 8, 30), "punch": 0, "status": 1},
        ]
        sync.pull_process_and_push_data(device, logs)
        self.assertEqual([row[0] for row in sent], ["midnight", "later"])

    def test_deterministic_attendance_ordering(self):
        sync = load_sync_module(self.logs_directory)
        logs = [
            {"uid": 3, "user_id": "third", "timestamp": datetime.datetime(2026, 8, 27, 9, 0), "punch": 0, "status": 1},
            {"uid": 1, "user_id": "first", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1},
            {"uid": 2, "user_id": "second", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1},
        ]
        ordered = sync.normalize_attendance_logs(logs)
        self.assertEqual([row["user_id"] for row in ordered], ["first", "second", "third"])

    def test_retry_dump_identity_is_stable_when_ip_changes(self):
        sync = load_sync_module(self.logs_directory)
        first = sync.get_dump_file_name_and_directory("FP1", "10.0.0.20")
        second = sync.get_dump_file_name_and_directory("FP1", "10.0.0.21")
        self.assertEqual(first, second)
        self.assertTrue(first.endswith("FP1_last_fetch_dump.json"))

    def test_erpnext_requests_use_configured_timeout(self):
        sync = load_sync_module(self.logs_directory, request_timeout=12)
        sync.send_to_erpnext("100", datetime.datetime(2026, 8, 27), "FP1")
        sys.modules["requests"].request.assert_called_with(
            "POST",
            "https://erp.example.test/api/method/hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field",
            headers={
                "Authorization": "token key:secret",
                "Accept": "application/json",
            },
            json={
                "employee_field_value": "100",
                "timestamp": "2026-08-27 00:00:00",
                "device_id": "FP1",
                "log_type": None,
                "latitude": None,
                "longitude": None,
            },
            timeout=12,
        )

    def test_shift_sync_requests_use_configured_timeout(self):
        sync = load_sync_module(self.logs_directory, request_timeout=12)
        sync.send_shift_sync_to_erpnext("Shift1", datetime.datetime(2026, 8, 27))
        sys.modules["requests"].request.assert_called_with(
            "PUT",
            "https://erp.example.test/api/resource/Shift Type/Shift1",
            headers={
                "Authorization": "token key:secret",
                "Accept": "application/json",
            },
            data='{"last_sync_of_checkin": "2026-08-27 00:00:00"}',
            timeout=12,
        )

    def test_device_config_requires_device_id(self):
        sync = load_sync_module(self.logs_directory)
        with self.assertRaisesRegex(ValueError, "device_id"):
            sync.normalize_device_config({"ip": "10.0.0.20"})

    def test_device_config_requires_ip_or_host(self):
        sync = load_sync_module(self.logs_directory)
        with self.assertRaisesRegex(ValueError, "ip or host"):
            sync.normalize_device_config({"device_id": "FP1"})

    def test_device_config_rejects_invalid_port(self):
        sync = load_sync_module(self.logs_directory)
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            sync.normalize_device_config({"device_id": "FP1", "ip": "10.0.0.20", "port": 70000})


if __name__ == "__main__":
    unittest.main()
