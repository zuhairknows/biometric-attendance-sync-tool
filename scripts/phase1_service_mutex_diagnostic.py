"""Ledger-free, staging-only service-token mutex observation. No install CLI."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import sys

import servicemanager
import win32api
import win32con
import win32security
import win32service
import win32serviceutil

NAME = 'FPFPhase1Staging'
SID = 'S-1-5-80-3239607251-981885122-1049059141-1040181771-757005368'


def process_sid():
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    finally:
        token.Close()


def observe_mutex():
    # Same ABI as the pinned worker, but never wait/acquire or open a ledger.
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = api.CreateMutexW(None, False, r'Global\ERPNextBiometricPushService-DurableWorker')
    error = ctypes.get_last_error()  # Capture before cleanup or any other API.
    value = dict(operation='CreateMutexW', win32_error=error, handle_opened=bool(handle))
    if handle:
        closed = api.CloseHandle(handle)
        close_error = ctypes.get_last_error()
        value.update(existed=error == 183, handle_closed=bool(closed))
        if not closed:
            return dict(value, status='FAIL', close_error=close_error)
    return dict(value, status='PASS' if handle else 'FAIL')


def run_probe(report):
    value = dict(scope='service_token_mutex_only', pid=os.getpid(), ledger_opened=False)
    try:
        value['sid'] = process_sid()
        if value['sid'] != SID:
            raise ValueError('wrong_staging_token')
        value.update(observe_mutex())
    except Exception as error:
        value.update(status='FAIL', category=type(error).__name__)
    # Only the actual service process creates this NEW evidence file.
    with report.open('x', encoding='ascii') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def install_guard(report):
    runtime = Path(sys.executable).parent
    source = Path(__file__).absolute()

    def audit(event, args):
        if (event.startswith(('socket.', 'subprocess.', 'winreg.', 'os.exec', 'os.spawn'))
                or event in {'os.system', 'sqlite3.connect'}):
            raise PermissionError('staging_mutex_only')
        if event == 'ctypes.dlopen' and args[0] != 'kernel32':
            raise PermissionError('staging_native_library_blocked')
        if event == 'ctypes.dlsym' and args[1] not in {'CreateMutexW', 'CloseHandle'}:
            raise PermissionError('staging_native_function_blocked')
        if event == 'open' and not isinstance(args[0], int):
            path = Path(os.path.abspath(os.fsdecode(args[0])))
            writing = args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if (writing and path != report) or (not writing and path != source and runtime not in path.parents):
                raise PermissionError('staging_file_access_blocked')
    sys.addaudithook(audit)


class MutexProbeService(win32serviceutil.ServiceFramework):
    _svc_name_ = NAME
    _svc_display_name_ = 'FPF staging mutex diagnostic'

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)

    def SvcDoRun(self):
        run_probe(REPORT)


if __name__ == '__main__':
    # The runner copies this source into the EXISTING protected code directory.
    source = Path(__file__).absolute()
    match = re.fullmatch(r'phase1_service_mutex_([0-9a-f]{32})\.py', source.name)
    if (not match or source.parent.name != 'code' or not re.fullmatch(r'scm-[0-9a-f]{32}', source.parent.parent.name)
            or len(sys.argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)):
        raise SystemExit(1)
    REPORT = source.parent.parent / 'data' / ('service-mutex-' + match[1] + '.json')
    install_guard(REPORT)
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(MutexProbeService)
    servicemanager.StartServiceCtrlDispatcher()
