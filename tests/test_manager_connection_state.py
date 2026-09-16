"""Regression tests for Manager connection-state ownership and refresh ordering.

Live symptom: Test Devices reported all 12 devices Connected in Messages, while
the device table's Connection column stayed Unknown and "Refreshing..." was on
screen. The connectivity result was stored but the repaint that published it was
dropped, because refresh() no-ops whenever a refresh is already in flight.
"""

import types
import unittest

from test_manager_app import (
    FakeController,
    app_module,
    install_fake_pyqt,
    wait_for_refresh,
)

from config.status import CONFIGURED, ConfigurationStatus
from manager.diagnostics import DiagnosticResult
from manager.health import DeviceHealth, HealthSnapshot
from manager.service_controller import ServiceStatus

install_fake_pyqt()

CONNECTION_COLUMN = app_module.CONNECTION_COLUMN
LATEST_SYNC_COLUMN = app_module.LATEST_SYNC_COLUMN

DEVICE_IDS = ["FP1_DEVICE_%02d" % index for index in range(1, 7)] + [
    "FP2_DEVICE_%02d" % index for index in range(7, 13)
]


def device_health(device_ids=None, sync_outcome=""):
    device_ids = DEVICE_IDS if device_ids is None else device_ids
    return [
        DeviceHealth(
            device_id=device_id,
            ip="10.0.0.%d" % (10 + index),
            port=4370,
            last_pull="2026-09-16 09:00:00",
            last_push="2026-09-16 09:01:00",
            sync_outcome=sync_outcome,
        )
        for index, device_id in enumerate(device_ids)
    ]


def connected_details(device_ids=None):
    device_ids = DEVICE_IDS if device_ids is None else device_ids
    return [
        "%s (10.0.0.%d:4370) - Connected" % (device_id, 10 + index)
        for index, device_id in enumerate(device_ids)
    ]


def running_status():
    return ServiceStatus(installed=True, state="Running", startup="Automatic", recovery="None")


class SnapshotWindow(app_module.SyncManagerWindow):
    """Window whose refresh snapshot is fully controlled by the test."""

    devices = DEVICE_IDS
    # Deliberately not named `service_status`: the window already binds that name
    # to a QLabel built by _build_ui.
    forced_service_status = None

    def _load_sync_module_safely(self):
        self.config_module = types.SimpleNamespace(LOGS_DIRECTORY=".test-logs")
        self.sync_module = types.SimpleNamespace(config=self.config_module)

    def _collect_refresh_snapshot(self):
        return self.build_snapshot()

    def build_snapshot(self, devices=None, service_status=None):
        snapshot = app_module.DashboardSnapshot()
        snapshot.configuration_status = ConfigurationStatus(CONFIGURED, "json", "Configured")
        snapshot.service_status = service_status or self.forced_service_status or running_status()
        snapshot.health = HealthSnapshot(
            last_successful_sync="2026-09-16 09:01:00",
            devices=device_health(devices if devices is not None else self.devices),
            warnings=[],
            cycle_summary={},
            status_file_found=True,
        )
        snapshot.configuration_summary = {"total_devices": 12, "enabled_devices": 12}
        snapshot.config_module = types.SimpleNamespace(LOGS_DIRECTORY=".test-logs")
        snapshot.sync_module = types.SimpleNamespace()
        return snapshot


class ManagerConnectionStateTests(unittest.TestCase):
    def setUp(self):
        self.window = SnapshotWindow(controller=FakeController(running_status()))
        wait_for_refresh(self.window)

    def tearDown(self):
        for job in list(self.window.active_jobs):
            job["thread"].quit()
            job["thread"].wait(1000)
        self.window.close()

    def connection_column(self):
        return [
            self.window.device_table.item(row, CONNECTION_COLUMN).text()
            for row in range(self.window.device_table.rowCount())
        ]

    def device_column(self):
        return [
            self.window.device_table.item(row, 0).text()
            for row in range(self.window.device_table.rowCount())
        ]

    def finish_device_test(self, device_ids=None):
        result = DiagnosticResult(
            ok=True,
            status="ok",
            message="All devices connected.",
            details=connected_details(device_ids),
        )
        self.window._handle_diagnostic_result(result, self.window.device_button)

    def test_all_twelve_devices_start_unknown(self):
        self.assertEqual(self.window.device_table.rowCount(), 12)
        self.assertEqual(self.connection_column(), ["Unknown"] * 12)

    def test_connection_test_result_is_published_even_while_a_refresh_is_running(self):
        # This is the live failure: the repaint used to be dropped here.
        self.window.refresh_running = True

        self.finish_device_test()

        self.assertEqual(self.connection_column(), ["Connected"] * 12)

    def test_older_refresh_result_cannot_revert_connection_to_unknown(self):
        # Refresh starts and captures a snapshot that predates the device test.
        self.window.refresh_running = True
        stale_snapshot = self.window.build_snapshot()

        # Test Devices finishes and reports every device Connected.
        self.finish_device_test()
        self.assertEqual(self.connection_column(), ["Connected"] * 12)

        # The older refresh result arrives afterwards.
        self.window._handle_refresh_result(stale_snapshot)

        self.assertEqual(self.connection_column(), ["Connected"] * 12)
        self.assertEqual(self.window.device_table.rowCount(), 12)

    def test_stale_refresh_generation_is_discarded(self):
        self.window._applied_refresh_generation = 7
        stale = self.window.build_snapshot(service_status=ServiceStatus(
            installed=True, state="Stop Pending", startup="Automatic", recovery="None"
        ))
        self.window.last_service_status = running_status()

        self.window._handle_refresh_result(stale, generation=3)

        self.assertEqual(self.window.last_service_status.state, "Running")

    def test_newer_refresh_generation_is_applied(self):
        self.window._applied_refresh_generation = 3
        newer = self.window.build_snapshot(service_status=ServiceStatus(
            installed=True, state="Stopped", startup="Automatic", recovery="None"
        ))

        self.window._handle_refresh_result(newer, generation=4)

        self.assertEqual(self.window.last_service_status.state, "Stopped")
        self.assertEqual(self.window._applied_refresh_generation, 4)

    def test_queued_refresh_runs_after_the_in_flight_one_completes(self):
        # A refresh requested while another is running must not be lost, or the
        # manager keeps showing Stop Pending after the service has actually stopped.
        self.window.refresh_running = True
        self.window.refresh()
        self.assertTrue(self.window._refresh_pending)

        self.window._handle_refresh_result(self.window.build_snapshot(), generation=self.window._refresh_generation)
        wait_for_refresh(self.window)

        self.assertFalse(self.window._refresh_pending)
        self.assertEqual(self.window.device_table.rowCount(), 12)

    def test_connection_survives_a_full_refresh_cycle(self):
        self.finish_device_test()
        wait_for_refresh(self.window)

        self.window._handle_refresh_result(self.window.build_snapshot(), generation=self.window._refresh_generation + 1)

        self.assertEqual(self.connection_column(), ["Connected"] * 12)

    def test_connection_and_latest_sync_are_separate_fields(self):
        self.finish_device_test()
        self.window._handle_refresh_result(
            self.window.build_snapshot(),
            generation=self.window._refresh_generation + 1,
        )

        latest_sync = [
            self.window.device_table.item(row, LATEST_SYNC_COLUMN).text()
            for row in range(self.window.device_table.rowCount())
        ]
        # Connection comes from the connectivity test; Latest Sync comes from the
        # persisted sync status. Missing sync data must not blank the Connection.
        self.assertEqual(self.connection_column(), ["Connected"] * 12)
        self.assertEqual(latest_sync, ["No sync data"] * 12)

    def test_failed_devices_are_reported_as_failed(self):
        details = connected_details()[:-1] + ["FP2_DEVICE_12 (10.0.0.21:4370) - Connection failed"]
        self.window._handle_diagnostic_result(
            DiagnosticResult(ok=False, status="warning", message="One device failed.", details=details),
            self.window.device_button,
        )

        self.assertEqual(self.connection_column(), ["Connected"] * 11 + ["Failed"])

    def test_row_identity_follows_device_id_not_row_index(self):
        self.finish_device_test(device_ids=DEVICE_IDS[:1])
        reordered = list(reversed(DEVICE_IDS))

        self.window._handle_refresh_result(
            self.window.build_snapshot(devices=reordered),
            generation=self.window._refresh_generation + 1,
        )

        self.assertEqual(self.device_column(), reordered)
        connection = self.connection_column()
        # FP1_DEVICE_01 is the only tested device and it is now the last row.
        self.assertEqual(connection[-1], "Connected")
        self.assertEqual(connection[:-1], ["Unknown"] * 11)


if __name__ == "__main__":
    unittest.main()
