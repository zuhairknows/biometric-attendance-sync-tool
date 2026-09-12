import ctypes
import subprocess
import sys
import time
from dataclasses import dataclass

from .paths import SERVICE_SCRIPT


SERVICE_NAME = "ERPNextBiometricPushService"
SERVICE_DISPLAY_NAME = "ERPNext Biometric Push Service"


@dataclass
class ActionResult:
    success: bool
    message: str
    requires_admin: bool = False
    command: tuple = ()


@dataclass
class ServiceStatus:
    installed: bool
    state: str = "Unknown"
    startup: str = "Unknown"


def is_running_as_admin():
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _completed_process_text(process):
    output = getattr(process, "stdout", "") or ""
    error = getattr(process, "stderr", "") or ""
    return (output + "\n" + error).strip()


def _parse_state(sc_output):
    for line in sc_output.splitlines():
        if "STATE" not in line:
            continue
        # Example: STATE              : 4  RUNNING
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        tokens = parts[1].strip().split()
        if tokens:
            return tokens[-1].title()
    return "Unknown"


def _parse_startup(sc_output):
    for line in sc_output.splitlines():
        if "START_TYPE" not in line:
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        tokens = parts[1].strip().split(maxsplit=1)
        if len(tokens) == 2:
            return tokens[1].replace("_", " ").title()
    return "Unknown"


class ServiceController:
    def __init__(self, runner=None, python_executable=None, admin_checker=None, sleep=None):
        self.runner = runner or subprocess.run
        self.python_executable = python_executable or sys.executable
        self.admin_checker = admin_checker or is_running_as_admin
        self.sleep = sleep or time.sleep

    def is_installed(self):
        result = self._run(["sc.exe", "query", SERVICE_NAME], timeout=10)
        return result.returncode == 0

    def get_status(self):
        query = self._run(["sc.exe", "query", SERVICE_NAME], timeout=10)
        if query.returncode != 0:
            return ServiceStatus(installed=False, state="Not Installed", startup="Not Installed")

        qc = self._run(["sc.exe", "qc", SERVICE_NAME], timeout=10)
        return ServiceStatus(
            installed=True,
            state=_parse_state(_completed_process_text(query)),
            startup=_parse_startup(_completed_process_text(qc)) if qc.returncode == 0 else "Unknown",
        )

    def install_service(self):
        if not self._has_admin_for_setup():
            return self._admin_required("install")
        command = self._service_script_command("install")
        result = self._run(command, timeout=60)
        return self._action_result(result, "Service installed successfully.", "Could not install the service.", command)

    def uninstall_service(self):
        if not self._has_admin_for_setup():
            return self._admin_required("remove")
        status = self.get_status()
        if status.installed and status.state == "Running":
            stop_result = self.stop_service(wait=True)
            if not stop_result.success:
                return stop_result
        command = self._service_script_command("remove")
        result = self._run(command, timeout=60)
        return self._action_result(result, "Service uninstalled successfully.", "Could not uninstall the service.", command)

    def start_service(self, wait=True):
        result = self._run(["sc.exe", "start", SERVICE_NAME], timeout=30)
        if result.returncode != 0:
            return self._action_result(result, "Service started successfully.", "Could not start the service.", ("sc.exe", "start", SERVICE_NAME))
        if wait and not self._wait_for_state("Running"):
            return ActionResult(False, "Service start was requested, but it did not report Running before timeout.")
        return ActionResult(True, "Service started successfully.", command=("sc.exe", "start", SERVICE_NAME))

    def stop_service(self, wait=True):
        result = self._run(["sc.exe", "stop", SERVICE_NAME], timeout=30)
        if result.returncode != 0:
            return self._action_result(result, "Service stopped successfully.", "Could not stop the service.", ("sc.exe", "stop", SERVICE_NAME))
        if wait and not self._wait_for_state("Stopped"):
            return ActionResult(False, "Service stop was requested, but it did not report Stopped before timeout.")
        return ActionResult(True, "Service stopped successfully.", command=("sc.exe", "stop", SERVICE_NAME))

    def restart_service(self):
        stop_result = self.stop_service(wait=True)
        if not stop_result.success:
            return stop_result
        return self.start_service(wait=True)

    def _has_admin_for_setup(self):
        return bool(self.admin_checker())

    def _admin_required(self, action):
        return ActionResult(
            False,
            "Administrator permission is required to " + action + " the service. Please restart FPF Biometric Sync Manager as Administrator.",
            requires_admin=True,
        )

    def _service_script_command(self, action):
        return (self.python_executable, str(SERVICE_SCRIPT), action)

    def _wait_for_state(self, target_state, timeout_seconds=30):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.get_status().state == target_state:
                return True
            self.sleep(1)
        return False

    def _run(self, command, timeout):
        try:
            return self.runner(list(command), capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(list(command), 1, stdout="", stderr=str(exc))

    def _action_result(self, process, success_message, failure_message, command):
        if process.returncode == 0:
            return ActionResult(True, success_message, command=tuple(command))
        detail = _completed_process_text(process)
        if detail:
            failure_message = failure_message + " " + detail
        return ActionResult(False, failure_message, command=tuple(command))


def is_installed():
    return ServiceController().is_installed()


def get_status():
    return ServiceController().get_status()


def install_service():
    return ServiceController().install_service()


def uninstall_service():
    return ServiceController().uninstall_service()


def start_service():
    return ServiceController().start_service()


def stop_service():
    return ServiceController().stop_service()


def restart_service():
    return ServiceController().restart_service()
