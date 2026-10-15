"""Local capture guards only: no sync engine, config imports, database, or API client."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SERVICE_NAME = 'ERPNextBiometricPushService'
MIN_FREE_BYTES = 256 * 1024 * 1024
PROTECTED_ROOTS = (Path(r'C:\GitHub\biometric-attendance-sync-tool'),
                   Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / SERVICE_NAME)


class CaptureError(Exception):
    """Only fixed diagnostic codes are displayed; SDK exception text may contain PII."""


def service_state():
    """Query SCM read-only. Missing service/access/platform is an error, never 'stopped'."""
    if sys.platform != 'win32':
        raise CaptureError('service_check_unavailable')
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    api.OpenSCManagerW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    api.OpenSCManagerW.restype = wintypes.HANDLE
    api.OpenServiceW.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD)
    api.OpenServiceW.restype = wintypes.HANDLE
    api.QueryServiceStatusEx.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    api.QueryServiceStatusEx.restype = wintypes.BOOL
    api.CloseServiceHandle.argtypes = (wintypes.HANDLE,)
    api.CloseServiceHandle.restype = wintypes.BOOL
    manager, service = None, None
    try:
        manager = api.OpenSCManagerW(None, None, 1)  # SC_MANAGER_CONNECT only.
        if not manager:
            raise CaptureError('service_check_unavailable')
        service = api.OpenServiceW(manager, SERVICE_NAME, 4)  # SERVICE_QUERY_STATUS only.
        if not service:
            raise CaptureError('service_check_unavailable')
        status = (wintypes.DWORD * 9)()  # SERVICE_STATUS_PROCESS: nine DWORD fields.
        needed = wintypes.DWORD()
        if not api.QueryServiceStatusEx(service, 0, status, ctypes.sizeof(status), ctypes.byref(needed)):
            raise CaptureError('service_check_unavailable')
        return {1: 'STOPPED', 2: 'START_PENDING', 3: 'STOP_PENDING', 4: 'RUNNING',
                5: 'CONTINUE_PENDING', 6: 'PAUSE_PENDING', 7: 'PAUSED'}.get(status[1], 'UNKNOWN')
    finally:
        if service:
            api.CloseServiceHandle(service)
        if manager:
            api.CloseServiceHandle(manager)


def require_stopped(check):
    if check() != 'STOPPED':
        raise CaptureError('service_not_confirmed_stopped')


def check_directory_acl(directory):
    """Require protected Windows ACLs without broad group grants; never change them."""
    if sys.platform != 'win32':
        raise CaptureError('output_acl_check_unavailable')
    script = r"""
$ErrorActionPreference='Stop'
$acl=Get-Acl -LiteralPath $env:BIOMETRIC_CAPTURE_OUTPUT_DIR
$broad=@('S-1-1-0','S-1-5-11','S-1-5-32-545','S-1-5-32-546','S-1-5-7','S-1-5-4')
$unsafe=-not $acl.AreAccessRulesProtected
foreach ($rule in $acl.Access) {
  $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
  if ($rule.AccessControlType -eq 'Allow' -and (($broad -contains $sid) -or $sid.EndsWith('-513') -or $sid.EndsWith('-514'))) { $unsafe=$true }
}
@{safe=(-not $unsafe)} | ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            env=dict(os.environ, BIOMETRIC_CAPTURE_OUTPUT_DIR=str(directory)),
            capture_output=True, text=True, timeout=15, check=False)
        if result.returncode != 0 or json.loads(result.stdout).get('safe') is not True:
            raise CaptureError('output_acl_not_private')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise CaptureError('output_acl_check_unavailable') from None


def preflight_output(path, live=False, acl_check=check_directory_acl):
    directory = Path(path)
    if not directory.is_absolute() or not directory.is_dir():
        raise CaptureError('output_must_be_existing_absolute_directory')
    directory = directory.resolve()
    repository = Path(__file__).resolve().parents[1]
    protected = (repository, *(root.resolve() for root in PROTECTED_ROOTS))
    if any(directory == root or root in directory.parents for root in protected) or directory == Path(directory.anchor):
        raise CaptureError('output_must_be_dedicated_outside_checkout')
    if str(directory).startswith('\\\\'):
        raise CaptureError('output_must_be_local')
    # Even a custom production state directory must not be reused as an evidence folder.
    markers = ('status.json', '*_last_fetch_dump.json', '*.sqlite*', '*.db', 'error.log', 'logs.log', 'attendance*.log')
    if any(next(directory.glob(pattern), None) is not None for pattern in markers):
        raise CaptureError('output_contains_production_state_or_logs')
    acl_check(directory)
    if not os.access(directory, os.R_OK | os.W_OK):
        raise CaptureError('output_not_writable')
    if shutil.disk_usage(directory).free < MIN_FREE_BYTES:
        raise CaptureError('insufficient_disk_space')
    if live:
        # Verify create/write/flush/remove before opening a terminal. Probe contains no PII.
        fd, name = tempfile.mkstemp(prefix='.capture-probe-', suffix='.part', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(b'capture output preflight\n')
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            Path(name).unlink()
    return directory


def finalize_no_replace(temporary, destination):
    """Same-directory publication; never use replace-existing or a cross-volume copy."""
    if os.name == 'nt':
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.MoveFileExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        api.MoveFileExW.restype = wintypes.BOOL
        if not api.MoveFileExW(str(temporary), str(destination), 8):  # WRITE_THROUGH, no REPLACE.
            raise OSError('capture_finalization_failed')
    else:
        os.link(temporary, destination)  # Atomic failure if the target already exists.
        Path(temporary).unlink()
        fd = os.open(str(Path(destination).parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
