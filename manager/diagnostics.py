import logging
import types
from datetime import datetime
from dataclasses import dataclass, field

try:
    import requests
except ImportError:
    requests = types.SimpleNamespace(
        request=None,
        exceptions=types.SimpleNamespace(Timeout=TimeoutError, ConnectionError=ConnectionError),
    )

from .health import read_status_data
from .paths import get_manager_log_file
from . import support


@dataclass
class DiagnosticResult:
    ok: bool
    status: str
    message: str
    details: list = field(default_factory=list)
    title: str = ""
    action: str = ""
    severity: str = ""
    technical_reference: str = ""

    @classmethod
    def from_message(cls, ok, status, message, details=None, technical_reference=""):
        return cls(
            ok=ok,
            status=status,
            message=message.message,
            details=list(details or message.technical_details),
            title=message.title,
            action=message.action,
            severity=message.severity,
            technical_reference=technical_reference,
        )


def _get_logger(config_module=None):
    log_file = get_manager_log_file(config_module)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("manager")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s\t%(levelname)s\t%(message)s"))
        logger.addHandler(handler)
    return logger


def validate_configuration(sync_module=None):
    if sync_module is None:
        import erpnext_sync as sync_module
    try:
        sync_module.validate_runtime_config()
        return DiagnosticResult(True, "ok", "Configuration is valid.", title="Configuration Valid", action="No action required.", severity=support.SUCCESS)
    except Exception as exc:
        errors = _friendly_validation_errors(str(exc))
        return DiagnosticResult.from_message(False, "error", support.configuration_invalid_message(errors), details=errors, technical_reference="manager.log")


def test_erpnext_connection(config_module=None, request_func=None):
    if config_module is None:
        import erpnext_sync
        config_module = erpnext_sync.config
    request_func = request_func or requests.request
    if request_func is None:
        return DiagnosticResult.from_message(False, "error", support.erpnext_unexpected_message(), technical_reference="manager.log")
    logger = _get_logger(config_module)
    base_url = str(getattr(config_module, "ERPNEXT_URL", "")).rstrip("/")
    timeout = getattr(config_module, "ERPNEXT_REQUEST_TIMEOUT", getattr(config_module, "REQUEST_TIMEOUT", 30))
    verify_ssl = getattr(config_module, "ERPNEXT_VERIFY_SSL", True)
    url = base_url + "/api/method/frappe.auth.get_logged_user"
    headers = {
        "Authorization": "token " + str(getattr(config_module, "ERPNEXT_API_KEY", "")) + ":" + str(getattr(config_module, "ERPNEXT_API_SECRET", "")),
        "Accept": "application/json",
    }
    ssl_error = getattr(requests.exceptions, "SSLError", type("_NeverSSLError", (Exception,), {}))
    try:
        response = request_func("GET", url, headers=headers, timeout=timeout, verify=verify_ssl)
    except requests.exceptions.Timeout:
        logger.exception("ERPNext connection timed out")
        return DiagnosticResult.from_message(False, "error", support.erpnext_timeout_message(), technical_reference="manager.log")
    except ssl_error:
        logger.exception("ERPNext SSL validation failed")
        return DiagnosticResult.from_message(False, "error", support.erpnext_ssl_message(), technical_reference="manager.log")
    except requests.exceptions.ConnectionError:
        logger.exception("ERPNext connection failed")
        return DiagnosticResult.from_message(False, "error", support.erpnext_unreachable_message(), technical_reference="manager.log")
    except Exception:
        logger.exception("Unexpected ERPNext diagnostic failure")
        return DiagnosticResult.from_message(False, "error", support.erpnext_unexpected_message(), technical_reference="manager.log")

    if response.status_code == 200:
        return DiagnosticResult.from_message(True, "ok", support.erpnext_success_message())
    if response.status_code in (401, 403):
        return DiagnosticResult.from_message(False, "error", support.erpnext_authentication_message(), technical_reference="manager.log")
    return DiagnosticResult.from_message(False, "error", support.erpnext_http_error_message(response.status_code), technical_reference="manager.log")


def test_devices(config_module=None, sync_module=None, zk_class=None):
    if sync_module is None:
        import erpnext_sync as sync_module
    if config_module is None:
        config_module = sync_module.config
    if zk_class is None:
        zk_class = sync_module.ZK

    logger = _get_logger(config_module)
    results = []
    all_ok = True
    for raw_device in getattr(config_module, "devices", []) or []:
        conn = None
        try:
            device = sync_module.normalize_device_config(raw_device)
            if not device.get("enabled", True):
                results.append(device["device_id"] + " (" + str(device["ip"]) + ":" + str(device["port"]) + ") - Disabled")
                continue
            zk = zk_class(device["ip"], port=device["port"], timeout=10, password=device["password"])
            conn = zk.connect()
            if hasattr(conn, "get_serialnumber"):
                conn.get_serialnumber()
            results.append(device["device_id"] + " (" + str(device["ip"]) + ":" + str(device["port"]) + ") - Connected")
        except Exception:
            all_ok = False
            device_id = str(raw_device.get("device_id", "Unknown"))
            ip = str(raw_device.get("ip") or raw_device.get("host") or "Unknown")
            port = str(raw_device.get("port", sync_module.DEFAULT_ZK_PORT))
            logger.exception("Device diagnostic failed for " + device_id)
            results.append(device_id + " (" + ip + ":" + port + ") - Connection failed")
        finally:
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception:
                    logger.exception("Device disconnect failed")
    status = "ok" if all_ok else "warning"
    if all_ok:
        message = support.device_connected_message()
        return DiagnosticResult.from_message(True, status, message, details=results)
    return DiagnosticResult.from_message(False, status, support.device_unreachable_message(), details=results, technical_reference="manager.log")


def run_one_sync(sync_module=None):
    if sync_module is None:
        import erpnext_sync as sync_module
    before_timestamp = _read_mission_accomplished_timestamp(sync_module.config)
    try:
        sync_module.main()
    except Exception:
        _get_logger(sync_module.config).exception("Manual sync failed")
        return DiagnosticResult(False, "error", "Synchronization could not be completed.", title="Synchronization Failed", action="Open Logs, fix the reported issue, then retry synchronization.", severity=support.ERROR, technical_reference="manager.log")

    after_timestamp = _read_mission_accomplished_timestamp(sync_module.config)
    if _timestamp_advanced(before_timestamp, after_timestamp):
        return DiagnosticResult(True, "ok", "Manual sync completed.", title="Synchronization Completed", action="No action required.", severity=support.SUCCESS)
    return DiagnosticResult(False, "error", "Manual sync did not complete successfully. Check logs.", title="Synchronization Not Confirmed", action="Open Logs and verify whether attendance records were imported.", severity=support.ERROR, technical_reference="manager.log")


def _read_mission_accomplished_timestamp(config_module):
    status_data, _found = read_status_data(config_module)
    timestamp = status_data.get("mission_accomplished_timestamp")
    if timestamp is None:
        return ""
    return str(timestamp)


def _timestamp_advanced(before_timestamp, after_timestamp):
    if not after_timestamp:
        return False
    before_date = _parse_timestamp(before_timestamp)
    after_date = _parse_timestamp(after_timestamp)
    if not after_date:
        return False
    if after_date and before_date:
        return after_date > before_date
    if not before_timestamp:
        return True
    return before_date is None


def _parse_timestamp(timestamp):
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(str(timestamp))
    except ValueError:
        return None


def _friendly_validation_errors(message):
    lines = []
    for line in message.splitlines():
        clean = line.strip()
        if clean.startswith("- "):
            lines.append(clean)
    if lines:
        return lines
    return ["- " + message.replace("\n", " ")]
