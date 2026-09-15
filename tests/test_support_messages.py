import unittest

from manager import support
from manager.service_controller import ServiceStatus


class SupportMessageTests(unittest.TestCase):
    def test_service_not_installed_message_is_actionable(self):
        message = support.service_not_installed_message()

        self.assertEqual(message.title, "Synchronization Service Not Installed")
        self.assertIn("Install", message.action)

    def test_service_stopped_message_is_actionable(self):
        message = support.service_stopped_message()

        self.assertEqual(message.title, "Synchronization Service Stopped")
        self.assertIn("Start", message.action)

    def test_service_action_failure_without_raw_command_requirement(self):
        message = support.service_action_failed_message("start", detail="sc.exe failed with code 5")

        self.assertEqual(message.title, "Synchronization Service Action Failed")
        self.assertIn("Open Diagnostics or Logs", message.action)
        self.assertNotIn("sc.exe", message.compact())

    def test_service_permission_message_is_actionable(self):
        message = support.service_action_failed_message("start", requires_admin=True)

        self.assertEqual(message.title, "Administrator Rights Required")
        self.assertIn("Administrator", message.action)

    def test_invalid_configuration_message_is_actionable(self):
        message = support.configuration_invalid_message(["- devices must contain at least one device."])

        self.assertEqual(message.title, "Configuration Invalid")
        self.assertIn("Configure", message.action)

    def test_service_status_label_handles_missing_service(self):
        status = ServiceStatus(installed=False, state="Not Installed")

        self.assertEqual(support.status_label_for_service(status), "Not installed")


if __name__ == "__main__":
    unittest.main()
