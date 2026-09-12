import logging
import types
from dataclasses import dataclass, field

try:
    import requests
except ImportError:
    requests = types.SimpleNamespace(
        request=None,
        exceptions=types.SimpleNamespace(Timeout=TimeoutError, ConnectionError=ConnectionError),
    )

from .paths import get_manager_log_file


@dataclass
class DiagnosticResult:
    ok: bool
    status: str
    message: str
    details: list = field(default_factory=list)


def _get_logger(config_module=None):
    log_file = get_manager_log_file(config_module)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("manager")
    logger.setLevel(logging.INFO)
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
        return DiagnosticResult(True, "ok", "Configuration is valid.")
    except Exception as exc:
        errors = _friendly_validation_errors(str(exc))
        return DiagnosticResult(False, "error", "Configuration problem:", errors)


def test_erpnext_connection(config_module=None, request_func=None):
    if config_module is None:
        import erpnext_sync
        config_module = erpnext_sync.config
    request_func = request_func or requests.request
    if request_func is None:
        return DiagnosticResult(False, "error", "ERPNext test failed. The requests package is not installed.")
    logger = _get_logger(config_module)
    base_url = str(getattr(config_module, "ERPNEXT_URL", "")).rstrip("/")
    timeout = getattr(config_module, "ERPNEXT_REQUEST_TIMEOUT", getattr(config_module, "REQUEST_TIMEOUT", 30))
    url = base_url + "/api/method/frappe.auth.get_logged_user"
    headers = {
        "Authorization": "token " + str(getattr(config_module, "ERPNEXT_API_KEY", "")) + ":" + str(getattr(config_module, "ERPNEXT_API_SECRET", "")),
        "Accept": "application/json",
    }
    try:
        response = request_func("GET", url, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        logger.exception("ERPNext connection timed out")
        return DiagnosticResult(False, "error", "ERPNext unreachable.")
    except requests.exceptions.ConnectionError:
        logger.exception("ERPNext connection failed")
        return DiagnosticResult(False, "error", "ERPNext unreachable.")
    except Exception:
        logger.exception("Unexpected ERPNext diagnostic failure")
        return DiagnosticResult(False, "error", "ERPNext test failed.")

    if response.status_code == 200:
        return DiagnosticResult(True, "ok", "ERPNext connected successfully.")
    if response.status_code in (401, 403):
        return DiagnosticResult(False, "error", "ERPNext authentication failed.")
    return DiagnosticResult(False, "error", "ERPNext returned HTTP " + str(response.status_code) + ".")


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
    message = "All devices connected." if all_ok else "One or more devices failed."
    return DiagnosticResult(all_ok, status, message, results)


def run_one_sync(sync_module=None):
    if sync_module is None:
        import erpnext_sync as sync_module
    try:
        sync_module.main()
        return DiagnosticResult(True, "ok", "Manual sync completed.")
    except Exception:
        _get_logger(sync_module.config).exception("Manual sync failed")
        return DiagnosticResult(False, "error", "Manual sync failed.")


def _friendly_validation_errors(message):
    lines = []
    for line in message.splitlines():
        clean = line.strip()
        if clean.startswith("- "):
            lines.append(clean)
    if lines:
        return lines
    return ["- " + message.replace("\n", " ")]
