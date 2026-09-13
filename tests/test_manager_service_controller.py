import subprocess
import unittest
from pathlib import Path

from manager import paths
from manager.paths import ServiceRuntime
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


def runtime_for(command_prefix):
    return ServiceRuntime("packaged" if len(command_prefix) == 1 else "development", Path(command_prefix[0]), tuple(command_prefix), "test")


def status_responses(state="RUNNING", startup="AUTO_START", delayed=False, recovery=""):
    delayed_text = "DELAYED_AUTO_START  : 1\n" if delayed else "DELAYED_AUTO_START  : 0\n"
    return [
        completed(stdout="STATE              : 4  " + state + "\n"),
        completed(stdout="START_TYPE         : 2   " + startup + "\n"),
        completed(stdout=delayed_text),
        completed(stdout=recovery),
    ]


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
        qc_output = "START_TYPE         : 2   AUTO_START\n"
        delayed_output = "DELAYED_AUTO_START  : 1\n"
        failure_output = "FAILURE_ACTIONS    : RESTART -- Delay = 60000 milliseconds.\n"
        runner = FakeRunner([completed(stdout=query_output), completed(stdout=qc_output), completed(stdout=delayed_output), completed(stdout=failure_output)])
        controller = ServiceController(runner=runner)

        status = controller.get_status()

        self.assertTrue(status.installed)
        self.assertEqual(status.state, "Running")
        self.assertEqual(status.startup, "Automatic (Delayed Start)")
        self.assertEqual(status.recovery, "Restart after 60s")

    def test_service_state_parsing_start_pending(self):
        runner = FakeRunner(status_responses(state="START_PENDING"))
        controller = ServiceController(runner=runner)

        self.assertEqual(controller.get_status().state, "Start Pending")

    def test_service_state_parsing_stop_pending(self):
        runner = FakeRunner(status_responses(state="STOP_PENDING"))
        controller = ServiceController(runner=runner)

        self.assertEqual(controller.get_status().state, "Stop Pending")

    def test_service_state_parsing_stopped(self):
        runner = FakeRunner(status_responses(state="STOPPED"))
        controller = ServiceController(runner=runner)

        self.assertEqual(controller.get_status().state, "Stopped")

    def test_startup_parsing_manual_auto_and_disabled(self):
        cases = [
            ("DEMAND_START", False, "Manual"),
            ("AUTO_START", False, "Automatic"),
            ("AUTO_START", True, "Automatic (Delayed Start)"),
            ("DISABLED", False, "Disabled"),
        ]
        for startup, delayed, expected in cases:
            with self.subTest(startup=startup, delayed=delayed):
                runner = FakeRunner(status_responses(startup=startup, delayed=delayed))
                controller = ServiceController(runner=runner)
                self.assertEqual(controller.get_status().startup, expected)

    def test_install_command_construction(self):
        runner = FakeRunner([completed(returncode=1060), completed()])
        controller = ServiceController(
            runner=runner,
            python_executable="python.exe",
            admin_checker=lambda: True,
            service_runtime_resolver=lambda python_executable=None: runtime_for(("python.exe", str(paths.SERVICE_SCRIPT))),
        )

        result = controller.install_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[1][0][0], "python.exe")
        self.assertTrue(runner.calls[1][0][1].endswith("erpnext_sync_win.py"))
        self.assertEqual(runner.calls[1][0][2], "install")

    def test_service_executable_command_construction(self):
        runner = FakeRunner([completed(returncode=1060), completed()])
        service_exe = Path(r"C:\Program Files\FPF Biometric Sync\FPF-Biometric-Sync-Service.exe")
        controller = ServiceController(
            runner=runner,
            python_executable="python.exe",
            admin_checker=lambda: True,
            service_runtime_resolver=lambda python_executable=None: runtime_for((str(service_exe),)),
        )

        result = controller.install_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[1][0], [str(service_exe), "install"])

    def test_manager_uses_frozen_service_executable_when_available(self):
        test_dir = Path.cwd() / ".test-logs" / self._testMethodName
        test_dir.mkdir(parents=True, exist_ok=True)
        service_exe = test_dir / paths.SERVICE_EXE_NAME
        service_exe.write_text("placeholder", encoding="utf-8")
        with unittest.mock.patch.object(paths, "is_frozen_app", return_value=True), unittest.mock.patch.object(paths, "get_app_root", return_value=test_dir):
            self.assertEqual(paths.get_packaged_service_executable(), service_exe.resolve())

    def test_install_requires_admin(self):
        runner = FakeRunner()
        controller = ServiceController(runner=runner, admin_checker=lambda: False)

        result = controller.install_service()

        self.assertFalse(result.success)
        self.assertTrue(result.requires_admin)
        self.assertEqual(runner.calls, [])

    def test_uninstall_flow_stops_running_service_then_removes(self):
        runner = FakeRunner([
            *status_responses(state="RUNNING"),
            *status_responses(state="RUNNING"),
            completed(),
            *status_responses(state="STOPPED"),
            completed(),
        ])
        controller = ServiceController(
            runner=runner,
            python_executable="python.exe",
            admin_checker=lambda: True,
            sleep=lambda _seconds: None,
            service_runtime_resolver=lambda python_executable=None: runtime_for(("python.exe", str(paths.SERVICE_SCRIPT))),
        )

        result = controller.uninstall_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[8][0], ["sc.exe", "stop", SERVICE_NAME])
        self.assertEqual(runner.calls[-1][0][2], "remove")

    def test_restart_flow_stops_then_starts(self):
        runner = FakeRunner([
            *status_responses(state="RUNNING"),
            *status_responses(state="RUNNING"),
            completed(),
            *status_responses(state="STOPPED"),
            *status_responses(state="STOPPED"),
            completed(),
            *status_responses(state="RUNNING"),
        ])
        controller = ServiceController(runner=runner, sleep=lambda _seconds: None)

        result = controller.restart_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[8][0], ["sc.exe", "stop", SERVICE_NAME])
        self.assertEqual(runner.calls[17][0], ["sc.exe", "start", SERVICE_NAME])

    def test_install_reports_already_installed(self):
        runner = FakeRunner([completed(stdout="SERVICE_NAME: " + SERVICE_NAME)])
        controller = ServiceController(runner=runner, admin_checker=lambda: True)

        result = controller.install_service()

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Service already installed.")
        self.assertEqual(len(runner.calls), 1)

    def test_uninstall_missing_service_is_friendly(self):
        runner = FakeRunner([completed(returncode=1060, stderr="service does not exist")])
        controller = ServiceController(runner=runner, admin_checker=lambda: True)

        result = controller.uninstall_service()

        self.assertTrue(result.success)
        self.assertEqual(result.message, "Service is not installed.")

    def test_start_success_waits_for_running(self):
        runner = FakeRunner([
            *status_responses(state="STOPPED"),
            completed(),
            *status_responses(state="RUNNING"),
        ])
        controller = ServiceController(runner=runner, sleep=lambda _seconds: None)

        result = controller.start_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[4][0], ["sc.exe", "start", SERVICE_NAME])

    def test_start_timeout_reports_final_state(self):
        clock = [0]
        runner = FakeRunner([
            *status_responses(state="STOPPED"),
            completed(),
            *status_responses(state="STOPPED"),
            *status_responses(state="STOPPED"),
        ])
        controller = ServiceController(runner=runner, sleep=lambda _seconds: clock.__setitem__(0, 31), time_func=lambda: clock[0])

        result = controller.start_service()

        self.assertFalse(result.success)
        self.assertIn("final state is Stopped", result.message)

    def test_stop_success_waits_for_stopped(self):
        runner = FakeRunner([
            *status_responses(state="RUNNING"),
            completed(),
            *status_responses(state="STOPPED"),
        ])
        controller = ServiceController(runner=runner, sleep=lambda _seconds: None)

        result = controller.stop_service()

        self.assertTrue(result.success)
        self.assertEqual(runner.calls[4][0], ["sc.exe", "stop", SERVICE_NAME])

    def test_start_service_not_installed(self):
        runner = FakeRunner([completed(returncode=1060, stderr="service does not exist")])
        controller = ServiceController(runner=runner)

        result = controller.start_service()

        self.assertFalse(result.success)
        self.assertEqual(result.message, "Service is not installed.")

    def test_access_denied_is_friendly(self):
        runner = FakeRunner([
            *status_responses(state="STOPPED"),
            completed(returncode=5, stderr="Access is denied."),
        ])
        controller = ServiceController(runner=runner)

        result = controller.start_service()

        self.assertFalse(result.success)
        self.assertTrue(result.requires_admin)
        self.assertEqual(result.message, "Administrator privileges are required for this action.")


if __name__ == "__main__":
    unittest.main()
