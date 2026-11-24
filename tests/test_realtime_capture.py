import datetime
import pathlib
import shutil
import unittest
import urllib.request
from unittest import mock

from realtime_capture import (
    AdmsListener,
    AdmsRouter,
    PunchEvent,
    build_device_index,
    parse_attlog,
    parse_attlog_timestamp,
    start_if_configured,
)

STATION = {
    "device_id": "STATION_01",
    "ip": "192.0.2.10",
    "serial_number": "ABC123",
    "realtime": True,
}
PLAIN = {
    "device_id": "GATE_01",
    "ip": "192.0.2.11",
    "serial_number": "ZZZ999",
}


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def _record(self, level):
        def log(message):
            self.messages.append((level, message))

        return log

    def __getattr__(self, name):
        return self._record(name)


class ParseAttlogTimestampTests(unittest.TestCase):
    def test_accepts_the_usual_device_formats(self):
        expected = datetime.datetime(2026, 11, 22, 14, 32, 5)
        for text in ("2026-11-22 14:32:05", "2026-11-22T14:32:05", "2026/11/22 14:32:05"):
            self.assertEqual(parse_attlog_timestamp(text), expected)

    def test_returns_none_rather_than_raising(self):
        for text in ("", None, "not a date", "2026-13-45 99:99:99"):
            self.assertIsNone(parse_attlog_timestamp(text))


class ParseAttlogTests(unittest.TestCase):
    def test_parses_a_normal_punch(self):
        body = "247\t2026-11-22 14:32:05\t0\t1\t12\t0\t0"
        events, malformed = parse_attlog(body, "STATION_01", "ABC123")
        self.assertEqual(malformed, 0)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.user_id, "247")
        self.assertEqual(event.timestamp, datetime.datetime(2026, 11, 22, 14, 32, 5))
        self.assertEqual(event.status, "0")
        self.assertEqual(event.verify_mode, "1")
        self.assertEqual(event.workcode, "12")
        self.assertEqual(event.device_id, "STATION_01")
        self.assertEqual(event.serial_number, "ABC123")

    def test_tolerates_a_short_row_and_unknown_trailing_columns(self):
        body = (
            "247\t2026-11-22 14:32:05\n"
            "248\t2026-11-22 14:33:00\t0\t1\t7\t0\t0\t1\t36.6\textra"
        )
        events, malformed = parse_attlog(body, "STATION_01", "ABC123")
        self.assertEqual(malformed, 0)
        self.assertEqual([e.user_id for e in events], ["247", "248"])
        self.assertEqual(events[0].status, "")
        self.assertEqual(events[1].workcode, "7")

    def test_skips_malformed_rows_without_losing_the_good_ones(self):
        body = (
            "247\t2026-11-22 14:32:05\t0\n"
            "\n"
            "nonsense\n"
            "\tmissing user\n"
            "249\tnot-a-date\n"
            "250\t2026-11-22 14:35:00\t0"
        )
        events, malformed = parse_attlog(body, "STATION_01", "ABC123")
        self.assertEqual([e.user_id for e in events], ["247", "250"])
        self.assertEqual(malformed, 3)

    def test_handles_windows_line_endings_and_empty_body(self):
        events, malformed = parse_attlog("247\t2026-11-22 14:32:05\r\n", "D", "S")
        self.assertEqual(len(events), 1)
        self.assertEqual(malformed, 0)
        self.assertEqual(parse_attlog("", "D", "S"), ([], 0))
        self.assertEqual(parse_attlog(None, "D", "S"), ([], 0))


class BuildDeviceIndexTests(unittest.TestCase):
    def test_indexes_only_realtime_devices(self):
        by_serial, by_ip = build_device_index([STATION, PLAIN])
        self.assertEqual(by_serial, {"ABC123": "STATION_01"})
        self.assertEqual(by_ip, {"192.0.2.10": "STATION_01"})

    def test_serial_lookup_is_case_insensitive(self):
        by_serial, _ = build_device_index([dict(STATION, serial_number="abc123")])
        self.assertEqual(by_serial, {"ABC123": "STATION_01"})

    def test_device_without_serial_is_still_reachable_by_ip(self):
        by_serial, by_ip = build_device_index([{"device_id": "S", "ip": "192.0.2.12", "realtime": True}])
        self.assertEqual(by_serial, {})
        self.assertEqual(by_ip, {"192.0.2.12": "S"})

    def test_empty_and_none_are_safe(self):
        self.assertEqual(build_device_index([]), ({}, {}))
        self.assertEqual(build_device_index(None), ({}, {}))


class AdmsRouterTests(unittest.TestCase):
    def setUp(self):
        self.punches = []
        self.logger = RecordingLogger()
        self.router = AdmsRouter([STATION, PLAIN], self.punches.append, logger=self.logger)

    def test_handshake_asks_the_device_for_realtime_push(self):
        status, text = self.router.handle("GET", "/iclock/cdata", {"SN": ["ABC123"]}, "")
        self.assertEqual(status, 200)
        self.assertIn("GET OPTION FROM: ABC123", text)
        self.assertIn("Realtime=1", text)
        self.assertEqual(self.router.stats.handshakes, 1)

    def test_attlog_post_delivers_each_punch(self):
        body = "247\t2026-11-22 14:32:05\t0\t1\t12\n248\t2026-11-22 14:33:00\t0\t1\t12"
        status, text = self.router.handle("POST", "/iclock/cdata", {"SN": ["ABC123"], "table": ["ATTLOG"]}, body)
        self.assertEqual(status, 200)
        self.assertEqual(text, "OK: 2")
        self.assertEqual([p.user_id for p in self.punches], ["247", "248"])
        self.assertEqual(self.router.stats.punches, 2)

    def test_unknown_serial_is_refused(self):
        status, text = self.router.handle("POST", "/iclock/cdata", {"SN": ["NOPE"]}, "247\t2026-11-22 14:32:05")
        self.assertEqual(status, 401)
        self.assertEqual(text, "Unauthorized")
        self.assertEqual(self.punches, [])

    def test_non_realtime_device_is_refused_even_though_it_is_configured(self):
        status, _ = self.router.handle("POST", "/iclock/cdata", {"SN": ["ZZZ999"]}, "1\t2026-11-22 14:32:05")
        self.assertEqual(status, 401)
        self.assertEqual(self.punches, [])

    def test_falls_back_to_client_ip_when_serial_is_absent(self):
        status, text = self.router.handle(
            "POST", "/iclock/cdata", {"table": ["ATTLOG"]}, "247\t2026-11-22 14:32:05", client_ip="192.0.2.10"
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.punches[0].device_id, "STATION_01")

    def test_other_tables_are_acknowledged_but_not_acted_on(self):
        status, text = self.router.handle(
            "POST", "/iclock/cdata", {"SN": ["ABC123"], "table": ["OPERLOG"]}, "anything at all"
        )
        self.assertEqual((status, text), (200, "OK"))
        self.assertEqual(self.punches, [])

    def test_getrequest_and_devicecmd_are_answered(self):
        self.assertEqual(self.router.handle("GET", "/iclock/getrequest", {"SN": ["ABC123"]}, ""), (200, "OK"))
        self.assertEqual(self.router.handle("POST", "/iclock/devicecmd", {"SN": ["ABC123"]}, ""), (200, "OK"))

    def test_unknown_path_is_404(self):
        status, _ = self.router.handle("GET", "/iclock/nonsense", {"SN": ["ABC123"]}, "")
        self.assertEqual(status, 404)

    def test_a_failing_callback_does_not_fail_the_batch(self):
        def explode(event):
            raise RuntimeError("endpoint down")

        router = AdmsRouter([STATION], explode, logger=self.logger)
        status, text = router.handle("POST", "/iclock/cdata", {"SN": ["ABC123"]}, "247\t2026-11-22 14:32:05")
        # The device must be told OK, or it replays punches we have already seen.
        self.assertEqual((status, text), (200, "OK: 1"))
        self.assertTrue(any(level == "exception" for level, _ in self.logger.messages))

    def test_malformed_rows_are_counted_and_logged(self):
        self.router.handle("POST", "/iclock/cdata", {"SN": ["ABC123"]}, "rubbish\n247\t2026-11-22 14:32:05")
        self.assertEqual(self.router.stats.malformed_lines, 1)
        self.assertEqual(self.router.stats.punches, 1)


class StationAgeLimitTests(unittest.TestCase):
    """Stored punches a terminal uploads on switching to ADMS must not fire."""

    NOW = datetime.datetime(2026, 11, 23, 14, 0, 0)

    def setUp(self):
        self.punches = []
        self.logger = RecordingLogger()
        self.router = AdmsRouter(
            [STATION], self.punches.append, logger=self.logger, max_age_seconds=300, clock=lambda: self.NOW
        )

    def post(self, body):
        return self.router.handle("POST", "/iclock/cdata", {"SN": ["ABC123"], "table": ["ATTLOG"]}, body)

    def test_a_fresh_punch_is_delivered(self):
        self.assertEqual(self.post("247\t2026-11-23 13:59:58\t0\t1"), (200, "OK: 1"))
        self.assertEqual([p.user_id for p in self.punches], ["247"])

    def test_stored_history_is_acknowledged_but_not_delivered(self):
        body = "\n".join([
            "100\t2026-10-01 07:00:00\t0\t1",
            "101\t2026-11-23 13:50:00\t0\t1",
            "247\t2026-11-23 13:59:30\t0\t1",
        ])
        status, text = self.post(body)
        # OK for the whole batch, so the terminal does not offer them again.
        self.assertEqual(status, 200)
        self.assertEqual([p.user_id for p in self.punches], ["247"])
        self.assertEqual(self.router.stats.stale_punches, 2)
        self.assertEqual(self.router.stats.punches, 1)
        self.assertTrue(any("age limit" in message for _, message in self.logger.messages))

    def test_a_terminal_clock_far_ahead_is_not_trusted(self):
        self.post("247\t2026-11-23 14:10:00\t0\t1")
        self.assertEqual(self.punches, [])
        self.assertEqual(self.router.stats.stale_punches, 1)

    def test_the_limit_is_inclusive(self):
        self.post("247\t2026-11-23 13:55:00\t0\t1")
        self.assertEqual(len(self.punches), 1)

    def test_no_limit_when_none(self):
        router = AdmsRouter([STATION], self.punches.append, max_age_seconds=None, clock=lambda: self.NOW)
        router.handle("POST", "/iclock/cdata", {"SN": ["ABC123"]}, "100\t2026-10-01 07:00:00")
        self.assertEqual(len(self.punches), 1)

    def test_start_if_configured_applies_the_default_limit(self):
        listener = start_if_configured([STATION], self.punches.append, host="127.0.0.1", port=0)
        self.addCleanup(listener.stop)
        self.assertEqual(listener.router.max_age_seconds, 300)


class AdmsListenerTests(unittest.TestCase):
    def test_serves_a_real_punch_over_http(self):
        punches = []
        listener = AdmsListener([STATION], punches.append, host="127.0.0.1", port=0).start()
        self.addCleanup(listener.stop)
        url = "http://127.0.0.1:" + str(listener.actual_port) + "/iclock/cdata?SN=ABC123&table=ATTLOG"
        request = urllib.request.Request(url, data=b"247\t2026-11-22 14:32:05\t0\t1\t12", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read().decode(), "OK: 1")
        self.assertEqual([p.user_id for p in punches], ["247"])

    def test_stop_is_idempotent(self):
        listener = AdmsListener([STATION], lambda event: None, host="127.0.0.1", port=0).start()
        listener.stop()
        listener.stop()

    def test_start_if_configured_opens_no_port_without_a_realtime_device(self):
        self.assertIsNone(start_if_configured([PLAIN], lambda event: None, host="127.0.0.1", port=0))

    def test_start_if_configured_returns_a_running_listener(self):
        listener = start_if_configured([STATION], lambda event: None, host="127.0.0.1", port=0)
        self.assertIsNotNone(listener)
        self.addCleanup(listener.stop)
        self.assertNotEqual(listener.actual_port, 0)


class DeviceConfigTests(unittest.TestCase):
    """normalize_device_config has to carry the two new keys through.

    The poll reads its devices through it, and the listener is indexed from the
    same list, so a key it dropped would leave a configured terminal silently
    unreachable.
    """

    def setUp(self):
        from test_erpnext_sync import load_sync_module

        self.logs_directory = pathlib.Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)
        self.sync = load_sync_module(self.logs_directory)
        self.addCleanup(shutil.rmtree, self.logs_directory, True)

    def test_realtime_defaults_to_off(self):
        device = self.sync.normalize_device_config({"device_id": "GATE_01", "ip": "192.0.2.11"})
        self.assertFalse(device["realtime"])
        self.assertIsNone(device["serial_number"])

    def test_realtime_and_serial_are_carried_through(self):
        device = self.sync.normalize_device_config(STATION)
        self.assertTrue(device["realtime"])
        self.assertEqual(device["serial_number"], "ABC123")


class ListenerLifecycleTests(unittest.TestCase):
    """The wiring in erpnext_sync, loaded the way the sibling suite loads it."""

    def setUp(self):
        from test_erpnext_sync import load_sync_module

        self.logs_directory = pathlib.Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)
        self.sync = load_sync_module(self.logs_directory)
        # Port 0 lets the OS pick, so a test never fights for a real one.
        self.sync.STATION_LISTENER_HOST = "127.0.0.1"
        self.sync.STATION_LISTENER_PORT = 0
        self.addCleanup(self.sync.stop_realtime_listener)
        self.addCleanup(shutil.rmtree, self.logs_directory, True)

    def test_no_listener_when_no_device_asks_for_one(self):
        self.sync.config.devices = [PLAIN]
        self.assertIsNone(self.sync.ensure_realtime_listener())

    def test_listener_starts_for_a_realtime_device(self):
        self.sync.config.devices = [STATION]
        listener = self.sync.ensure_realtime_listener()
        self.assertIsNotNone(listener)
        self.assertNotEqual(listener.actual_port, 0)

    def test_ensure_is_idempotent(self):
        self.sync.config.devices = [STATION]
        first = self.sync.ensure_realtime_listener()
        self.assertIs(self.sync.ensure_realtime_listener(), first)

    def test_a_failing_start_does_not_raise_and_is_retried(self):
        self.sync.config.devices = [STATION]
        with mock.patch.object(
            self.sync.realtime_capture, "start_if_configured", side_effect=OSError("port in use")
        ):
            self.assertIsNone(self.sync.ensure_realtime_listener())
        # The next cycle tries again rather than waiting for a restart.
        self.assertIsNotNone(self.sync.ensure_realtime_listener())

    def test_stop_is_safe_when_never_started(self):
        self.sync.stop_realtime_listener()
        self.sync.stop_realtime_listener()

    def test_main_brings_the_listener_up_before_the_poll_is_due(self):
        self.sync.config.devices = [STATION]
        # A lift-off well inside PULL_FREQUENCY, so the poll itself is skipped.
        self.sync.status.set("lift_off_timestamp", str(datetime.datetime.now()))
        self.sync.main()
        self.assertIsNotNone(self.sync._realtime_listener)

    def test_station_devices_marks_a_configured_device(self):
        self.sync.config.devices = [PLAIN]
        self.sync.config.STATION_DEVICES = {"GATE_01": "SN-GATE"}
        listener = self.sync.ensure_realtime_listener()
        self.assertIsNotNone(listener)
        self.assertEqual(listener.router.by_serial, {"SN-GATE": "GATE_01"})
        # The poll's own device list is untouched.
        self.assertNotIn("realtime", self.sync.config.devices[0])

    def test_station_devices_ignores_unknown_devices_and_bad_values(self):
        self.sync.config.devices = [PLAIN]
        self.sync.config.STATION_DEVICES = {"NOT_CONFIGURED": "SN"}
        self.assertIsNone(self.sync.ensure_realtime_listener())
        self.sync.config.STATION_DEVICES = ["GATE_01"]
        self.assertIsNone(self.sync.ensure_realtime_listener())

    def test_a_device_flagged_in_devices_keeps_its_own_serial(self):
        self.sync.config.devices = [STATION]
        self.sync.config.STATION_DEVICES = {"STATION_01": "OTHER"}
        listener = self.sync.ensure_realtime_listener()
        self.assertEqual(listener.router.by_serial, {"ABC123": "STATION_01"})

    def test_the_configured_age_limit_reaches_the_listener(self):
        self.sync.config.devices = [STATION]
        self.sync.STATION_PUNCH_MAX_AGE_SECONDS = 120
        self.assertEqual(self.sync.ensure_realtime_listener().router.max_age_seconds, 120)

    def test_main_releases_the_listener_when_a_stop_is_requested(self):
        self.sync.config.devices = [STATION]
        self.assertIsNotNone(self.sync.ensure_realtime_listener())
        self.sync.main(stop_requested=lambda: True)
        self.assertIsNone(self.sync._realtime_listener)


class SendPunchEventTests(unittest.TestCase):
    def setUp(self):
        from test_erpnext_sync import load_sync_module

        self.logs_directory = pathlib.Path.cwd() / ".test-logs" / self._testMethodName
        if self.logs_directory.exists():
            shutil.rmtree(self.logs_directory)
        self.logs_directory.mkdir(parents=True)
        self.sync = load_sync_module(self.logs_directory)
        self.addCleanup(shutil.rmtree, self.logs_directory, True)
        self.event = PunchEvent(
            device_id="STATION_01",
            serial_number="ABC123",
            user_id="247",
            timestamp=datetime.datetime(2026, 11, 22, 14, 32, 5),
            status="0",
            verify_mode="1",
            workcode="12",
        )

    def test_posts_nothing_when_no_method_is_configured(self):
        self.sync.config.STATION_PUNCH_METHOD = ""
        status_code, message = self.sync.send_punch_event(self.event)
        self.assertEqual(status_code, 0)
        self.assertIn("no station punch method", message.lower())
        self.sync.requests.request.assert_not_called()

    def test_posts_the_punch_to_the_configured_method(self):
        self.sync.config.STATION_PUNCH_METHOD = "myapp.api.station_punch"
        status_code, _ = self.sync.send_punch_event(self.event)
        self.assertEqual(status_code, 200)
        _, kwargs = self.sync.requests.request.call_args
        self.assertEqual(kwargs["json"]["user_id"], "247")
        self.assertEqual(kwargs["json"]["device_id"], "STATION_01")
        self.assertEqual(kwargs["json"]["workcode"], "12")
        # Short, because somebody is waiting at the machine.
        self.assertEqual(kwargs["timeout"], self.sync.STATION_PUNCH_TIMEOUT)

    def test_a_transport_failure_is_reported_and_not_raised(self):
        self.sync.config.STATION_PUNCH_METHOD = "myapp.api.station_punch"
        self.sync.requests.request.side_effect = RuntimeError("no route to host")
        status_code, message = self.sync.send_punch_event(self.event)
        self.assertEqual(status_code, 0)
        self.assertIn("transport failure", message.lower())

    def test_the_url_names_the_configured_method(self):
        self.sync.config.STATION_PUNCH_METHOD = "myapp.api.station_punch"
        self.sync.send_punch_event(self.event)
        args, _ = self.sync.requests.request.call_args
        self.assertTrue(args[1].endswith("/api/method/myapp.api.station_punch"), args[1])


if __name__ == "__main__":
    unittest.main()
