import sys
import time
from pathlib import Path

import servicemanager

from SMWinservice import SMWinservice
from erpnext_sync import main


class PythonCornerExample(SMWinservice):
    _svc_name_ = "ERPNextBiometricPushService"
    _svc_display_name_ = "ERPNext Biometric Push Service"
    _svc_description_ = "Service to push biometric data from device to ERPNext"

    # IMPORTANT:
    # Host the Windows Service directly with the Python interpreter from
    # the current virtual environment instead of pythonservice.exe.
    #
    # During installation this becomes approximately:
    #
    #   "C:\...\ .venv\Scripts\python.exe"
    #   "C:\...\erpnext_sync_win.py"
    #
    # This avoids the pythonservice.exe hosting problem we have confirmed.
    _exe_name_ = sys.executable
    _exe_args_ = f'"{Path(__file__).resolve()}"'

    def start(self):
        self.isrunning = True

    def stop(self):
        self.isrunning = False

    def main(self):
        while self.isrunning:
            main()
            time.sleep(15)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Windows SCM started this script directly through python.exe.
        #
        # Prepare this Python process to behave as a native Windows
        # service host and hand control to the Service Control Manager.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(PythonCornerExample)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # install / update / remove / start / stop / debug commands
        PythonCornerExample.parse_command_line()