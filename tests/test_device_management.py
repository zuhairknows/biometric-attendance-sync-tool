import unittest

from manager.device_management import (
    CONNECTED,
    DISABLED,
    NOT_TESTED,
    UNAVAILABLE,
    bulk_test_summary,
    display_connectivity_status,
    format_device_timestamp,
    friendly_device_failure_message,
    summarize_connectivity_counts,
    validate_device_form_values,
)


class DeviceManagementHelperTests(unittest.TestCase):
    def test_enabled_device_without_test_is_not_tested(self):
        self.assertEqual(display_connectivity_status(True, None), NOT_TESTED)

    def test_disabled_device_is_disabled_not_failed(self):
        self.assertEqual(display_connectivity_status(False, "Failed"), DISABLED)
        self.assertEqual(display_connectivity_status(False, None), DISABLED)

    def test_failed_legacy_state_displays_as_unavailable(self):
        self.assertEqual(display_connectivity_status(True, "Failed"), UNAVAILABLE)

    def test_connected_state_is_preserved(self):
        self.assertEqual(display_connectivity_status(True, "Connected"), CONNECTED)

    def test_empty_timestamp_displays_no_data(self):
        self.assertEqual(format_device_timestamp(""), "No data")

    def test_iso_timestamp_is_friendly(self):
        self.assertEqual(format_device_timestamp("2026-09-15 14:30:05.123456"), "15 Sep 2026, 14:30")

    def test_unparsed_timestamp_is_returned_without_online_interpretation(self):
        self.assertEqual(format_device_timestamp("older status text"), "older status text")

    def test_valid_device_form_has_no_errors(self):
        errors = validate_device_form_values("Front Gate", "GATE_01", "192.168.1.50", 4370, [])
        self.assertEqual(errors, [])

    def test_device_name_is_required(self):
        errors = validate_device_form_values("", "GATE_01", "192.168.1.50", 4370, [])
        self.assertIn("Device name is required.", errors)

    def test_device_id_is_required(self):
        errors = validate_device_form_values("Front Gate", "", "192.168.1.50", 4370, [])
        self.assertIn("Device ID is required.", errors)

    def test_device_id_reuses_schema_validation(self):
        errors = validate_device_form_values("Front Gate", "bad id", "192.168.1.50", 4370, [])
        self.assertTrue(any("Device ID" in error and "invalid" in error for error in errors))

    def test_host_is_required(self):
        errors = validate_device_form_values("Front Gate", "GATE_01", "", 4370, [])
        self.assertIn("Host/IP is required.", errors)

    def test_port_must_be_numeric(self):
        errors = validate_device_form_values("Front Gate", "GATE_01", "zk.local", "abc", [])
        self.assertIn("Port must be a number between 1 and 65535.", errors)

    def test_port_must_be_in_range(self):
        errors = validate_device_form_values("Front Gate", "GATE_01", "zk.local", 70000, [])
        self.assertIn("Port must be between 1 and 65535.", errors)

    def test_duplicate_device_id_is_rejected(self):
        errors = validate_device_form_values("Front Gate", "GATE_01", "zk.local", 4370, ["GATE_01"])
        self.assertIn("Device ID must be unique.", errors)

    def test_summary_counts_connected_unavailable_disabled_and_not_tested(self):
        counts = summarize_connectivity_counts([CONNECTED, UNAVAILABLE, DISABLED, NOT_TESTED])
        self.assertEqual(counts[CONNECTED], 1)
        self.assertEqual(counts[UNAVAILABLE], 1)
        self.assertEqual(counts[DISABLED], 1)
        self.assertEqual(counts[NOT_TESTED], 1)

    def test_bulk_summary_is_customer_friendly(self):
        summary = bulk_test_summary([CONNECTED, UNAVAILABLE, DISABLED])
        self.assertEqual(summary, "Connected: 1 | Unavailable: 1 | Disabled: 1")

    def test_failure_message_is_friendly_without_exception_details(self):
        result = type("Result", (), {"message": ""})()
        self.assertIn("Device is unavailable.", friendly_device_failure_message(result))


if __name__ == "__main__":
    unittest.main()
