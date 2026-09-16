import datetime
import importlib
import json
import logging
import os
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


def erpnext_response(status_code, payload):
    return types.SimpleNamespace(
        status_code=status_code,
        _content=json.dumps(payload).encode("utf-8"),
    )


def erpnext_validation_response(message, status_code=417):
    return erpnext_response(
        status_code,
        {"exc": json.dumps(["frappe.exceptions.ValidationError: " + message])},
    )


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


def load_sync_module(logs_directory, import_start_date=None, request_timeout=30, include_logs_directory=True):
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
    if include_logs_directory:
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

    programdata = Path(os.environ.get("BIOMETRIC_SYNC_PROGRAMDATA") or (Path(logs_directory).parent / "programdata"))
    config_dir = Path(logs_directory).parent / "legacy-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "BIOMETRIC_SYNC_PROGRAMDATA": str(programdata),
        "BIOMETRIC_SYNC_CONFIG_DIR": str(config_dir),
    }
    with mock.patch.dict(os.environ, env):
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
        device = sync.normalize_device_config({"device_id": "DEVICE_01", "ip": "192.0.2.10"})
        sync.get_all_attendance_from_device(
            device["ip"],
            port=device["port"],
            password=device["password"],
            device_id=device["device_id"],
        )
        self.assertEqual(FakeZK.instances[-1].port, 4370)

    def test_custom_port_4371(self):
        sync = load_sync_module(self.logs_directory)
        device = sync.normalize_device_config({"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 4371})
        sync.get_all_attendance_from_device(
            device["ip"],
            port=device["port"],
            password=device["password"],
            device_id=device["device_id"],
        )
        self.assertEqual(FakeZK.instances[-1].port, 4371)

    def test_default_password_0(self):
        sync = load_sync_module(self.logs_directory)
        device = sync.normalize_device_config({"device_id": "DEVICE_01", "ip": "192.0.2.10"})
        sync.get_all_attendance_from_device(
            device["ip"],
            port=device["port"],
            password=device["password"],
            device_id=device["device_id"],
        )
        self.assertEqual(FakeZK.instances[-1].password, 0)

    def test_custom_password(self):
        sync = load_sync_module(self.logs_directory)
        device = sync.normalize_device_config({"device_id": "DEVICE_01", "ip": "192.0.2.10", "password": 1234})
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
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
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
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None, "latitude": 0.0, "longitude": 0.0}
        logs = [
            {"uid": 1, "user_id": "before", "timestamp": datetime.datetime(2026, 8, 26, 23, 59), "punch": 0, "status": 1},
            {"uid": 2, "user_id": "midnight", "timestamp": datetime.datetime(2026, 8, 27, 0, 0), "punch": 0, "status": 1},
            {"uid": 3, "user_id": "later", "timestamp": datetime.datetime(2026, 8, 27, 8, 30), "punch": 0, "status": 1},
        ]
        sync.pull_process_and_push_data(device, logs)
        self.assertEqual([row[0] for row in sent], ["midnight", "later"])

    def test_duplicate_employee_checkin_does_not_halt_processing(self):
        sync = load_sync_module(self.logs_directory)
        sent = []

        def fake_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
            sent.append(user_id)
            if user_id == "duplicate":
                return 417, sync.DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE
            return 200, "CHECKIN-0001"

        sync.send_to_erpnext = fake_send
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
        logs = [
            {"uid": 1, "user_id": "duplicate", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1},
            {"uid": 2, "user_id": "later", "timestamp": datetime.datetime(2026, 8, 27, 8, 1), "punch": 0, "status": 1},
        ]
        sync.pull_process_and_push_data(device, logs)

        self.assertEqual(sent, ["duplicate", "later"])
        success_log = (self.logs_directory / "attendance_success_log_DEVICE_01.log").read_text()
        failed_log = (self.logs_directory / "attendance_failed_log_DEVICE_01.log").read_text()
        self.assertIn("DUPLICATE_ALREADY_SYNCED", success_log)
        self.assertEqual(failed_log, "")

    def test_duplicate_timestamp_erpnext_response_is_idempotent_success(self):
        sync = load_sync_module(self.logs_directory)
        sync.requests.request.return_value = erpnext_validation_response(sync.DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE)
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
        logs = [{"uid": 1, "user_id": "duplicate", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1}]

        sync.pull_process_and_push_data(device, logs)

        success_log = (self.logs_directory / "attendance_success_log_DEVICE_01.log").read_text()
        failed_log = (self.logs_directory / "attendance_failed_log_DEVICE_01.log").read_text()
        error_log = (self.logs_directory / "error.log").read_text()
        self.assertIn("DUPLICATE_ALREADY_SYNCED", success_log)
        self.assertEqual(failed_log, "")
        self.assertNotIn("Error during ERPNext API Call", error_log)

    def test_duplicate_employee_checkin_advances_local_checkpoint(self):
        sync = load_sync_module(self.logs_directory)
        first_run_sent = []

        def first_run_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
            first_run_sent.append(user_id)
            return 417, sync.DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE

        sync.send_to_erpnext = first_run_send
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
        duplicate_log = {"uid": 1, "user_id": "duplicate", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1}
        sync.pull_process_and_push_data(device, [duplicate_log])
        self.assertEqual(first_run_sent, ["duplicate"])

        second_run_sent = []

        def second_run_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
            second_run_sent.append(user_id)
            return 200, "CHECKIN-0002"

        sync.send_to_erpnext = second_run_send
        later_log = {"uid": 2, "user_id": "later", "timestamp": datetime.datetime(2026, 8, 27, 8, 1), "punch": 0, "status": 1}
        sync.pull_process_and_push_data(device, [duplicate_log, later_log])

        self.assertEqual(second_run_sent, ["later"])

    def test_duplicate_timestamp_is_not_written_repeatedly_to_failed_retry_storage(self):
        sync = load_sync_module(self.logs_directory)
        sync.requests.request.return_value = erpnext_validation_response(sync.DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE)
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
        duplicate_log = {"uid": 1, "user_id": "duplicate", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1}

        sync.pull_process_and_push_data(device, [duplicate_log])
        sync.pull_process_and_push_data(device, [duplicate_log])

        failed_log = (self.logs_directory / "attendance_failed_log_DEVICE_01.log").read_text()
        success_log = (self.logs_directory / "attendance_success_log_DEVICE_01.log").read_text()
        self.assertEqual(failed_log, "")
        self.assertEqual(sync.requests.request.call_count, 1)
        self.assertEqual(success_log.count("DUPLICATE_ALREADY_SYNCED"), 1)

    def test_missing_employee_attendance_device_id_remains_actionable_failure(self):
        sync = load_sync_module(self.logs_directory)
        sync.requests.request.return_value = erpnext_validation_response(sync.EMPLOYEE_NOT_FOUND_ATTENDANCE_DEVICE_ID_MESSAGE)
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
        logs = [{"uid": 1, "user_id": "missing", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1}]

        sync.pull_process_and_push_data(device, logs)

        failed_log = (self.logs_directory / "attendance_failed_log_DEVICE_01.log").read_text()
        success_log = (self.logs_directory / "attendance_success_log_DEVICE_01.log").read_text()
        self.assertIn("417", failed_log)
        self.assertIn("missing", failed_log)
        self.assertNotIn("DUPLICATE_ALREADY_SYNCED", success_log)

    def test_unrelated_http_417_remains_failure(self):
        sync = load_sync_module(self.logs_directory)

        def fake_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
            return 417, "Some other validation error"

        sync.send_to_erpnext = fake_send
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
        logs = [{"uid": 1, "user_id": "100", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1}]

        with self.assertRaisesRegex(Exception, "API Call to ERPNext Failed"):
            sync.pull_process_and_push_data(device, logs)

        failed_log = (self.logs_directory / "attendance_failed_log_DEVICE_01.log").read_text()
        self.assertIn("417", failed_log)
        self.assertNotIn("DUPLICATE_ALREADY_SYNCED", failed_log)

    def test_http_417_with_duplicate_text_but_unrelated_validation_remains_failure(self):
        sync = load_sync_module(self.logs_directory)

        def fake_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
            return 417, "A different validation failure occurred."

        sync.send_to_erpnext = fake_send
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
        logs = [{"uid": 1, "user_id": "100", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1}]

        with self.assertRaisesRegex(Exception, "API Call to ERPNext Failed"):
            sync.pull_process_and_push_data(device, logs)

    def test_http_5xx_responses_remain_retryable_failures(self):
        for status_code in [500, 503]:
            with self.subTest(status_code=status_code):
                sync = load_sync_module(self.logs_directory)

                def fake_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
                    return status_code, "Temporary ERPNext server failure"

                sync.send_to_erpnext = fake_send
                device_id = "DEVICE_" + str(status_code)
                device = {"device_id": device_id, "ip": "192.0.2.10", "punch_direction": None}
                logs = [{"uid": 1, "user_id": "100", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1}]

                with self.assertRaisesRegex(Exception, "API Call to ERPNext Failed"):
                    sync.pull_process_and_push_data(device, logs)

                failed_log = (self.logs_directory / ("attendance_failed_log_" + device_id + ".log")).read_text()
                self.assertIn(str(status_code), failed_log)

    def test_send_to_erpnext_200_response_remains_success(self):
        sync = load_sync_module(self.logs_directory)
        sync.requests.request.return_value = erpnext_response(200, {"message": {"name": "CHECKIN-0001"}})

        status_code, message = sync.send_to_erpnext("100", datetime.datetime(2026, 8, 27, 8, 0), "DEVICE_01")

        self.assertEqual(status_code, 200)
        self.assertEqual(message, "CHECKIN-0001")

    def test_later_punches_are_processed_after_duplicate(self):
        sync = load_sync_module(self.logs_directory)
        sent = []

        def fake_send(user_id, timestamp, device_id=None, log_type=None, latitude=None, longitude=None):
            sent.append(user_id)
            if user_id == "duplicate":
                return 417, sync.DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE
            return 200, "CHECKIN-0001"

        sync.send_to_erpnext = fake_send
        device = {"device_id": "DEVICE_01", "ip": "192.0.2.10", "punch_direction": None}
        logs = [
            {"uid": 1, "user_id": "duplicate", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1},
            {"uid": 2, "user_id": "later-1", "timestamp": datetime.datetime(2026, 8, 27, 8, 1), "punch": 0, "status": 1},
            {"uid": 3, "user_id": "later-2", "timestamp": datetime.datetime(2026, 8, 27, 8, 2), "punch": 0, "status": 1},
        ]
        sync.pull_process_and_push_data(device, logs)

        self.assertEqual(sent, ["duplicate", "later-1", "later-2"])

    def test_info_logger_writes_utf8_arabic_and_mixed_text(self):
        sync = load_sync_module(self.logs_directory)
        message = "Shift Type الوردية الصباحية synchronized"

        sync.info_logger.info(message)
        for handler in sync.info_logger.handlers:
            handler.flush()

        log_text = (self.logs_directory / "logs.log").read_text(encoding="utf-8")
        self.assertIn(message, log_text)

    def test_error_logger_writes_utf8_arabic_and_mixed_text(self):
        sync = load_sync_module(self.logs_directory)
        message = "Error updating Shift Type الوردية الليلية"

        sync.error_logger.error(message)
        for handler in sync.error_logger.handlers:
            handler.flush()

        log_text = (self.logs_directory / "error.log").read_text(encoding="utf-8")
        self.assertIn(message, log_text)

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
        first = sync.get_dump_file_name_and_directory("DEVICE_01", "192.0.2.10")
        second = sync.get_dump_file_name_and_directory("DEVICE_01", "192.0.2.11")
        self.assertEqual(first, second)
        self.assertTrue(first.endswith("DEVICE_01_last_fetch_dump.json"))

    def test_erpnext_requests_use_configured_timeout(self):
        sync = load_sync_module(self.logs_directory, request_timeout=12)
        sync.send_to_erpnext("100", datetime.datetime(2026, 8, 27), "DEVICE_01")
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
                "device_id": "DEVICE_01",
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
            sync.normalize_device_config({"ip": "192.0.2.10"})

    def test_device_config_requires_ip_or_host(self):
        sync = load_sync_module(self.logs_directory)
        with self.assertRaisesRegex(ValueError, "ip or host"):
            sync.normalize_device_config({"device_id": "DEVICE_01"})

    def test_device_config_rejects_invalid_port(self):
        sync = load_sync_module(self.logs_directory)
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            sync.normalize_device_config({"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 70000})

    def test_device_config_accepts_safe_device_id(self):
        sync = load_sync_module(self.logs_directory)
        first = sync.normalize_device_config({"device_id": "DEVICE_01", "ip": "192.0.2.10"})
        second = sync.normalize_device_config({"device_id": "GATE-02", "ip": "192.0.2.11"})
        self.assertEqual(first["device_id"], "DEVICE_01")
        self.assertEqual(second["device_id"], "GATE-02")

    def test_device_config_rejects_unsafe_device_id(self):
        sync = load_sync_module(self.logs_directory)
        unsafe_device_ids = ["../DEVICE_01", "DEVICE.01", "DEVICE:01", "DEVICE/01", "DEVICE\\01"]
        for device_id in unsafe_device_ids:
            with self.subTest(device_id=device_id):
                with self.assertRaisesRegex(ValueError, "invalid"):
                    sync.normalize_device_config({"device_id": device_id, "ip": "192.0.2.10"})

    def test_config_validation_rejects_duplicate_device_ids(self):
        sync = load_sync_module(self.logs_directory)
        with self.assertRaisesRegex(ValueError, "Duplicate device_id"):
            sync.validate_unique_device_ids([
                {"device_id": "DEVICE_01", "ip": "192.0.2.10"},
                {"device_id": "DEVICE_01", "ip": "192.0.2.11"},
            ])

    def test_runtime_config_validation_passes_without_device_connectivity(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 4370, "password": 0}
        ]

        self.assertTrue(sync.validate_runtime_config())
        self.assertEqual(FakeZK.instances, [])

    def test_runtime_config_validation_rejects_missing_required_values(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.ERPNEXT_URL = ""
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 4370, "password": 0}
        ]

        with self.assertRaisesRegex(ValueError, "ERPNEXT_URL is required"):
            sync.validate_runtime_config()

    def test_runtime_config_validation_rejects_invalid_url(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.ERPNEXT_URL = "erp.example.com"
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 4370, "password": 0}
        ]

        with self.assertRaisesRegex(ValueError, "ERPNEXT_URL must start"):
            sync.validate_runtime_config()

    def test_runtime_config_validation_rejects_credential_placeholders(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 4370, "password": 0}
        ]
        placeholder_cases = [
            ("ERPNEXT_API_KEY", "YOUR_API_KEY"),
            ("ERPNEXT_API_SECRET", "YOUR_API_SECRET"),
            ("ERPNEXT_API_KEY", "YOUR_REAL_API_KEY"),
            ("ERPNEXT_API_SECRET", "YOUR_REAL_API_SECRET"),
        ]
        for key, placeholder in placeholder_cases:
            with self.subTest(key=key, placeholder=placeholder):
                original_value = getattr(sync.config, key)
                setattr(sync.config, key, placeholder)
                with self.assertRaisesRegex(ValueError, key + " must be set to the real local credential"):
                    sync.validate_runtime_config()
                setattr(sync.config, key, original_value)

    def test_runtime_config_validation_rejects_empty_credentials(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.ERPNEXT_API_KEY = ""
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 4370, "password": 0}
        ]

        with self.assertRaisesRegex(ValueError, "ERPNEXT_API_KEY is required"):
            sync.validate_runtime_config()

    def test_runtime_config_validation_allows_custom_credential_with_placeholder_like_prefix(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.ERPNEXT_API_KEY = "YOUR_CUSTOM_REAL_CREDENTIAL_123"
        sync.config.ERPNEXT_API_SECRET = "YOUR_CUSTOM_SECRET_456"
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 4370, "password": 0}
        ]

        self.assertTrue(sync.validate_runtime_config())

    def test_main_isolates_per_device_faults_and_redacts_passwords(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10", "password": 1111},
            {"device_id": "DEVICE_BAD", "password": 1234},
            {"device_id": "DEVICE_03", "ip": "192.0.2.12", "password": 3333},
        ]
        processed_device_ids = []

        def fake_pull_process_and_push_data(device, device_attendance_logs=None):
            processed_device_ids.append(device["device_id"])

        sync.pull_process_and_push_data = fake_pull_process_and_push_data
        sync.main()

        self.assertEqual(processed_device_ids, ["DEVICE_01", "DEVICE_03"])
        error_log = (self.logs_directory / "error.log").read_text()
        self.assertIn("DEVICE_BAD", error_log)
        self.assertIn("***", error_log)
        self.assertNotIn("1234", error_log)

    def test_main_skips_disabled_devices(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10", "enabled": False},
            {"device_id": "DEVICE_02", "ip": "192.0.2.11"},
        ]
        processed_device_ids = []

        def fake_pull_process_and_push_data(device, device_attendance_logs=None):
            processed_device_ids.append(device["device_id"])

        sync.pull_process_and_push_data = fake_pull_process_and_push_data
        sync.main()

        self.assertEqual(processed_device_ids, ["DEVICE_02"])

    def test_stop_requested_before_cycle_processes_no_devices(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10"},
        ]
        sync.pull_process_and_push_data = mock.Mock()

        sync.main(stop_requested=lambda: True)

        sync.pull_process_and_push_data.assert_not_called()

    def test_stop_requested_after_first_device_does_not_start_second_device(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10"},
            {"device_id": "DEVICE_02", "ip": "192.0.2.11"},
        ]
        processed_device_ids = []
        stop = {"requested": False}

        def fake_pull_process_and_push_data(device, device_attendance_logs=None):
            processed_device_ids.append(device["device_id"])
            stop["requested"] = True

        sync.pull_process_and_push_data = fake_pull_process_and_push_data
        sync.main(stop_requested=lambda: stop["requested"])

        self.assertEqual(processed_device_ids, ["DEVICE_01"])
        self.assertIsNone(sync.status.get("mission_accomplished_timestamp"))

    def test_current_device_completes_before_shutdown(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10"},
            {"device_id": "DEVICE_02", "ip": "192.0.2.11"},
        ]
        events = []
        stop = {"requested": False}

        def fake_pull_process_and_push_data(device, device_attendance_logs=None):
            events.append("start-" + device["device_id"])
            stop["requested"] = True
            events.append("finish-" + device["device_id"])

        sync.pull_process_and_push_data = fake_pull_process_and_push_data
        sync.main(stop_requested=lambda: stop["requested"])

        self.assertEqual(events, ["start-DEVICE_01", "finish-DEVICE_01"])

    def test_manual_main_without_stop_callback_remains_backward_compatible(self):
        sync = load_sync_module(self.logs_directory)
        sync.config.devices = [
            {"device_id": "DEVICE_01", "ip": "192.0.2.10"},
        ]
        processed_device_ids = []

        def fake_pull_process_and_push_data(device, device_attendance_logs=None):
            processed_device_ids.append(device["device_id"])

        sync.pull_process_and_push_data = fake_pull_process_and_push_data
        sync.main()

        self.assertEqual(processed_device_ids, ["DEVICE_01"])

    def test_disabled_device_is_reenabled_when_fetch_fails(self):
        sync = load_sync_module(self.logs_directory)
        calls = []

        class FailingConnection:
            def disable_device(self):
                calls.append("disable")
                return True

            def get_attendance(self):
                calls.append("fetch")
                raise RuntimeError("stop requested during active fetch")

            def enable_device(self):
                calls.append("enable")
                return True

            def disconnect(self):
                calls.append("disconnect")

        class FailingZK:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                return FailingConnection()

        sync.ZK = FailingZK

        with self.assertRaisesRegex(Exception, "Device fetch failed"):
            sync.get_all_attendance_from_device("192.0.2.10", device_id="DEVICE_01")

        self.assertEqual(calls, ["disable", "fetch", "enable", "disconnect"])

    def test_minimal_legacy_config_without_logs_directory_imports_runtime(self):
        programdata = self.logs_directory.parent / (self._testMethodName + "_programdata")
        if programdata.exists():
            shutil.rmtree(programdata)

        try:
            with mock.patch.dict(os.environ, {"BIOMETRIC_SYNC_PROGRAMDATA": str(programdata)}, clear=True):
                sync = load_sync_module(self.logs_directory, include_logs_directory=False)

            sync.config.devices = [{"device_id": "DEVICE_01", "ip": "192.0.2.10"}]
            self.assertEqual(sync.config.CONFIG_SOURCE, "legacy")
            self.assertEqual(sync.config.LOGS_DIRECTORY, str(programdata.resolve() / "logs"))
            self.assertTrue(sync.validate_runtime_config())
        finally:
            for logger_name in logging.root.manager.loggerDict:
                logger = logging.getLogger(logger_name)
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)
            if programdata.exists():
                shutil.rmtree(programdata)


if __name__ == "__main__":
    unittest.main()
