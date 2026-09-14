"""Frozen Windows service entry point for Biometric Attendance Sync."""

import sys

from service_runtime import load_runtime_config, prepare_runtime_paths
from config.status import INVALID, UNCONFIGURED, get_configuration_status


SERVICE_NAME = "ERPNextBiometricPushService"
SERVICE_DISPLAY_NAME = "ERPNext Biometric Push Service"
SERVICE_DESCRIPTION = "Synchronizes ZKTeco biometric attendance punches with ERPNext HRMS."
SERVICE_CHECK_INTERVAL_MS = 15000

import servicemanager
import win32event
import win32service
import win32serviceutil

erpnext_sync = None
service_unconfigured = False
service_invalid_configuration = False


def load_sync_runtime():
    global erpnext_sync
    if erpnext_sync is None:
        prepare_runtime_paths()
        load_runtime_config()
        import erpnext_sync as sync_module
        erpnext_sync = sync_module
    return erpnext_sync


def log_service_info(message):
    if erpnext_sync is not None:
        erpnext_sync.info_logger.info(message)
    try:
        servicemanager.LogInfoMsg(message)
    except Exception:
        pass


def log_service_error(message):
    if erpnext_sync is not None:
        erpnext_sync.error_logger.error(message)
    try:
        servicemanager.LogErrorMsg(message)
    except Exception:
        pass


class BiometricAttendanceSyncService(win32serviceutil.ServiceFramework):
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
        global service_invalid_configuration, service_unconfigured
        configuration_status = get_configuration_status()
        if configuration_status.state == UNCONFIGURED:
            service_unconfigured = True
            service_invalid_configuration = False
            log_service_info("Product is not configured. Complete first-run setup.")
            self.isrunning = True
            self.main()
            return
        if configuration_status.state == INVALID:
            service_unconfigured = False
            service_invalid_configuration = True
            log_service_error("Configuration is invalid. Complete setup or repair protected secrets.")
            self.isrunning = True
            self.main()
            return
        service_unconfigured = False
        service_invalid_configuration = False
        sync_module = load_sync_runtime()
        log_service_info("ERPNext Biometric Push Service starting")
        try:
            sync_module.validate_runtime_config()
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
                if service_unconfigured:
                    log_service_info("Product is not configured. Complete first-run setup.")
                elif service_invalid_configuration:
                    log_service_error("Configuration is invalid. Complete setup or repair protected secrets.")
                else:
                    load_sync_runtime().main()
            except Exception:
                if erpnext_sync is not None:
                    erpnext_sync.error_logger.exception("Unexpected service cycle exception")
                log_service_error("Unexpected service cycle exception")

            wait_result = win32event.WaitForSingleObject(self.hWaitStop, SERVICE_CHECK_INTERVAL_MS)
            if wait_result == win32event.WAIT_OBJECT_0:
                self.isrunning = False

        log_service_info("Service stopped")


def run_service_dispatch(argv=None):
    argv = argv or sys.argv
    if len(argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(BiometricAttendanceSyncService)
        servicemanager.StartServiceCtrlDispatcher()
        return
    win32serviceutil.HandleCommandLine(BiometricAttendanceSyncService)


def main():
    run_service_dispatch()


if __name__ == "__main__":
    main()
