"""Pinned Windows service wrapper with synthetic adapters. Staging identity only.

No installation commands are implemented here. The administrator session runner
alone changes the existing staging ImagePath and restores it after a clean stop.
"""
import sys
import os
import json
import time

BOOTSTRAP_STREAM = None
BOOTSTRAP_ID = None


def bootstrap_event(stage, error=None):
    """Fixed fields only: never serialize exception text, arguments or locals."""
    if BOOTSTRAP_STREAM is None:
        return
    value = dict(stage=stage, pid=os.getpid(), time_ns=time.time_ns())
    if error is not None:
        value['category'] = type(error).__name__
        for name in ('errno', 'winerror'):
            number = getattr(error, name, None)
            if type(number) is int:
                value[name] = number
        # Line numbers locate failures without copying source/configuration text.
        frames, tb = [], error.__traceback__
        while tb is not None:
            frames.append(dict(file=os.path.basename(tb.tb_frame.f_code.co_filename), line=tb.tb_lineno))
            tb = tb.tb_next
        value['frames'] = frames[-8:]
    BOOTSTRAP_STREAM.write(json.dumps(value, sort_keys=True) + '\n')
    BOOTSTRAP_STREAM.flush()
    os.fsync(BOOTSTRAP_STREAM.fileno())


if __name__ == '__main__' and '--bootstrap-idle' in sys.argv:
    # The administrator creates a unique, reviewed adapter copy in the existing
    # frozen code directory. Only the SERVICE process creates this log in data.
    leaf = os.path.basename(__file__)
    prefix, suffix = 'phase1_staging_scm_bootstrap_', '.py'
    identifier = leaf[len(prefix):-len(suffix)]
    if (not leaf.startswith(prefix) or not leaf.endswith(suffix) or len(identifier) != 32
            or any(c not in '0123456789abcdef' for c in identifier)):
        raise SystemExit(1)
    BOOTSTRAP_ID = identifier
    log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'data', 'bootstrap-' + identifier + '.jsonl')
    BOOTSTRAP_STREAM = open(log, 'x', encoding='ascii')
    bootstrap_event('process_entry_python_initialized')

try:
    import argparse
    import datetime as dt
    import hashlib
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import importlib.util
    import logging
    from pathlib import Path
    import re
    import shutil
    import socket
    import socketserver
    import ssl
    import threading
    import types
    import urllib.request
except Exception as error:
    bootstrap_event('stdlib_initialization_exception', error)
    raise SystemExit(1) from None

bootstrap_event('stdlib_initialization_complete')

NAME = 'FPFPhase1Staging'
CURRENT_PHASE = 'arguments'
PHASES = ('idle', 'inflight', 'recover')
HELPER_HASH = '91d5229af15c26d2faa85058df1abcf218878a666ae3fcb7e712f439d225326b'
WRAPPER_HASHES = {
    'erpnext_sync_win.py': '121a6cf92b53cbd45e24b3f0411fb97ae2e669dc168638a094574e0cfe6d41ad',
    'SMWinservice.py': 'c0f20a35cec74705d4875da51ac5e09597a376433c056c07e61055c022d3c006'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fresh_bootstrap_settings(settings, now=None):
    # The retained fixture's cutover expires between operator attempts. A new
    # synthetic ledger needs its OWN future boundary; never rewrite evidence.
    boundary = (now or dt.datetime.now()).replace(microsecond=0) + dt.timedelta(minutes=2)
    return dict(settings, cutover=str(boundary))


def inspect_mutex(expected=None):
    """Staging observer only; expected=None never waits or acquires ownership."""
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = api.CreateMutexW(None, False, r'Global\ERPNextBiometricPushService-DurableWorker')
    native_error = ctypes.get_last_error()  # Immediately: no other Windows call.
    result = dict(operation='CreateMutexW', win32_error=native_error, handle_opened=bool(handle))
    if not handle:
        return dict(result, status='FAIL', phase='inspection', category='StartupError', code='worker_lock_unavailable')
    try:
        if expected is None:
            # If absent, CreateMutexW creates an UNOWNED temporary object. Close
            # it below; this is not proof of the earlier service-held condition.
            return dict(result, status='INFO', existed=native_error == 183,
                        scope='mutex_handle_only_no_wait_no_ledger')
        api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        api.ReleaseMutex.restype = wintypes.BOOL
        state = api.WaitForSingleObject(handle, 0)
        wait_error = ctypes.get_last_error()
        if state not in (0, 0x80, 0x102):
            return dict(result, status='FAIL', operation='WaitForSingleObject', win32_error=wait_error)
        locked = state == 0x102
        if not locked and not api.ReleaseMutex(handle):
            release_error = ctypes.get_last_error()
            return dict(result, status='FAIL', operation='ReleaseMutex', win32_error=release_error)
        return dict(result, status='PASS' if locked == (expected == 'locked') else 'FAIL', locked=locked)
    finally:
        api.CloseHandle(handle)


def publish(path, value):
    # Repeated idle ticks may observe the same state; never rewrite evidence.
    if path.exists():
        return
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)  # Windows rename is atomic and refuses an existing destination.


def shared_helpers(code):
    path = code / 'phase1_staging_engine_crash.py'
    if digest(path) != HELPER_HASH:
        raise ValueError('helper_hash_mismatch')
    spec = importlib.util.spec_from_file_location('scm_shared_helpers', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.helper()


def context(code, settings=True):
    code = Path(code).resolve(strict=True)
    attempt = code.parent
    if code.name != 'code' or not attempt.name.startswith('scm-'):
        raise ValueError('invalid_staging_bundle')
    for path in (code, *code.parents):
        if getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('reparse_path')
    shared, base = shared_helpers(code)
    hashes = dict(base.HASHES, **WRAPPER_HASHES)
    for name, expected in hashes.items():
        if digest(code / name) != expected:
            raise ValueError('pinned_source_mismatch')
    manifest = json.loads((code / 'bundle.json').read_text())
    if set(manifest) != {'sha256'} or not manifest['sha256']:
        raise ValueError('invalid_bundle_manifest')
    for name, expected in manifest['sha256'].items():
        if Path(name).name != name or digest(code / name) != expected:
            raise ValueError('bundle_hash_mismatch')
    sys.path.insert(0, str(code))
    import durable_sync
    import durable_startup
    from sync_ledger import Ledger
    cfg = json.loads((code / 'settings.json').read_text()) if settings else None
    if cfg is not None:
        if set(cfg) != {'port', 'cutover', 'service_sid'} or type(cfg['port']) is not int or not 1 <= cfg['port'] <= 65535:
            raise ValueError('invalid_synthetic_settings')
        if not re.fullmatch(r'S-1-5-80-(?:[0-9]+-){4}[0-9]+', cfg['service_sid']):
            raise ValueError('invalid_staging_sid')
        durable_startup.cutover_time(cfg['cutover'])
    return types.SimpleNamespace(code=code, attempt=attempt, data=attempt / 'data',
        ledger=attempt / 'data' / 'synthetic.sqlite3', settings=cfg, shared=shared,
        base=base, sync=durable_sync, startup=durable_startup, Ledger=Ledger)


def install_guard(ctx, provision=False):
    class Policy(ctx.base.Policy):
        def audit(self, event, args):
            if BOOTSTRAP_ID and event.startswith('socket.'):
                # Idle diagnostics never need a network operation, even loopback.
                self.event = event
                self.deny()
            if provision and event == 'subprocess.Popen':
                # Offline administrator provisioning must allow this one exact
                # staging SID in the unchanged ACL verifier's environment.
                executable, argv, cwd, env = args
                if isinstance(env, dict) and env.get('FPF_LEDGER_SERVICE_SID') == ctx.settings['service_sid']:
                    reviewed = dict(env, FPF_LEDGER_SERVICE_SID='')
                    return super().audit(event, (executable, argv, cwd, reviewed))
            return super().audit(event, args)
    policy = Policy(ctx.attempt, ctx.code / 'localhost-cert.pem', ctx.code / 'localhost-cert.pem',
                    ctx.ledger, ctx.shared.acl_script(ctx.startup))
    policy.port = ctx.settings['port']
    ctx.shared.activate_guard(policy)
    return policy


def engine_adapter(ctx):
    origin = 'https://127.0.0.1:' + str(ctx.settings['port'])
    tls = ssl.create_default_context(cafile=str(ctx.code / 'localhost-cert.pem'))
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), ctx.base.NoRedirect(),
                                        urllib.request.HTTPSHandler(context=tls))
    config = types.SimpleNamespace(DURABLE_SYNC_ENABLED=True, DURABLE_DB_PATH=str(ctx.ledger),
        ERPNEXT_URL=origin, ERPNEXT_VERSION=15, ERPNEXT_API_KEY='SYNTHETIC', ERPNEXT_API_SECRET='SYNTHETIC',
        ERPNEXT_REQUEST_TIMEOUT=3, IMPORT_START_DATE=None, PULL_FREQUENCY=60,
        shift_type_device_mapping=[], devices=[])
    logger = logging.getLogger('synthetic_scm')
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    stamp = dt.datetime.fromisoformat(ctx.settings['cutover']) + dt.timedelta(seconds=1)
    engine = types.SimpleNamespace(config=config, requests=types.SimpleNamespace(request=ctx.base.transport(opener, origin)),
        info_logger=logger, error_logger=logger, status=types.SimpleNamespace(usable=True),
        device_punch_values_IN=[0, 4], device_punch_values_OUT=[1, 5],
        validate_unique_device_ids=lambda devices: None, normalize_device_config=dict,
        get_dump_file_name_and_directory=lambda _: str(ctx.data / 'unused-synthetic-dump'))
    state = dict(phase=None, sid=None)

    def records(*args, **kwargs):
        if state['phase'] != 'inflight':
            raise RuntimeError('unexpected_synthetic_poll')
        yield dict(user_id='STAGE-SYNTHETIC-001', timestamp=stamp, punch=0, status=1, uid=1)
        # This point is inside real Ledger.ingest after INSERT, before COMMIT.
        publish(ctx.data / 'inflight-ready.json', dict(phase='inflight', pid=os.getpid(), sid=state['sid']))
        threading.Event().wait(5)  # Bounded capture, deliberately finishes after STOP_PENDING.
    engine.get_all_attendance_from_device = records

    def tick(stop_requested=lambda: False):
        phase = state['phase']
        try:
            ctx.sync.run(engine, stop_requested=stop_requested)
            with ctx.Ledger(ctx.ledger, readonly=True) as db:
                db.verify_integrity()
                counts = db.diagnostics()['states']
            value = dict(phase=phase, pid=os.getpid(), sid=state['sid'], states=counts,
                         stop_requested=bool(stop_requested()), integrity='ok')
            prefix = ('bootstrap-' + BOOTSTRAP_ID + '-') if BOOTSTRAP_ID else ''
            publish(ctx.data / (prefix + phase + '-complete.json'), value)
            if phase != 'inflight':
                publish(ctx.data / (prefix + phase + '-ready.json'), value)
                bootstrap_event('idle_engine_cycle_complete')
        except Exception as error:
            bootstrap_event('engine_cycle_exception', error)
            prefix = ('bootstrap-' + BOOTSTRAP_ID + '-') if BOOTSTRAP_ID else ''
            publish(ctx.data / (prefix + phase + '-failure.json'), dict(category=type(error).__name__))
            raise RuntimeError('synthetic_service_cycle_failed') from None
    return engine, state, tick


def build_service(ctx, token_sid=None):
    # Native dependencies are loaded before the guard. Only the pinned wrapper
    # and base are imported; erpnext_sync is an in-memory synthetic module.
    import servicemanager
    import win32api
    import win32con
    import win32security
    engine, state, tick = engine_adapter(ctx)
    synthetic = types.ModuleType('erpnext_sync')
    synthetic.config, synthetic.main = engine.config, tick
    sys.modules['erpnext_sync'] = synthetic
    for name in ('SMWinservice', 'erpnext_sync_win'):
        spec = importlib.util.spec_from_file_location(name, ctx.code / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    wrapper = sys.modules['erpnext_sync_win']

    def actual_sid():
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()

    class StagingService(wrapper.PythonCornerExample):
        # No changes to start/stop/main/SvcStop/SvcDoRun or mutex handling.
        _svc_name_ = NAME
        _svc_display_name_ = 'FPF Phase1 Staging'

        def __init__(self, args):
            try:
                bootstrap_event('service_constructor_entered')
                if (len(args) != 2 or args[0] != NAME or args[1] not in PHASES
                        or (BOOTSTRAP_ID and args[1] != 'idle')):
                    raise ValueError('staging_phase_required')
                state['phase'], state['sid'] = args[1], (token_sid or actual_sid)()
                if state['sid'] != ctx.settings['service_sid']:
                    raise ValueError('wrong_service_token')
                bootstrap_event('service_token_verified')
                if state['phase'] != 'idle':
                    engine.config.devices = [dict(device_id='STAGE-DEVICE', ip='SYNTHETIC-NO-SOCKET',
                        port=4370, password=0, punch_direction=None, clear_from_device_on_fetch=False)]
                super().__init__(args)
                bootstrap_event('service_control_handler_registered')
                if BOOTSTRAP_ID:
                    # A NEW synthetic ledger is owned by this actual service
                    # token. No ACL changes or writes to prior evidence occur.
                    bootstrap_event('synthetic_provision_begin')
                    ctx.startup.provision(ctx.ledger, engine.config.ERPNEXT_URL, 15,
                        ctx.settings['cutover'], 'SYNTHETIC IDLE BOOTSTRAP ONLY')
                    bootstrap_event('synthetic_provision_complete')
            except Exception as error:
                bootstrap_event('service_constructor_exception', error)
                raise

        def SvcRun(self):
            # Preserve pywin32's lifecycle/status implementation; only observe it.
            try:
                bootstrap_event('service_run_entered')
                super().SvcRun()
                bootstrap_event('service_run_returned')
            except Exception as error:
                bootstrap_event('service_run_exception', error)
                raise
    return StagingService, servicemanager


def mock_server(ctx, stage):
    # Only the foreground administrator mock reads the original private key.
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    cert, key = stage / 'localhost-cert.pem', stage / 'localhost-key.pem'
    if digest(cert) != digest(ctx.code / 'localhost-cert.pem'):
        raise ValueError('certificate_mismatch')
    tls.load_cert_chain(cert, key)
    accepted = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            size = int(self.headers.get('Content-Length', 0))
            value = json.loads(self.rfile.read(size)) if 0 < size <= 4096 else {}
            settings = json.loads((ctx.code / 'settings.json').read_text())
            expected = dict(employee_field_value='STAGE-SYNTHETIC-001',
                timestamp=str(dt.datetime.fromisoformat(settings['cutover']) + dt.timedelta(seconds=1)),
                device_id='STAGE-DEVICE', log_type=None, latitude=None, longitude=None)
            if self.path != ctx.base.METHOD or value != expected:
                raise ValueError('unexpected_mock_request')
            accepted.append(value)
            raw = b'{"message":{"name":"MOCK-SCM-001"}}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    errors = []
    class Server(HTTPServer):
        def server_bind(self):
            # HTTPServer's default performs reverse DNS even for loopback.
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(3)
            try:
                return tls.wrap_socket(connection, server_side=True), address
            except BaseException:
                connection.close()
                raise
        def handle_error(self, *args):
            errors.append('mock_handler_failure')  # No traceback, URLs or request contents.

    def guard(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr',
                     'socket.sendto', 'socket.sendmsg', 'os.system'} or event.startswith(('subprocess.', 'ctypes.')):
            raise RuntimeError('mock_outbound_denied')
        if event == 'socket.__new__' and (args[1] != socket.AF_INET or args[2] != socket.SOCK_STREAM):
            raise RuntimeError('mock_non_tcp_denied')
        if event == 'socket.bind' and args[1] != ('127.0.0.1', 0):
            raise RuntimeError('mock_bind_denied')
        if event == 'open' and not isinstance(args[0], int) and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            if Path(args[0]).resolve() not in {ctx.data / name for name in (
                    'mock-ready.json', 'mock-result.json', 'mock-ready.json.tmp', 'mock-result.json.tmp')}:
                raise RuntimeError('mock_write_denied')
        if event == 'os.rename' and tuple(Path(p).resolve() for p in args[:2]) not in [
                (ctx.data / (name + '.tmp'), ctx.data / name) for name in ('mock-ready.json', 'mock-result.json')]:
            raise RuntimeError('mock_rename_denied')
    sys.addaudithook(guard)
    server = Server(('127.0.0.1', 0), Handler)
    server.timeout = 1
    publish(ctx.data / 'mock-ready.json', dict(port=server.server_port))
    deadline = time.monotonic() + 120
    try:
        while not accepted and not errors and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    passed = len(accepted) == 1 and not errors
    publish(ctx.data / 'mock-result.json', dict(status='PASS' if passed else 'FAIL', mock_records=len(accepted), stopped=True))
    return 0 if passed else 1


def package_runtime(destination):
    """Private copy of installed stdlib and only required pywin32 files; no pip."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError('runtime_destination_already_exists')
    source = Path(sys.base_prefix)
    site = source / 'Lib' / 'site-packages'
    names = ('_win32sysloader', 'servicemanager', 'win32api', 'win32event', 'win32service', 'win32security')
    inputs = [source / 'python.exe', source / ('python' + str(sys.version_info.major) + str(sys.version_info.minor) + '.dll')]
    inputs += list(source.glob('vcruntime*.dll'))
    inputs += [site / 'win32' / (name + '.pyd') for name in names]
    inputs += [site / 'win32' / 'lib' / name for name in ('win32serviceutil.py', 'win32con.py', 'winerror.py', 'pywintypes.py')]
    inputs += [site / 'pywin32_system32' / ('pywintypes' + str(sys.version_info.major) + str(sys.version_info.minor) + '.dll')]
    if not all(path.is_file() for path in inputs):
        raise ValueError('installed_runtime_dependencies_missing')
    for path in inputs:
        for item in (path, *path.parents):
            if getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('runtime_reparse_refused')
            if item == source:
                break
    for tree in (source / 'Lib', source / 'DLLs'):
        if not tree.is_dir():
            raise ValueError('standard_runtime_missing')
        for folder, dirs, files in os.walk(tree):
            dirs[:] = [d for d in dirs if d not in {'site-packages', '__pycache__', 'test', 'tests'}]
            for path in [Path(folder)] + [Path(folder) / name for name in files + dirs]:
                if getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                    raise ValueError('runtime_reparse_refused')
    destination.mkdir()
    for tree in ('Lib', 'DLLs'):
        shutil.copytree(source / tree, destination / tree,
            ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'tests'))
    for path in inputs:
        shutil.copyfile(path, destination / path.name)
        if digest(path) != digest(destination / path.name):
            raise ValueError('runtime_transfer_mismatch')
    (destination / ('python' + str(sys.version_info.major) + str(sys.version_info.minor) + '._pth')).write_text('.\nLib\nDLLs\n', encoding='ascii')
    files = {str(path.relative_to(destination)): digest(path) for path in destination.rglob('*') if path.is_file()}
    publish(destination / 'runtime-manifest.json', dict(source=str(source), python=sys.version, sha256=files))


def validate_runtime_destination(destination):
    target = Path(destination).absolute()
    stage = target.parents[2]
    if ((target.name != 'runtime' and not re.fullmatch(r'runtime-[0-9a-f]{32}', target.name))
            or target.parent.name != 'code' or target.parents[1].name != 'service-boundary'
            or stage.parent != Path(os.environ['LOCALAPPDATA']).resolve()
            or not re.fullmatch(r'FPF-Phase1-Staging-[0-9a-f]{32}', stage.name)):
        raise ValueError('reserved_staging_runtime_required')
    for path in (target.parent, *target.parent.parents):
        if getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('reparse_runtime_destination')
    return target


def main():
    global CURRENT_PHASE
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--package-runtime', type=Path)
    modes.add_argument('--mock-stage', type=Path)
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--provision', action='store_true')
    modes.add_argument('--inspect', choices=('locked', 'unlocked'))
    modes.add_argument('--bootstrap-idle', action='store_true')
    modes.add_argument('--mutex-error-only', action='store_true')
    args = parser.parse_args()
    try:
        if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
            raise ValueError('isolated_flags_required')
        if args.mutex_error_only:
            # Standalone numeric observation: no bundle/configuration/ledger load.
            result = inspect_mutex()
            print(json.dumps(result))
            return 1 if result['status'] == 'FAIL' else 0
        if args.package_runtime:
            package_runtime(validate_runtime_destination(args.package_runtime))
            return 0
        CURRENT_PHASE = 'bundle_validation'
        bootstrap_event('bundle_validation_begin')
        ctx = context(Path(__file__).resolve().parent, settings=not bool(args.mock_stage))
        bootstrap_event('bundle_validation_complete')
        if args.bootstrap_idle:
            if BOOTSTRAP_ID is None:
                raise ValueError('unique_bootstrap_adapter_required')
            ctx.settings = fresh_bootstrap_settings(ctx.settings)
            ctx.ledger = ctx.data / ('bootstrap-' + BOOTSTRAP_ID + '.sqlite3')
            if ctx.ledger.exists() or ctx.startup.activation_path(ctx.ledger).exists():
                raise ValueError('bootstrap_ledger_exists_preserve_evidence')
        if args.mock_stage:
            CURRENT_PHASE = 'mock'
            if args.mock_stage.resolve(strict=True) != ctx.attempt.parent:
                raise ValueError('mock_stage_mismatch')
            return mock_server(ctx, args.mock_stage.resolve(strict=True))
        if args.preflight:
            CURRENT_PHASE = 'native_import_preflight'
            service, _ = build_service(ctx)  # Class load only; no instance or SCM API.
            if service._svc_name_ != NAME:
                raise ValueError('wrong_service_class')
            print('{"status":"PASS","scope":"imports_only_no_SCM"}')
            return 0
        if args.provision:
            CURRENT_PHASE = 'provision'
            install_guard(ctx, provision=True)
            ctx.startup.provision(ctx.ledger, 'https://127.0.0.1:' + str(ctx.settings['port']), 15,
                ctx.settings['cutover'], 'SYNTHETIC SCM STAGING ONLY',
                storage_check=lambda path: ctx.startup.private_storage(path, ctx.settings['service_sid']))
            return 0
        if args.inspect:
            CURRENT_PHASE = 'inspection'
            install_guard(ctx)
            result = inspect_mutex(args.inspect)
            if result['status'] != 'PASS':
                print(json.dumps(result))
                return 1
            with ctx.Ledger(ctx.ledger, readonly=True) as db:
                db.verify_integrity()
                print(json.dumps(dict(result, states=db.diagnostics()['states'])))
            return 0
        CURRENT_PHASE = 'SCM_dispatch'
        bootstrap_event('native_service_load_begin')
        service, manager = build_service(ctx)
        bootstrap_event('native_service_load_complete')
        install_guard(ctx)
        bootstrap_event('isolation_guard_installed')
        bootstrap_event('dispatcher_initialize_begin')
        manager.Initialize()
        bootstrap_event('dispatcher_initialize_complete')
        manager.PrepareToHostSingle(service)
        bootstrap_event('dispatcher_registered')
        bootstrap_event('dispatcher_connect_begin')
        manager.StartServiceCtrlDispatcher()
        bootstrap_event('dispatcher_returned')
        return 0
    except Exception as error:
        bootstrap_event('startup_exception', error)
        if BOOTSTRAP_STREAM is None:
            print(json.dumps(dict(status='FAIL', phase=CURRENT_PHASE, category=type(error).__name__, details_suppressed=True)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
