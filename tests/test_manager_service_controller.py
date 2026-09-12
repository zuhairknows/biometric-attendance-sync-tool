import subprocess
import unittest

from manager.service_controller import SERVICE_NAME, ServiceController


class FakeRunner:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def __call__(self, command, capture_output=True, text=True, timeout=None):
        self.calls.append((command, timeout))
        if self.responses:
            return self.responses.pop(0)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def completed(command=None, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command or [], returncode, stdout=stdout, stderr=stderr)


class ServiceControllerTests(unittest.TestCase):
    def test_installed_service_detection(self):
        runner = FakeRunner([completed(stdout="SERVICE_NAME: " + SERVICE_NAME)])
        controller = ServiceController(runner=runner)

        self.assertTrue(controller.is_installed())
        self.assertEqual(runner.calls[0][0], ["sc.exe", "query", SERVICE_NAME])

    def test_missing_service_detection(self):
        runner = FakeRunner([completed(returncode=1060, stderr="service does not exist")])
        controller = ServiceController(runner=runner)

        self.assertFalse(controller.is_installed())

    def test_status_parsing(self):
        query_output = "STATE              : 4  RUNNING\n"
        qc_output = "START_TYPE         : 2   AUTO_START  (DELAYED)\n"
        runner = FakeRunner([completed(stdout=query_output), completed(stdout=qc_output)])
        controller = ServiceController(runner=runner)

        status = controller.get_status()

        self.assertTrue(status.installed)
        self.assertEqual(status.state, "Running")
        self.assertIn("Auto", status.startup)

    def test_install_command_construction(self):
        runner = FakeRunner([completed()])
        controller = ServiceController(runner=runner, python_executable="python.exe", admin_checker=lambda: True)

        result = controller.install_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[0][0][0], "python.exe")
        self.assertTrue(runner.calls[0][0][1].endswith("erpnext_sync_win.py"))
        self.assertEqual(runner.calls[0][0][2], "install")

    def test_install_requires_admin(self):
        runner = FakeRunner()
        controller = ServiceController(runner=runner, admin_checker=lambda: False)

        result = controller.install_service()

        self.assertFalse(result.success)
        self.assertTrue(result.requires_admin)
        self.assertEqual(runner.calls, [])

    def test_uninstall_flow_stops_running_service_then_removes(self):
        runner = FakeRunner([
            completed(stdout="STATE              : 4  RUNNING\n"),
            completed(stdout="START_TYPE         : 2   AUTO_START\n"),
            completed(),
            completed(stdout="STATE              : 1  STOPPED\n"),
            completed(stdout="START_TYPE         : 2   AUTO_START\n"),
            completed(),
        ])
        controller = ServiceController(runner=runner, python_executable="python.exe", admin_checker=lambda: True, sleep=lambda _seconds: None)

        result = controller.uninstall_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[2][0], ["sc.exe", "stop", SERVICE_NAME])
        self.assertEqual(runner.calls[-1][0][2], "remove")

    def test_restart_flow_stops_then_starts(self):
        runner = FakeRunner([
            completed(),
            completed(stdout="STATE              : 1  STOPPED\n"),
            completed(stdout="START_TYPE         : 2   AUTO_START\n"),
            completed(),
            completed(stdout="STATE              : 4  RUNNING\n"),
            completed(stdout="START_TYPE         : 2   AUTO_START\n"),
        ])
        controller = ServiceController(runner=runner, sleep=lambda _seconds: None)

        result = controller.restart_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[0][0], ["sc.exe", "stop", SERVICE_NAME])
        self.assertEqual(runner.calls[3][0], ["sc.exe", "start", SERVICE_NAME])


if __name__ == "__main__":
    unittest.main()

