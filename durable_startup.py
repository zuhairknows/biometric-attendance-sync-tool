"""Local activation and worker guards. Never import production configuration here."""
from contextlib import contextmanager
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from sync_ledger import Ledger


class StartupError(RuntimeError):
    """Only fixed categories; paths and configuration values must not enter logs."""


_local_locks = threading.local()


def destination_key(url, version=14):
    if type(version) is not int or not 12 <= version <= 99:
        raise StartupError('destination_version_invalid')
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise StartupError('destination_url_invalid') from None
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/') or port == 0
            or not isinstance(url, str) or any(c.isspace() for c in url)):
        raise StartupError('destination_url_invalid')
    app = 'hrms' if version > 13 else 'erpnext'
    identity = parsed.scheme + '://' + parsed.netloc.lower() + '/' + app
    return hashlib.sha256(identity.encode()).hexdigest()


def cutover_time(value):
    try:
        stamp = dt.datetime.fromisoformat(value)
        if stamp.tzinfo is not None or stamp.microsecond or value != stamp.isoformat(' '):
            raise ValueError
        return stamp
    except (TypeError, ValueError):
        raise StartupError('cutover_requires_local_second_precision') from None


def private_storage(path, service_sid=None):
    """Verify a local private directory and all entries; never change permissions."""
    path = Path(path)
    if (not path.is_absolute() or str(path).startswith('\\\\') or not path.parent.is_dir()
            or path.parent.resolve() == Path(path.anchor)):
        raise StartupError('ledger_requires_existing_local_directory')
    root = Path(__file__).resolve().parent
    if root == path.resolve() or root in path.resolve().parents:
        raise StartupError('ledger_inside_checkout')
    for key in ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer'):
        cloud = os.environ.get(key)
        if cloud and (Path(cloud).resolve() == path.resolve() or Path(cloud).resolve() in path.resolve().parents):
            raise StartupError('ledger_in_cloud_directory')
    if sys.platform != 'win32':
        raise StartupError('private_storage_check_requires_windows')
    if service_sid is not None and not re.fullmatch(r'S-1-[0-9]+(?:-[0-9]+)+', service_sid):
        raise StartupError('invalid_service_sid')
    script = r'''
$ErrorActionPreference='Stop'
$p=$env:FPF_LEDGER_DIRECTORY
$current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$allowed=@($current,'S-1-5-18','S-1-5-32-544')
if ($env:FPF_LEDGER_SERVICE_SID) { $allowed+=@($env:FPF_LEDGER_SERVICE_SID) }
$drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($p))
$safe=($drive.DriveType -eq 'Fixed' -and $drive.DriveFormat -eq 'NTFS')
$items=@(Get-Item -LiteralPath $p -Force)+@(Get-ChildItem -LiteralPath $p -Force)
foreach ($item in $items) {
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { $safe=$false }
  $acl=Get-Acl -LiteralPath $item.FullName
  if ($item.FullName -eq $p -and -not $acl.AreAccessRulesProtected) { $safe=$false }
  $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
  if ($allowed -notcontains $owner) { $safe=$false }
  foreach ($rule in $acl.Access) {
    $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($rule.AccessControlType -eq 'Allow' -and $allowed -notcontains $sid) { $safe=$false }
  }
}
@{safe=$safe} | ConvertTo-Json -Compress
'''
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            env=dict(os.environ, FPF_LEDGER_DIRECTORY=str(path.parent.resolve()),
                     FPF_LEDGER_SERVICE_SID=service_sid or ''),
            capture_output=True, text=True, timeout=15, check=False)
        if result.returncode or json.loads(result.stdout).get('safe') is not True:
            raise StartupError('ledger_storage_not_private')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise StartupError('ledger_storage_verification_failed') from None


@contextmanager
def worker_lock(path):
    """Global Windows mutex prevents a second durable worker, even at another DB path."""
    if sys.platform == 'win32':
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        api.CreateMutexW.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = api.CreateMutexW(None, False, r'Global\ERPNextBiometricPushService-DurableWorker')
        if not handle:
            raise StartupError('worker_lock_unavailable')
        acquired = api.WaitForSingleObject(handle, 0) in (0, 0x80)
        try:
            if not acquired:
                raise StartupError('another_durable_worker_running')
            yield
        finally:
            if acquired:
                api.ReleaseMutex(handle)
            api.CloseHandle(handle)
    else:
        # Portable synthetic/staging support; Windows production uses the global mutex above.
        import fcntl
        active = getattr(_local_locks, 'active', None)
        if active is None:
            active = _local_locks.active = set()
        key = str(Path(path).resolve())
        if key in active:
            yield  # The same worker may enter a cycle while holding its lifetime lock.
            return
        with Path(str(path) + '.worker.lock').open('a+b') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise StartupError('another_durable_worker_running') from None
            try:
                active.add(key)
                yield
            finally:
                active.remove(key)
                fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def worker_session(config):
    """Keep the worker exclusive during idle polling waits as well as active cycles."""
    if getattr(config, 'DURABLE_SYNC_ENABLED', False) is True:
        from durable_sync import database_path
        with worker_lock(database_path(config)):
            yield
    else:
        yield


def activation_path(path):
    return Path(str(path) + '.activation.json')


def provision(path, url, version, boundary, reference, *, storage_check=private_storage, now=None):
    """Explicit offline provisioning only; an existing populated ledger is never adopted."""
    path = Path(path)
    storage_check(path)
    stamp = cutover_time(boundary)
    if stamp <= (now or dt.datetime.now()):
        raise StartupError('new_activation_requires_future_cutover')
    key = destination_key(url, version)
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 200:
        raise StartupError('cutover_review_reference_required')
    with worker_lock(path):
        marker = activation_path(path)
        if marker.exists():
            raise StartupError('activation_already_exists')
        def require_unbound_empty(ledger):
            # Never silently rebind a lost marker, migrated backlog, or previous deployment.
            if ledger.db.execute('PRAGMA application_id').fetchone()[0] != 0:
                raise StartupError('ledger_already_bound_marker_missing')
            for table in ('punches', 'device_state', 'delivery_attempts', 'legacy_imports', 'shift_state'):
                if ledger.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]:
                    raise StartupError('nonempty_ledger_requires_separate_migration_review')
            if any(ledger.db.execute('SELECT cooldown_until,prefer_retry,prefer_recent FROM destination_state').fetchone()):
                raise StartupError('previously_used_ledger_requires_separate_migration_review')
        if path.exists():
            # Refuse an existing populated database before any write-mode SQLite PRAGMA.
            with Ledger(path, readonly=True) as existing:
                require_unbound_empty(existing)
        with Ledger(path) as ledger:
            require_unbound_empty(ledger)
            cookie = secrets.randbelow(2**31 - 1) + 1
            ledger.db.execute('PRAGMA application_id=' + str(cookie))
            ledger.db.commit()
            value = dict(schema='durable-activation-v1', ledger_cookie=cookie,
                destination_sha256=key, cutover_time=boundary, review_reference=reference,
                provisioned_at=dt.datetime.now(dt.timezone.utc).isoformat())
            # A crash between binding and marker publication fails closed on the next attempt.
            with marker.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
    return value


def validate_activation(path, config):
    """Must run before opening Ledger read/write: missing SQLite must never be created."""
    path = Path(path)
    marker = activation_path(path)
    if not path.is_file() or not marker.is_file() or marker.stat().st_size > 16384:
        raise StartupError('ledger_or_activation_missing_provisioning_required')
    try:
        value = json.loads(marker.read_text(encoding='utf-8'))
        if (value['schema'] != 'durable-activation-v1' or type(value['ledger_cookie']) is not int
                or not 0 < value['ledger_cookie'] < 2**31
                or value['destination_sha256'] != destination_key(config.ERPNEXT_URL, getattr(config, 'ERPNEXT_VERSION', 14))):
            raise StartupError('activation_destination_or_schema_mismatch')
        cutover_time(value['cutover_time'])
        with Ledger(path, readonly=True) as ledger:
            if ledger.db.execute('PRAGMA application_id').fetchone()[0] != value['ledger_cookie']:
                raise StartupError('ledger_identity_mismatch')
        return value
    except (KeyError, TypeError, ValueError):
        raise StartupError('activation_invalid') from None
