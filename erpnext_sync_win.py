import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Windows services may start from C:\Windows\System32. The sync tool still uses
# Python-source config and relative log paths, so make those resolve from here.
os.chdir(str(APP_DIR))

import servicemanager
import win32event

import erpnext_sync
from SMWinservice import SMWinservice


SERVICE_CHECK_INTERVAL_MS = 15000


def log_service_info(message):
    erpnext_sync.info_logger.info(message)
    try:
        servicemanager.LogInfoMsg(message)
    except Exception:
        pass


def log_service_error(message):
    erpnext_sync.error_logger.error(message)
    try:
        servicemanager.LogErrorMsg(message)
    except Exception:
        pass


class PythonCornerExample(SMWinservice):
    _svc_name_ = "ERPNextBiometricPushService"
    _svc_display_name_ = "ERPNext Biometric Push Service"
    _svc_description_ = "Synchronizes ZKTeco biometric attendance punches with ERPNext HRMS."

    def start(self):
        log_service_info("ERPNext Biometric Push Service starting")
        if getattr(erpnext_sync.config, "CONFIG_SOURCE", "") == "defaults":
            log_service_info("Product is not configured. Complete first-run setup.")
            self.isrunning = True
            return
        try:
            erpnext_sync.validate_runtime_config()
        except Exception as e:
            log_service_error("Configuration validation failed: "+str(e))
            raise
        log_service_info("Configuration validation passed")
        self.isrunning = True

    def stop(self):
        self.isrunning = False
        log_service_info("Service stop requested")
        log_service_info("Finishing current device before shutdown")

    def main(self):
        log_service_info("Service loop started")
        while self.isrunning:
            try:
                if getattr(erpnext_sync.config, "CONFIG_SOURCE", "") == "defaults":
                    log_service_info("Product is not configured. Complete first-run setup.")
                else:
                    erpnext_sync.main(stop_requested=self.stop_requested)
            except Exception:
                erpnext_sync.error_logger.exception("Unexpected service cycle exception")
                log_service_error("Unexpected service cycle exception")

            wait_result = win32event.WaitForSingleObject(self.hWaitStop, SERVICE_CHECK_INTERVAL_MS)
            if wait_result == win32event.WAIT_OBJECT_0:
                self.isrunning = False

        log_service_info("Service loop exiting")
        log_service_info("Service stopped")

    def stop_requested(self):
        return not self.isrunning


if __name__ == '__main__':
    PythonCornerExample.parse_command_line()
