"""Performance regression tests for Manager dashboard health counters.

The live FP2 run grew the audit logs to hundreds of megabytes and the Manager
became unusable, because every dashboard refresh read each audit file in full
just to count warnings. Structured status must be the primary source, and the
legacy fallback must never read an unbounded amount.
"""

import builtins
import json
import shutil
import time
import types
import unittest
from pathlib import Path

from manager import health
from manager.health import LOG_TAIL_BYTE_LIMIT, get_health_snapshot, read_log_tail_lines


LARGE_LOG_BYTES = 12 * 1024 * 1024


class FakeSyncModule:
    DEFAULT_ZK_PORT = 4370

    @staticmethod
    def normalize_device_config(device):
        normalized = dict(device)
        normalized["ip"] = normalized.get("ip") or normalized.get("host")
        normalized["port"] = int(normalized.get("port", 4370))
        return normalized


class ReadTracker:
    """Counts bytes actually pulled off disk by manager.health."""

    def __init__(self, test_case):
        self.test_case = test_case
        self.bytes_read = 0
        self.opened = []
        self._real_open = builtins.open
        self._real_read_text = Path.read_text

    def __enter__(self):
        tracker = self

        def tracking_open(file, mode="r", *args, **kwargs):
            handle = tracker._real_open(file, mode, *args, **kwargs)
            tracker.opened.append(str(file))
            real_read = handle.read

            def counting_read(*read_args, **read_kwargs):
                payload = real_read(*read_args, **read_kwargs)
                tracker.bytes_read += len(payload)
                return payload

            handle.read = counting_read
            return handle

        def tracking_read_text(self, *args, **kwargs):
            payload = tracker._real_read_text(self, *args, **kwargs)
            tracker.opened.append(str(self))
            tracker.bytes_read += len(payload)
            return payload

        builtins.open = tracking_open
        Path.read_text = tracking_read_text
        return self

    def __exit__(self, *exc_info):
        builtins.open = self._real_open
        Path.read_text = self._real_read_text
        return False


class HealthPerformanceTestCase(unittest.TestCase):
    def setUp(self):
        self.logs_directory = Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)
        self.config = types.SimpleNamespace(
            LOGS_DIRECTORY=str(self.logs_directory),
            devices=[{"device_id": "FP2_DEVICE_11", "ip": "10.0.0.20", "port": 4370}],
        )

    def tearDown(self):
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)

    def write_large_log(self, name, marker, trailing_marker_count=3):
        """Write a multi-megabyte audit log with the markers only at the very end."""
        path = self.logs_directory / name
        filler = ("2026-09-15 08:00:00\tINFO\tfiller record padding the audit file" + " " * 40 + "\n")
        chunk = filler * 1000
        with open(path, "w", encoding="utf-8") as handle:
            written = 0
            while written < LARGE_LOG_BYTES:
                handle.write(chunk)
                written += len(chunk)
            for index in range(trailing_marker_count):
                handle.write("2026-09-16 09:00:0%d\tWARNING\t%s\tFP2_DEVICE_11\n" % (index, marker))
        self.assertGreater(path.stat().st_size, LARGE_LOG_BYTES)
        return path

    def write_status(self, payload):
        (self.logs_directory / "status.json").write_text(json.dumps(payload), encoding="utf-8")


class StructuredStatusAvoidsLogScanningTests(HealthPerformanceTestCase):
    def test_structured_cycle_counters_do_not_read_large_audit_logs(self):
        missing_log = self.write_large_log("attendance_missing_employee_log_FP2_DEVICE_11.log", "MISSING_EMPLOYEE_MAPPING")
        corrupt_log = self.write_large_log("attendance_corrupt_record_log_FP2_DEVICE_11.log", "CORRUPT_ATTENDANCE_RECORD")
        self.write_status({
            "mission_accomplished_timestamp": "2026-09-16 09:01:00",
            "latest_sync_cycle": {
                "started_at": "2026-09-16 09:00:00",
                "completed_at": "2026-09-16 09:01:00",
                "total_enabled_devices_attempted": 12,
                "successful": 11,
                "successful_with_warnings": 1,
                "retryable_failures": 0,
                "failed": 0,
                "devices": [{
                    "device_id": "FP2_DEVICE_11",
                    "outcome": "DEVICE_SUCCESS_WITH_WARNINGS",
                    "missing_employee_count": 4,
                    "validation_failure_count": 2,
                    "corrupt_record_count": 7,
                    "invalid_record_count": 9,
                }],
            },
        })

        with ReadTracker(self) as tracker:
            snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertIn("Missing Employee mappings: 4", snapshot.warnings)
        self.assertIn("Corrupt attendance records skipped: 7", snapshot.warnings)
        self.assertIn("Invalid attendance records rejected locally: 9", snapshot.warnings)
        # The audit files must not be touched at all when structured status exists.
        self.assertNotIn(str(missing_log), tracker.opened)
        self.assertNotIn(str(corrupt_log), tracker.opened)
        self.assertLess(tracker.bytes_read, 1024 * 1024)

    def test_structured_status_refresh_is_fast_with_huge_logs_present(self):
        self.write_large_log("attendance_missing_employee_log_FP2_DEVICE_11.log", "MISSING_EMPLOYEE_MAPPING")
        self.write_large_log("attendance_failed_log_FP2_DEVICE_11.log", "500")
        self.write_status({
            "latest_sync_cycle": {
                "started_at": "2026-09-16 09:00:00",
                "retryable_failures": 1,
                "failed": 0,
                "devices": [{"device_id": "FP2_DEVICE_11", "missing_employee_count": 1}],
            },
        })

        started = time.monotonic()
        for _ in range(5):
            get_health_snapshot(self.config, FakeSyncModule)
        elapsed = time.monotonic() - started

        # Five refreshes over ~24MB of logs would take seconds if they were read.
        self.assertLess(elapsed, 1.0)


class BoundedFallbackScanningTests(HealthPerformanceTestCase):
    def test_fallback_scanning_reads_a_bounded_tail_only(self):
        self.write_large_log("attendance_corrupt_record_log_FP2_DEVICE_11.log", "CORRUPT_ATTENDANCE_RECORD")
        # No latest_sync_cycle: the legacy log path is the only source available.
        self.write_status({"mission_accomplished_timestamp": "2026-09-16 09:01:00"})

        with ReadTracker(self) as tracker:
            snapshot = get_health_snapshot(self.config, FakeSyncModule)

        self.assertIn("Corrupt attendance records skipped: 3", snapshot.warnings)
        # Bounded: a couple of tail windows, not the whole 12MB file.
        self.assertLess(tracker.bytes_read, 4 * LOG_TAIL_BYTE_LIMIT)

    def test_repeated_fallback_refreshes_stay_bounded(self):
        self.write_large_log("attendance_missing_employee_log_FP2_DEVICE_11.log", "MISSING_EMPLOYEE_MAPPING")
        self.write_status({})

        with ReadTracker(self) as tracker:
            for _ in range(5):
                get_health_snapshot(self.config, FakeSyncModule)

        self.assertLess(tracker.bytes_read, 10 * LOG_TAIL_BYTE_LIMIT)


class LogTailReaderTests(HealthPerformanceTestCase):
    def test_tail_reader_returns_the_last_lines(self):
        path = self.logs_directory / "sample.log"
        path.write_text("\n".join("line %d" % index for index in range(1000)), encoding="utf-8")

        lines = read_log_tail_lines(path, line_limit=10)

        self.assertEqual(lines, ["line %d" % index for index in range(990, 1000)])

    def test_tail_reader_drops_a_partial_leading_line(self):
        path = self.logs_directory / "sample.log"
        path.write_text("A" * 100 + "\n" + "B" * 100 + "\n", encoding="utf-8")

        lines = read_log_tail_lines(path, line_limit=10, byte_limit=150)

        # The truncated head of the first record must not be counted as a line.
        self.assertEqual(lines, ["B" * 100])

    def test_tail_reader_handles_missing_files(self):
        self.assertEqual(read_log_tail_lines(self.logs_directory / "absent.log"), [])

    def test_tail_reader_handles_undecodable_bytes(self):
        path = self.logs_directory / "sample.log"
        path.write_bytes(b"ok line\n\xff\xfe binary tail\n")

        lines = read_log_tail_lines(path)

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "ok line")

    def test_health_module_exposes_bounded_limits(self):
        self.assertLessEqual(health.LOG_TAIL_BYTE_LIMIT, 1024 * 1024)
        self.assertLessEqual(health.LOG_TAIL_LINE_LIMIT, 1000)


if __name__ == "__main__":
    unittest.main()
