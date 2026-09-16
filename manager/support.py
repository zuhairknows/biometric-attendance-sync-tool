"""User-facing diagnostics and error presentation helpers."""

import platform
from dataclasses import dataclass, field


INFO = "Information"
SUCCESS = "Success"
WARNING = "Warning"
ERROR = "Error"


@dataclass
class OperationalMessage:
    severity: str
    title: str
    message: str
    action: str = ""
    technical_details: list = field(default_factory=list)

    def to_lines(self, include_technical=False):
        lines = [self.title, self.message]
        if self.action:
            lines.append("Suggested action: " + self.action)
        if include_technical and self.technical_details:
            lines.append("Technical details:")
            lines.extend(str(detail) for detail in self.technical_details)
        return lines

    def compact(self):
        lines = [self.title + ": " + self.message]
        if self.action:
            lines.append("Action: " + self.action)
        return " ".join(lines)


def erpnext_success_message():
    return OperationalMessage(
        SUCCESS,
        "ERPNext Connected",
        "The application connected to ERPNext successfully.",
        "No action required.",
    )


def erpnext_authentication_message():
    return OperationalMessage(
        ERROR,
        "ERPNext Authentication Failed",
        "ERPNext rejected the API credentials.",
        "Verify the API Key and API Secret, confirm the ERPNext user is enabled, then retry the connection test.",
    )


def erpnext_unreachable_message():
    return OperationalMessage(
        ERROR,
        "ERPNext Server Unreachable",
        "The application could not reach the ERPNext server.",
        "Check the ERPNext URL, network connection, DNS, firewall, and proxy settings.",
    )


def erpnext_ssl_message():
    return OperationalMessage(
        ERROR,
        "ERPNext SSL Certificate Problem",
        "The ERPNext SSL certificate could not be verified.",
        "Check the certificate on the ERPNext site, or disable SSL verification only if your administrator approves it.",
    )


def erpnext_timeout_message():
    return OperationalMessage(
        ERROR,
        "ERPNext Connection Timed Out",
        "ERPNext did not respond before the connection timeout.",
        "Confirm the ERPNext site is online and reachable from this computer, then retry the connection test.",
    )


def erpnext_http_error_message(status_code):
    return OperationalMessage(
        ERROR,
        "ERPNext Server Error",
        "ERPNext returned HTTP " + str(status_code) + ".",
        "Check ERPNext server status and permissions, then retry the connection test.",
    )


def erpnext_unexpected_message():
    return OperationalMessage(
        ERROR,
        "ERPNext Test Failed",
        "The ERPNext connection test could not be completed.",
        "Open Diagnostics or Logs for technical details, then retry the connection test.",
    )


def device_connected_message():
    return OperationalMessage(SUCCESS, "Biometric Device Connected", "The biometric device connection test succeeded.", "No action required.")


def device_disabled_message():
    return OperationalMessage(INFO, "Biometric Device Disabled", "This biometric device is disabled and was skipped.", "Enable the device before testing or synchronizing it.")


def device_unreachable_message():
    return OperationalMessage(
        WARNING,
        "Biometric Device Unavailable",
        "The application could not connect to one or more biometric devices.",
        "Confirm the device is powered on, verify host/IP and port, check the network path, and retry the device test.",
    )


def configuration_invalid_message(details=None):
    return OperationalMessage(
        ERROR,
        "Configuration Invalid",
        "The configuration is incomplete or invalid.",
        "Open Configure to repair the highlighted settings.",
        list(details or []),
    )


def configuration_missing_message():
    return OperationalMessage(WARNING, "Configuration Missing", "The product is not configured yet.", "Complete first-run setup.")


def service_not_installed_message():
    return OperationalMessage(ERROR, "Synchronization Service Not Installed", "The synchronization service is not installed.", "Install the service or run the packaged installer repair.")


def service_stopped_message():
    return OperationalMessage(ERROR, "Synchronization Service Stopped", "The synchronization service is stopped.", "Start the service. If it fails, open Diagnostics or Logs.")


def service_action_failed_message(action, requires_admin=False, detail=""):
    if requires_admin:
        return OperationalMessage(
            ERROR,
            "Administrator Rights Required",
            "Windows blocked the service action because administrator rights are required.",
            "Open the Manager as Administrator and try again.",
            [detail] if detail else [],
        )
    return OperationalMessage(
        ERROR,
        "Synchronization Service Action Failed",
        "The synchronization service could not " + str(action) + ".",
        "Check the service status, then Open Diagnostics or Logs for technical details.",
        [detail] if detail else [],
    )


def file_operation_failed_message(action):
    return OperationalMessage(ERROR, "File Operation Failed", "The file could not be " + str(action) + ".", "Check file permissions and try again.")


def status_label_for_service(service_status):
    if service_status is None:
        return "Unknown"
    if not getattr(service_status, "installed", False):
        return "Not installed"
    return str(getattr(service_status, "state", "") or "Unknown")


def platform_summary():
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
