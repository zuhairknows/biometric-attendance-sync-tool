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
            devices=[{"device_id": "FP1_DEVICE_01", "ip": "10.0.0.20", "port": 4371}],
        )

    def tearDown(self):
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)

    def test_status_json_parsing(self):
        (self.logs_directory / "status.json").write_text(json.dumps({
            "mission_accomplished_timestamp": "2026-09-12 14:15:26",
            "FP1_DEVICE_01_pull_timestamp": "2026-09-12 14:15:06",
            "FP1_DEVICE_01_push_timestamp": "2026-09-12 14:15:08",
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
        self.config.devices = [{"device_id": "FP1_BAD"}]

        snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertEqual(snapshot.devices[0].device_id, "FP1_BAD")
        self.assertIn("Configuration problem", snapshot.devices[0].last_push)

    def test_missing_employee_warning_count(self):
        failed_log = self.logs_directory / "attendance_failed_log_FP1_DEVICE_01.log"
        failed_log.write_text(
            "No Employee found for the given employee field value\n"
            "No Employee found for the given employee field value\n",
            encoding="utf-8",
        )

        snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertEqual(snapshot.warnings, ["2 attendance records could not be matched to ERPNext employees."])


if __name__ == "__main__":
    unittest.main()

