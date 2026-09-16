import json
import shutil
import types
import unittest
from pathlib import Path

from manager.health import get_health_snapshot, read_status_data


class FakeSyncModule:
    DEFAULT_ZK_PORT = 4370

    @staticmethod
    def normalize_device_config(device):
        normalized = dict(device)
        normalized["ip"] = normalized.get("ip") or normalized.get("host")
        normalized["port"] = int(normalized.get("port", 4370))
        if not normalized.get("device_id"):
            raise ValueError("device_id missing")
        if not normalized.get("ip"):
            raise ValueError("ip missing")
        return normalized


class ManagerHealthTests(unittest.TestCase):
    def setUp(self):
        self.logs_directory = Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)
        self.config = types.SimpleNamespace(
            LOGS_DIRECTORY=str(self.logs_directory),
            devices=[{"device_id": "DEVICE_01", "ip": "192.0.2.10", "port": 4370}],
        )

    def tearDown(self):
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)

    def test_status_json_parsing(self):
        (self.logs_directory / "status.json").write_text(json.dumps({
            "mission_accomplished_timestamp": "2026-09-12 14:15:26",
            "DEVICE_01_pull_timestamp": "2026-09-12 14:15:06",
            "DEVICE_01_push_timestamp": "2026-09-12 14:15:08",
        }), encoding="utf-8")

        snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertTrue(snapshot.status_file_found)
        self.assertEqual(snapshot.last_successful_sync, "2026-09-12 14:15:26")
        self.assertEqual(snapshot.devices[0].last_pull, "2026-09-12 14:15:06")
        self.assertEqual(snapshot.devices[0].last_push, "2026-09-12 14:15:08")

    def test_missing_status_json_handling(self):
        data, found = read_status_data(self.config)

        self.assertEqual(data, {})
        self.assertFalse(found)

    def test_invalid_config_device_does_not_crash_health(self):
        self.config.devices = [{"device_id": "DEVICE_BAD"}]

        snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertEqual(snapshot.devices[0].device_id, "DEVICE_BAD")
        self.assertIn("Configuration problem", snapshot.devices[0].last_push)

    def test_missing_employee_warning_count(self):
        missing_log = self.logs_directory / "attendance_missing_employee_log_DEVICE_01.log"
        missing_log.write_text(
            "MISSING_EMPLOYEE_MAPPING\tDEVICE_01\t554068\t2026-08-27 08:00:00\tmissing_employee_mapping\n"
            "MISSING_EMPLOYEE_MAPPING\tDEVICE_01\t554069\t2026-08-27 08:05:00\tmissing_employee_mapping\n",
            encoding="utf-8",
        )

        snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertEqual(snapshot.warnings, ["Missing Employee mappings: 2"])

    def test_legacy_missing_employee_failed_log_still_counts(self):
        failed_log = self.logs_directory / "attendance_failed_log_DEVICE_01.log"
        failed_log.write_text("No Employee found for attendance_device_id\n", encoding="utf-8")

        snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertEqual(snapshot.warnings, ["Missing Employee mappings: 1"])

    def test_retryable_and_validation_failure_warning_counts(self):
        failed_log = self.logs_directory / "attendance_failed_log_DEVICE_01.log"
        failed_log.write_text(
            "503\t1\t100\t1780000000.0\t0\t1\t{}\n"
            "0\t2\t101\t1780000060.0\t0\t1\t{}\n",
            encoding="utf-8",
        )
        validation_log = self.logs_directory / "attendance_validation_failure_log_DEVICE_01.log"
        validation_log.write_text(
            "VALIDATION_FAILURE\tDEVICE_01\t100\t2026-08-27 08:00:00\t417\tvalidation_failure\n",
            encoding="utf-8",
        )

        snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertEqual(snapshot.warnings, [
            "Retryable synchronization failures: 2",
            "Permanent validation/data failures: 1",
        ])

    def test_corrupt_attendance_record_warning_count(self):
        corrupt_log = self.logs_directory / "attendance_corrupt_record_log_DEVICE_01.log"
        corrupt_log.write_text(
            "CORRUPT_ATTENDANCE_RECORD\tDEVICE_01\t192.0.2.10\t0\t16\tdeadbeef\tValueError\n"
            "CORRUPT_ATTENDANCE_RECORD\tDEVICE_01\t192.0.2.10\t1\t16\tcafebabe\tValueError\n",
            encoding="utf-8",
        )

        snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertEqual(snapshot.warnings, ["Corrupt attendance records skipped: 2"])


if __name__ == "__main__":
    unittest.main()
