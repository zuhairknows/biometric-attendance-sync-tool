"""Frozen Windows service entry point for FPF Biometric Sync."""

from service_runtime import load_runtime_config, prepare_runtime_paths


SERVICE_NAME = "ERPNextBiometricPushService"
SERVICE_DISPLAY_NAME = "ERPNext Biometric Push Service"
SERVICE_DESCRIPTION = "Synchronizes ZKTeco biometric attendance punches with ERPNext HRMS."
SERVICE_CHECK_INTERVAL_MS = 15000
prepare_runtime_paths()
load_runtime_config()

import servicemanager
import win32event
import win32service
import win32serviceutil

import erpnext_sync


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


class FPFBiometricSyncService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.isrunning = False

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.isrunning = False
        log_service_info("Service stop requested")
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        log_service_info("ERPNext Biometric Push Service starting")
        try:
            erpnext_sync.validate_runtime_config()
        except Exception as exc:
            log_service_error("Configuration validation failed: " + str(exc))
            raise
        log_service_info("Configuration validation passed")
        self.isrunning = True
        self.main()

    def main(self):
        log_service_info("Service loop started")
        while self.isrunning:
            try:
                erpnext_sync.main()
            except Exception:
                erpnext_sync.error_logger.exception("Unexpected service cycle exception")
                log_service_error("Unexpected service cycle exception")

            wait_result = win32event.WaitForSingleObject(self.hWaitStop, SERVICE_CHECK_INTERVAL_MS)
            if wait_result == win32event.WAIT_OBJECT_0:
                self.isrunning = False

        log_service_info("Service stopped")


def main():
    win32serviceutil.HandleCommandLine(FPFBiometricSyncService)


if __name__ == "__main__":
    main()
