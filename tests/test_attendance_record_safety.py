"""Regression tests for FP2 attendance decoding safety.

These reproduce the live FP2_DEVICE_11 failure mode: a buffer whose record
boundaries do not match the stride pyzk falls back to, decoded into punches that
look syntactically valid because ZKTeco's DecodeTime maps any four bytes onto a
datetime somewhere in 2000-2133.
"""

import datetime
import logging
import shutil
import struct
import types
import unittest
from pathlib import Path

from test_erpnext_sync import FakeZK, load_sync_module


def zk_encode_time(moment):
    """ZKTeco EncodeTime, copied from zkemsdk.c the same way pyzk copies it."""
    return (
        ((moment.year % 100) * 12 * 31 + ((moment.month - 1) * 31) + moment.day - 1)
        * (24 * 60 * 60)
        + (moment.hour * 60 + moment.minute) * 60
        + moment.second
    )


def zk_decode_time(raw_timestamp):
    """ZKTeco DecodeTime, byte-for-byte the algorithm pyzk's __decode_time uses."""
    value = struct.unpack("<I", raw_timestamp)[0]
    second = value % 60
    value //= 60
    minute = value % 60
    value //= 60
    hour = value % 24
    value //= 24
    day = value % 31 + 1
    value //= 31
    month = value % 12 + 1
    value //= 12
    return datetime.datetime(value + 2000, month, day, hour, minute, second)


def raw_time_for(moment):
    return struct.pack("<I", zk_encode_time(moment))


def make_40_byte_attendance(uid, user_id, raw_timestamp, status=1, punch=0):
    return struct.pack(
        "<H24sB4sB8s",
        uid,
        str(user_id).encode("ascii").ljust(24, b"\x00"),
        status,
        raw_timestamp,
        punch,
        b"\x00" * 8,
    )


class ControllableAttendanceConnection:
    """Low-level pyzk-shaped connection with independently controllable sizes.

    `records` and the buffer's declared `total_size` are set separately so a
    mismatch between them - the condition that makes the record stride unprovable -
    can be reproduced exactly.
    """

    def __init__(self, payload, records, total_size=None, calls=None):
        self.payload = payload
        self.records = records
        self.total_size = len(payload) if total_size is None else total_size
        self.calls = calls if calls is not None else []

    def disable_device(self):
        self.calls.append("disable")
        return True

    def read_sizes(self):
        self.calls.append("read_sizes")

    def get_users(self):
        self.calls.append("get_users")
        return []

    def read_with_buffer(self, command):
        self.calls.append("read_with_buffer")
        return struct.pack("I", self.total_size) + self.payload, len(self.payload) + 4

    def _ZK__decode_time(self, raw_timestamp):
        return zk_decode_time(raw_timestamp)

    def enable_device(self):
        self.calls.append("enable")
        return True

    def clear_attendance(self):
        self.calls.append("clear")
        return True

    def disconnect(self):
        self.calls.append("disconnect")


def zk_class_returning(connection):
    class LowLevelZK:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            return connection

    return LowLevelZK


class AttendanceTestCase(unittest.TestCase):
    def setUp(self):
        self.logs_directory = Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)

    def tearDown(self):
        for logger_name in list(logging.root.manager.loggerDict):
            logger = logging.getLogger(logger_name)
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)


class DecodeTimeCorruptionEvidenceTests(AttendanceTestCase):
    """Proves why a guessed stride is unsafe rather than merely untidy."""

    def test_arbitrary_bytes_decode_to_plausible_future_timestamps(self):
        # The live audit reported 2026-09-20, 2027-03-31 and 2027-10-08. Those are
        # not device data: they are what DecodeTime returns for misaligned bytes.
        for expected in (
            datetime.datetime(2026, 9, 20, 7, 30, 0),
            datetime.datetime(2027, 3, 31, 18, 5, 12),
            datetime.datetime(2027, 10, 8, 23, 59, 59),
        ):
            self.assertEqual(zk_decode_time(raw_time_for(expected)), expected)

    def test_most_random_words_decode_without_raising(self):
        # Only impossible day/month pairs raise, so a misaligned buffer yields a
        # low, evenly spread corrupt-record rate - exactly the live symptom - while
        # the vast majority of garbage decodes "successfully".
        decoded = 0
        failed = 0
        for seed in range(0, 200000, 97):
            try:
                zk_decode_time(struct.pack("<I", seed * 7919 % 0xFFFFFFFF))
                decoded += 1
            except ValueError:
                failed += 1
        self.assertGreater(decoded, failed * 5)
        self.assertGreater(failed, 0)


class RecordLayoutResolutionTests(AttendanceTestCase):
    def test_exact_supported_strides_are_accepted(self):
        sync = load_sync_module(self.logs_directory)
        for record_size in (8, 16, 40):
            total = record_size * 5
            self.assertEqual(sync.resolve_attendance_record_size(total, 5, total), record_size)

    def test_non_integral_stride_is_refused(self):
        sync = load_sync_module(self.logs_directory)
        # Upstream pyzk would compare a float and fall through to the 40-byte
        # catch-all; the product-local decoder truncated with int() and could pick
        # an entirely different layout. Both are guesses, so neither is allowed.
        self.assertIsNone(sync.resolve_attendance_record_size(1000, 61, 1000))

    def test_unsupported_stride_is_refused(self):
        sync = load_sync_module(self.logs_directory)
        self.assertIsNone(sync.resolve_attendance_record_size(32 * 4, 4, 32 * 4))

    def test_truncated_payload_is_refused(self):
        sync = load_sync_module(self.logs_directory)
        self.assertIsNone(sync.resolve_attendance_record_size(40 * 10, 10, 40 * 9))

    def test_zero_and_garbage_inputs_are_refused(self):
        sync = load_sync_module(self.logs_directory)
        self.assertIsNone(sync.resolve_attendance_record_size(0, 5, 0))
        self.assertIsNone(sync.resolve_attendance_record_size(40, 0, 40))
        self.assertIsNone(sync.resolve_attendance_record_size("x", 5, 40))


class UnprovableLayoutFailsDeviceSafelyTests(AttendanceTestCase):
    def test_unprovable_layout_refuses_to_decode(self):
        sync = load_sync_module(self.logs_directory)
        # 410 bytes declared across 10 records: no supported stride divides this.
        payload = bytes(range(256)) + bytes(range(154))
        calls = []
        connection = ControllableAttendanceConnection(payload, records=10, total_size=410, calls=calls)
        sync.ZK = zk_class_returning(connection)

        with self.assertRaises(sync.UnsupportedAttendanceRecordLayout):
            sync.get_all_attendance_from_device("192.0.2.10", device_id="FP2_DEVICE_11")

        # Device safety is preserved on the refusal path.
        self.assertEqual(calls[-2:], ["enable", "disconnect"])
        operational_log = (self.logs_directory / "logs.log").read_text()
        self.assertIn("Attendance record layout could not be proven", operational_log)
        self.assertIn("FP2_DEVICE_11", operational_log)

    def test_unprovable_layout_is_not_a_retryable_or_erpnext_failure(self):
        sync = load_sync_module(self.logs_directory)
        result = sync.device_sync_result_from_exception(
            {"device_id": "FP2_DEVICE_11"},
            sync.UnsupportedAttendanceRecordLayout("unprovable"),
        )

        self.assertEqual(result.outcome, sync.DEVICE_FAILED)
        self.assertEqual(result.error_category, sync.UNSUPPORTED_RECORD_LAYOUT)
        self.assertEqual(result.retryable_failure_count, 0)
        self.assertEqual(result.missing_employee_count, 0)
        self.assertEqual(result.validation_failure_count, 0)

    def test_proven_40_byte_layout_still_decodes(self):
        sync = load_sync_module(self.logs_directory)
        records = [
            make_40_byte_attendance(1, "554068", raw_time_for(datetime.datetime(2026, 8, 27, 8, 0))),
            make_40_byte_attendance(2, "554069", raw_time_for(datetime.datetime(2026, 8, 27, 8, 5))),
        ]
        payload = b"".join(records)
        connection = ControllableAttendanceConnection(payload, records=2)
        sync.ZK = zk_class_returning(connection)

        attendances = sync.get_all_attendance_from_device("192.0.2.10", device_id="FP2_DEVICE_11")

        self.assertEqual([row["user_id"] for row in attendances], ["554068", "554069"])


class AttendanceSemanticValidationTests(AttendanceTestCase):
    def test_control_character_user_id_is_rejected(self):
        sync = load_sync_module(self.logs_directory)
        is_valid, reason = sync.validate_attendance_record({
            "user_id": "55\x00\x13406",
            "timestamp": datetime.datetime(2026, 8, 27, 8, 0),
        })
        self.assertFalse(is_valid)
        self.assertEqual(reason, "user_id_contains_control_characters")

    def test_binary_user_id_is_rejected(self):
        sync = load_sync_module(self.logs_directory)
        is_valid, reason = sync.validate_attendance_record({
            "user_id": b"\x81\x9f\x02",
            "timestamp": datetime.datetime(2026, 8, 27, 8, 0),
        })
        self.assertFalse(is_valid)
        self.assertEqual(reason, "user_id_not_text")

    def test_future_2027_timestamp_is_rejected(self):
        sync = load_sync_module(self.logs_directory)
        is_valid, reason = sync.validate_attendance_record({
            "user_id": "554068",
            "timestamp": datetime.datetime(2027, 10, 8, 23, 59, 59),
        })
        self.assertFalse(is_valid)
        self.assertEqual(reason, "timestamp_in_future")

    def test_malformed_record_with_syntactically_valid_datetime_is_rejected(self):
        sync = load_sync_module(self.logs_directory)
        # Garbage bytes that DecodeTime happily turns into a real datetime object.
        decoded = zk_decode_time(struct.pack("<I", 0xC0FFEE11))
        self.assertIsInstance(decoded, datetime.datetime)
        is_valid, reason = sync.validate_attendance_record({"user_id": "554068", "timestamp": decoded})
        self.assertFalse(is_valid)
        self.assertEqual(reason, "timestamp_in_future")

    def test_non_datetime_timestamp_is_rejected(self):
        sync = load_sync_module(self.logs_directory)
        is_valid, reason = sync.validate_attendance_record({
            "user_id": "554068",
            "timestamp": "2026-08-27 08:00:00",
        })
        self.assertFalse(is_valid)
        self.assertEqual(reason, "timestamp_not_a_datetime")

    def test_valid_normal_fp1_attendance_is_accepted(self):
        sync = load_sync_module(self.logs_directory)
        is_valid, reason = sync.validate_attendance_record({
            "user_id": "554068",
            "timestamp": datetime.datetime(2026, 8, 27, 8, 0),
        })
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_small_clock_skew_is_tolerated(self):
        sync = load_sync_module(self.logs_directory)
        near_future = datetime.datetime.now() + datetime.timedelta(minutes=1)
        is_valid, _reason = sync.validate_attendance_record({"user_id": "554068", "timestamp": near_future})
        self.assertTrue(is_valid)

    def test_future_skew_allowance_defaults_to_a_conservative_value(self):
        sync = load_sync_module(self.logs_directory)
        # No schema change was made for this hotfix, so the documented default is
        # what production runs with. A few minutes covers device clock skew without
        # admitting the 2026/2027 timestamps the live audit reported.
        self.assertEqual(sync.MAX_ATTENDANCE_FUTURE_SKEW_MINUTES, 5)
        self.assertEqual(sync.MIN_ATTENDANCE_YEAR, 2000)

    def test_malformed_identifier_is_not_silently_normalized(self):
        sync = load_sync_module(self.logs_directory)
        valid, rejected = sync.partition_valid_attendance_logs([
            {"user_id": "55\x0140", "timestamp": datetime.datetime(2026, 8, 27, 8, 0)},
        ])
        self.assertEqual(valid, [])
        self.assertEqual(len(rejected), 1)
        # The original value is preserved for the audit, not repaired.
        self.assertEqual(rejected[0][0]["user_id"], "55\x0140")


class InvalidRecordsNeverReachERPNextTests(AttendanceTestCase):
    def _device_logs(self):
        return [
            {"uid": 1, "user_id": "554068", "timestamp": datetime.datetime(2026, 8, 27, 8, 0), "punch": 0, "status": 1},
            {"uid": 2, "user_id": "55\x00\x13406", "timestamp": datetime.datetime(2026, 8, 27, 8, 1), "punch": 0, "status": 1},
            {"uid": 3, "user_id": "554069", "timestamp": datetime.datetime(2027, 10, 8, 23, 59, 59), "punch": 0, "status": 1},
        ]

    def test_invalid_records_are_not_sent_and_get_their_own_category(self):
        sync = load_sync_module(self.logs_directory)
        sent = []
        sync.send_to_erpnext = lambda user_id, timestamp, *args, **kwargs: (sent.append((user_id, timestamp)), (200, "ok"))[1]
        device = {"device_id": "FP2_DEVICE_11", "ip": "192.0.2.10", "password": 0, "punch_direction": None}

        result = sync.pull_process_and_push_data(device, self._device_logs())

        self.assertEqual([row[0] for row in sent], ["554068"])
        self.assertEqual(result.invalid_record_count, 2)
        self.assertEqual(result.missing_employee_count, 0)
        self.assertEqual(result.validation_failure_count, 0)
        self.assertEqual(result.retryable_failure_count, 0)
        self.assertEqual(result.outcome, sync.DEVICE_SUCCESS_WITH_WARNINGS)

    def test_invalid_records_are_audited_separately_from_missing_employee(self):
        sync = load_sync_module(self.logs_directory)
        sync.send_to_erpnext = lambda *args, **kwargs: (200, "ok")
        device = {"device_id": "FP2_DEVICE_11", "ip": "192.0.2.10", "password": 0, "punch_direction": None}

        sync.pull_process_and_push_data(device, self._device_logs())

        invalid_log = (self.logs_directory / "attendance_invalid_record_log_FP2_DEVICE_11.log").read_text()
        self.assertIn("INVALID_ATTENDANCE_RECORD", invalid_log)
        self.assertIn("user_id_contains_control_characters", invalid_log)
        self.assertIn("timestamp_in_future", invalid_log)
        # The audit loggers are created eagerly, so assert they stayed empty: a
        # locally rejected record must not land in either of these categories.
        self.assertEqual((self.logs_directory / "attendance_missing_employee_log_FP2_DEVICE_11.log").read_text(), "")
        self.assertEqual((self.logs_directory / "attendance_validation_failure_log_FP2_DEVICE_11.log").read_text(), "")
        self.assertEqual((self.logs_directory / "attendance_failed_log_FP2_DEVICE_11.log").read_text(), "")

    def test_invalid_records_never_write_raw_control_bytes_to_the_audit(self):
        sync = load_sync_module(self.logs_directory)
        sync.send_to_erpnext = lambda *args, **kwargs: (200, "ok")
        device = {"device_id": "FP2_DEVICE_11", "ip": "192.0.2.10", "password": 0, "punch_direction": None}

        sync.pull_process_and_push_data(device, self._device_logs())

        invalid_log = (self.logs_directory / "attendance_invalid_record_log_FP2_DEVICE_11.log").read_text()
        self.assertNotIn("\x00", invalid_log)
        self.assertNotIn("\x13", invalid_log)

    def test_invalid_records_do_not_advance_the_success_checkpoint(self):
        sync = load_sync_module(self.logs_directory)
        sync.send_to_erpnext = lambda *args, **kwargs: (200, "ok")
        device = {"device_id": "FP2_DEVICE_11", "ip": "192.0.2.10", "password": 0, "punch_direction": None}

        sync.pull_process_and_push_data(device, self._device_logs())

        success_log = (self.logs_directory / "attendance_success_log_FP2_DEVICE_11.log").read_text()
        self.assertIn("554068", success_log)
        # The 2027 record must not become the checkpoint, or every later real
        # punch would be skipped as "already synced".
        self.assertNotIn("2027", success_log)
        self.assertNotIn("554069", success_log)

    def test_device_with_only_invalid_records_reports_warnings_not_success(self):
        sync = load_sync_module(self.logs_directory)
        sent = []
        sync.send_to_erpnext = lambda *args, **kwargs: (sent.append(args), (200, "ok"))[1]
        device = {"device_id": "FP2_DEVICE_11", "ip": "192.0.2.10", "password": 0, "punch_direction": None}

        result = sync.pull_process_and_push_data(device, [
            {"uid": 3, "user_id": "554069", "timestamp": datetime.datetime(2027, 3, 31, 1, 2, 3), "punch": 0, "status": 1},
        ])

        self.assertEqual(sent, [])
        self.assertEqual(result.invalid_record_count, 1)
        self.assertEqual(result.outcome, sync.DEVICE_SUCCESS_WITH_WARNINGS)


if __name__ == "__main__":
    unittest.main()
