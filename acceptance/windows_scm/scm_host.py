"""Isolated SCM operator harness. Never imported by the application.

All writable paths and all transport/device inputs are fixed synthetic values.
Administrative operations are implemented only in the staged PowerShell driver.
"""
import argparse
import ctypes
import datetime as dt
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib
import json
import logging
import os
from pathlib import Path
import socket
import socketserver
import sqlite3
import ssl
import struct
import sys
import threading
import time
import types

CANDIDATE = '4f20179f5fb10f84a6f86a6ff720f9c2876efc20'
RUN = 'A4F20179C01'
ROOT = Path('C:/FPFPhase2AclAcceptance-' + RUN)
NAME = 'FPFPhase2AclAcceptance_' + RUN
PORT = 18879
ORIGIN = 'https://127.0.0.1:' + str(PORT)
METHOD = '/api/method/hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field'
MUTEX = r'Global\ERPNextBiometricPushService-DurableWorker'
DB = ROOT / 'data' / 'synthetic.sqlite3'
MARKER = Path(str(DB) + '.activation.json')
CONFIG = ROOT / 'code' / 'local_config.py'
SYNTHETIC = 'SYNTHETIC_SCM_ONLY'
SYSTEM = 'S-1-5-18'


def require(value, code):
    if not value:
        raise RuntimeError(code)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_version():
    require(sys.version_info[:3] == (3, 11, 9) and struct.calcsize('P') == 8,
            'Python_3_11_9_x64_required')



def safety():
    # -I deliberately excludes script-directory imports; load only the installed protected copy.
    require(Path(__file__).absolute() == ROOT / 'tools' / 'scm_host.py', 'installed_harness_path_required')
    sys.path.insert(0, str(ROOT / 'tools'))
    return importlib.import_module('harness_safety')


def mutex_absent():
    """Open only: do not create, acquire or interfere with another writer."""
    from ctypes import wintypes as w
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenMutexW.argtypes, api.OpenMutexW.restype = [w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    ctypes.set_last_error(0)
    handle = api.OpenMutexW(0x100000, False, MUTEX)
    error = ctypes.get_last_error()
    if handle:
        api.CloseHandle(handle)
    require(not handle and error == 2, 'existing_or_inaccessible_global_writer_mutex')


def installed(*, monitor=False):
    require(Path(__file__).absolute() == ROOT / 'tools' / 'scm_host.py', 'installed_harness_path_required')
    require(os.environ.get('COMPUTERNAME', '').upper() == 'ZUHAIR-PC', 'ZUHAIR_PC_only')
    def checked_walk(path):
        require(not path.is_symlink() and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400,
                'reparse_point_refused')
        yield path
        if path.is_dir():
            for child in path.iterdir():
                yield from checked_walk(child)
    paths = (ROOT, ROOT / 'code', ROOT / 'tools', ROOT / 'runtime', DB.parent, DB) if monitor else checked_walk(ROOT)
    for path in paths:
        require(not path.is_symlink() and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400,
                'reparse_point_refused')
    receipt = json.loads((ROOT / 'receipt.json').read_text(encoding='utf-8-sig'))
    require(receipt['schema'] == 'fpf-phase2-scm-v1' and receipt['candidate'] == CANDIDATE
            and receipt['service'] == NAME and receipt['root'] == str(ROOT), 'receipt_mismatch')
    for relative, expected in receipt['source_sha256'].items():
        path = ROOT / relative
        require(not Path(relative).is_absolute() and ':' not in relative, 'source_path_escape')
        for parent in (path, *path.parents):
            if parent == ROOT:
                break
            require(parent.is_relative_to(ROOT) and not parent.is_symlink()
                    and not getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400, 'reparse_point_refused')
        require(path.is_relative_to(ROOT) and '..' not in Path(relative).parts
                and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400 and digest(path) == expected, 'source_hash_mismatch')
    if not monitor and receipt.get('prepared'):
        require(digest(CONFIG) == receipt['config_sha256'], 'configuration_hash_mismatch')
    sys.path.insert(0, str(ROOT / 'code'))
    return receipt


def token():
    import win32api
    import win32con
    import win32security as security
    handle = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    sid = security.ConvertSidToStringSid(security.GetTokenInformation(handle, security.TokenUser)[0])
    return handle, sid


def load_config():
    config = importlib.import_module('local_config')
    require(Path(config.__file__) == CONFIG and config.DURABLE_DB_PATH == str(DB)
            and config.ERPNEXT_URL == ORIGIN and config.ERPNEXT_API_KEY == SYNTHETIC
            and config.ERPNEXT_API_SECRET == SYNTHETIC
            and config.HISTORICAL_RECOVERY_ENABLED is False
            and config.HISTORICAL_RECOVERY_COORDINATION is False, 'synthetic_config_mismatch')
    return config


def publish(leaf, value):
    require(Path(leaf).name == leaf, 'evidence_leaf_only')
    target = ROOT / 'evidence' / leaf
    temporary = target.with_suffix('.part')
    temporary.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    os.replace(temporary, target)


def snapshot():
    from sync_ledger import Ledger
    with Ledger(DB, readonly=True) as db:
        db.verify_integrity()
        rows = [tuple(r) for r in db.db.execute(
            'SELECT device_id,latest_observed_punch FROM device_state ORDER BY device_id')]
        result = dict(total=db.diagnostics()['total'], states=db.diagnostics()['states'],
            holds=db.diagnostics()['cutover_holds'] if 'cutover_holds' in db.diagnostics() else
                db.db.execute("SELECT COUNT(*) FROM punches WHERE error_category='cutover_hold'").fetchone()[0],
            checkpoints=rows, activation=digest(MARKER),
            journal=db.db.execute('PRAGMA journal_mode').fetchone()[0],
            recovery_tables=[r[0] for r in db.db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sr_%'")])
    require(result['total'] == 13 and result['states']['synced'] == 12 and result['holds'] == 1
            and len(rows) == 12 and result['journal'] == 'delete' and not result['recovery_tables'],
            'unexpected_durable_state')
    return result


def configure(sid):
    # This runs BEFORE the read-only monitor grant, using the original strict validator.
    sys.path.insert(0, str(ROOT / 'code'))
    from monitor_storage import canonical_sid
    canonical_sid(sid)
    require(not DB.exists() and not MARKER.exists() and not CONFIG.exists(), 'fresh_disposable_files_required')
    settings = dict(DURABLE_SYNC_ENABLED=True, DURABLE_DB_PATH=str(DB),
        DURABLE_MONITOR_READONLY_SID=sid, ERPNEXT_URL=ORIGIN, ERPNEXT_VERSION=15,
        ERPNEXT_API_KEY=SYNTHETIC, ERPNEXT_API_SECRET=SYNTHETIC, ERPNEXT_REQUEST_TIMEOUT=3,
        IMPORT_START_DATE=None, PULL_FREQUENCY=0.01, DURABLE_REQUESTS_PER_SECOND=100,
        HISTORICAL_RECOVERY_ENABLED=False, HISTORICAL_RECOVERY_COORDINATION=False)
    CONFIG.write_text('\n'.join(k + ' = ' + repr(v) for k, v in settings.items()) + '\n', encoding='utf-8')
    from durable_startup import provision
    boundary = str((dt.datetime.now() + dt.timedelta(minutes=2)).replace(microsecond=0))
    with safety().exclusive_worker():
        provision(DB, ORIGIN, 15, boundary, 'SYNTHETIC SCM ACCEPTANCE ' + RUN)
    return {'cutover': boundary}


def transport_guard():
    """Restrict Python socket calls to the one fixed literal loopback endpoint.

    Windows native S4U/LSA may separately consult the domain for identity data.
    This guard does not replace Windows firewall policy or authorize AD changes.
    """
    original_connect = socket.socket.connect
    original_bind = socket.socket.bind
    original_info = socket.getaddrinfo

    def connect(sock, address):
        require(address == ('127.0.0.1', PORT), 'non_loopback_connection_denied')
        return original_connect(sock, address)

    def bind(sock, address):
        require(address == ('127.0.0.1', PORT), 'unexpected_bind_denied')
        return original_bind(sock, address)

    def info(host, port, family=0, type=0, proto=0, flags=0):
        require(host == '127.0.0.1' and port == PORT, 'DNS_or_destination_denied')
        return original_info(host, port, family, type, proto, flags | socket.AI_NUMERICHOST)

    def denied(*args, **kwargs):
        raise RuntimeError('UDP_DNS_or_connect_ex_denied')
    socket.socket.connect = connect
    socket.socket.bind = bind
    socket.socket.connect_ex = denied
    socket.socket.sendto = denied
    socket.getaddrinfo = info
    socket.gethostbyname = socket.gethostbyaddr = denied


def payload_ok(value):
    keys = {'employee_field_value', 'timestamp', 'device_id', 'log_type', 'latitude', 'longitude'}
    return (set(value) == keys and value['device_id'] in {'SCM_SYNTH_' + str(i).zfill(2) for i in range(1, 13)}
            and value['employee_field_value'] == 'SCM_EMP_' + value['device_id'][-2:]
            and value['log_type'] is None and value['latitude'] is None and value['longitude'] is None)


def serve():
    accepted = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            require(0 < size <= 2048 and self.path == METHOD
                    and self.headers.get('Authorization') == 'token ' + SYNTHETIC + ':' + SYNTHETIC,
                    'unexpected_receiver_request')
            value = json.loads(self.rfile.read(size))
            require(payload_ok(value), 'non_synthetic_payload')
            accepted.append(value)
            raw = json.dumps({'message': {'name': 'SCM-MOCK-' + value['device_id']}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    class Server(HTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(3)
            try:
                return context.wrap_socket(connection, server_side=True), address
            except BaseException:
                connection.close()
                raise
        def handle_error(self, *args):
            publish('receiver-failure.json', {'status': 'FAIL'})
    server = Server(('127.0.0.1', PORT), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(ROOT / 'tools' / 'localhost-cert.pem', ROOT / 'tools' / 'localhost-key.pem')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, accepted


def request(method, url, **options):
    require(method == 'POST' and url == ORIGIN + METHOD and options.get('allow_redirects') is False
            and options['headers']['Authorization'] == 'token ' + SYNTHETIC + ':' + SYNTHETIC
            and payload_ok(options['json']), 'unsafe_transport_input')
    context = ssl.create_default_context(cafile=str(ROOT / 'tools' / 'localhost-cert.pem'))
    connection = http.client.HTTPSConnection('127.0.0.1', PORT, timeout=3, context=context)
    try:
        connection.request('POST', METHOD, body=json.dumps(options['json']), headers=options['headers'])
        response = connection.getresponse()
        return types.SimpleNamespace(status_code=response.status, content=response.read(4096), headers={})
    finally:
        connection.close()


def service():
    receipt = installed()
    handle, sid = token()
    handle.Close()
    require(sid == SYSTEM, 'actual_LocalSystem_required')
    transport_guard()
    config = load_config()
    require(config.DURABLE_MONITOR_READONLY_SID == receipt['monitor_sid'], 'monitor_configuration_mismatch')
    config.devices = [dict(device_id='SCM_SYNTH_' + str(i).zfill(2), ip='SYNTHETIC_NO_SOCKET',
        port=0, password=0, punch_direction=None, clear_from_device_on_fetch=False) for i in range(1, 13)]
    config.shift_type_device_mapping = []
    boundary = dt.datetime.fromisoformat(json.loads(MARKER.read_text())['cutover_time'])
    polls = []

    def fetch(ip, **options):
        require(ip == 'SYNTHETIC_NO_SOCKET' and options['port'] == 0 and options['password'] == 0
                and options['clear_from_device_on_fetch'] is False, 'real_device_arguments_denied')
        number = int(options['device_id'][-2:])
        polls.append(options['device_id'])
        row = dict(user_id='SCM_EMP_' + str(number).zfill(2),
            timestamp=boundary + dt.timedelta(seconds=number), uid=number, punch=0, status=1)
        return [row, dict(row, timestamp=boundary - dt.timedelta(seconds=1), uid=1000)] if number == 1 else [row]

    logger = logging.getLogger(NAME)
    logger.addHandler(logging.NullHandler())
    engine = types.SimpleNamespace(config=config, requests=types.SimpleNamespace(request=request),
        validate_unique_device_ids=lambda ds: require(len({d['device_id'] for d in ds}) == 12, 'device_count'),
        normalize_device_config=dict, get_all_attendance_from_device=fetch,
        get_dump_file_name_and_directory=lambda _: ROOT / 'evidence' / 'absent-synthetic.dump',
        device_punch_values_IN=[0, 4], device_punch_values_OUT=[1, 5],
        info_logger=logger, error_logger=logger, status=types.SimpleNamespace(usable=False))
    from durable_sync import run
    posts = []
    ownership_check = None
    cycles = 0
    service_started = time.monotonic()

    def tick(stop_requested=lambda: False):
        nonlocal cycles
        try:
            ownership_check()
            require(time.monotonic() - service_started < 900, 'acceptance_lease_expired')
            require(config.HISTORICAL_RECOVERY_ENABLED is False
                    and config.HISTORICAL_RECOVERY_COORDINATION is False, 'recovery_enabled')
            polls.clear()
            run(engine, stop_requested=stop_requested)  # Real policy, Ledger, mutex and delivery.
            ownership_check()
            cycles += 1
            state = snapshot()
            require(len(set(polls)) == 12, 'twelve_device_poll_not_observed')
            started = time.time()
            # A real RESERVED writer transaction permits concurrent read-only SQLite.
            with sqlite3.connect(DB, timeout=5) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('UPDATE device_state SET fetch_ok=fetch_ok')
                publish('writer-window.json', {'pid': os.getpid(), 'start': started, 'active': True})
                for _ in range(50):
                    ownership_check()
                    time.sleep(.1)
            ended = time.time()
            with (ROOT / 'evidence' / 'windows.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'start': started, 'end': ended}) + '\n')
            publish('cycle.json', dict(state, pid=os.getpid(), sid=sid, cycles=cycles,
                executable=sys.executable, config=str(CONFIG), recovery_enabled=False,
                twelve_polled=True, posts=len(posts), time=time.time()))
        except Exception as error:
            publish('failure.json', {'status': 'FAIL', 'category': type(error).__name__})
            raise
    synthetic = types.ModuleType('erpnext_sync')
    synthetic.config, synthetic.main = config, tick
    sys.modules['erpnext_sync'] = synthetic
    import erpnext_sync_win
    import servicemanager
    class AcceptanceService(erpnext_sync_win.PythonCornerExample):
        _svc_name_ = NAME
        _svc_display_name_ = 'Disposable Phase 2 ACL acceptance ' + RUN
        _svc_description_ = 'Synthetic acceptance only ' + CANDIDATE
        def main(self):
            nonlocal posts, ownership_check
            # Same SCM service thread: the unchanged application lock is recursive here.
            with safety().exclusive_worker() as check:
                ownership_check = check
                server, thread, posts = serve()
                try:
                    super().main()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(5)
                    require(not thread.is_alive(), 'receiver_shutdown_failed')
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(AcceptanceService)
    servicemanager.StartServiceCtrlDispatcher()



def access_probe(path, access, allowed):
    """OPEN_EXISTING only: test requested rights without performing mutations."""
    from ctypes import wintypes as w
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    ctypes.set_last_error(0)
    handle = api.CreateFileW(str(path), access, 7, None, 3, 0x02000000, None)
    valid = handle not in (None, ctypes.c_void_p(-1).value)
    error = ctypes.get_last_error()
    if valid:
        api.CloseHandle(handle)
    require(valid if allowed else not valid and error == 5, 'access_probe_failed')
    return True


def monitor():
    receipt = installed(monitor=True)
    handle, sid = token()
    try:
        require(sid == receipt['monitor_sid'] and sid != SYSTEM, 'approved_actual_monitor_token_required')
        from monitor_storage_windows import Authz, descriptor
        from monitor_storage import check_identity, READ, DIRECTORY_READ
        import win32security as security
        name, _, account_type = security.LookupAccountSid(None, security.ConvertStringSidToSid(sid))
        context = Authz(token=handle)
        try:
            groups, privileges = context.identity_details()
            check_identity(sid, account_type, name, groups, privileges)
            require(context.maximum(descriptor(DB)[0]) & ~READ == 0, 'ledger_effective_excess')
            require(context.maximum(descriptor(DB.parent)[0]) & ~DIRECTORY_READ == 0, 'directory_effective_excess')
        finally:
            context.close()
        access_probe(DB, 0x80000000, True)
        access_probe(DB.parent, 0x80000000 | 0x20000000, True)
        # Each desired right is checked separately; one denied bit cannot hide another.
        for path, masks in ((DB, (2, 4, 0x10, 0x100, 0x10000, 0x40000, 0x80000)),
                            (DB.parent, (2, 4, 0x40, 0x10, 0x100, 0x10000, 0x40000, 0x80000))):
            for mask in masks:
                access_probe(path, mask, False)
        access_probe(CONFIG, 0x80000000, False)
        access_probe(MARKER, 0x80000000, False)
        started, samples = time.time(), []
        until = time.monotonic() + 45
        while time.monotonic() < until:
            with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=2) as connection:
                total = connection.execute('SELECT COUNT(*) FROM punches').fetchone()[0]
                require(total == 13, 'monitor_row_count')
                # Python's read-only connection must refuse a SQL write too.
                try:
                    connection.execute('UPDATE device_state SET fetch_ok=fetch_ok')
                except sqlite3.OperationalError as error:
                    require(getattr(error, 'sqlite_errorcode', None) == sqlite3.SQLITE_READONLY, 'unexpected_SQL_denial')
                else:
                    raise RuntimeError('read_only_SQL_write_succeeded')
            samples.append(time.time())
            time.sleep(.2)
        return dict(schema='fpf-monitor-result-v1', run=RUN, candidate=CANDIDATE, status='PASS',
            sid_sha256=hashlib.sha256(sid.encode()).hexdigest(), start=started, end=time.time(),
            samples=samples, read_only=True, config_denied=True, activation_denied=True)
    finally:
        handle.Close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('preflight', 'configure', 'service', 'monitor', 'contender', 'snapshot', 'backup', 'begin-evidence', 'seal-evidence', 'verify-evidence'))
    parser.add_argument('--sid')
    parser.add_argument('--pid', type=int, default=0)
    parser.add_argument('--attempt')
    args = parser.parse_args()
    require(os.environ.get('COMPUTERNAME', '').upper() == 'ZUHAIR-PC', 'ZUHAIR_PC_only')
    runtime_version()
    if args.mode == 'preflight':
        mutex_absent()
        print(json.dumps({'status': 'PASS', 'python': '3.11.9 x64', 'mutex': 'absent'}))
        return
    if args.mode == 'configure':
        require(Path(__file__).absolute() == ROOT / 'tools' / 'scm_host.py', 'installed_harness_path_required')
        print(json.dumps(configure(args.sid)))
        return
    if args.mode == 'service':
        return service()
    if args.mode == 'monitor':
        print(json.dumps(monitor()))
        return
    installed()
    if args.mode == 'begin-evidence':
        print(safety().begin().name)
        return
    if args.mode == 'seal-evidence':
        attempt = safety().attempt_path(args.attempt)
        required = ['receipt.json', 'summary.json', 'ACLs.json', 'partials.json']
        if (attempt / 'payload' / 'scm-qc.txt').exists():
            required += ['scm-qc.txt', 'scm-stopped.txt', 'scm-running-registration.json']
        print(safety().seal_publish(attempt, required))
        return
    if args.mode == 'verify-evidence':
        safety().validate_cleanup()
        print('EVIDENCE_COMPLETE_HASHES_AND_PARTIALS_VERIFIED')
        return
    if args.mode == 'backup':
        attempt = safety().attempt_path(args.attempt)
        safety().validate_attempt(attempt)
        destination = attempt / 'payload' / 'ledger.sqlite3'
        require(not destination.exists() and destination.parent.is_dir(), 'new_fixed_backup_required')
        with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(destination) as target:
                source.backup(target)
                require(target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'backup_integrity')
        print('{"status":"PASS","backup":"consistent_verified"}')
    elif args.mode == 'snapshot':
        print(json.dumps(snapshot()))
    elif args.mode == 'contender':
        # Observation only: if the expected service vanished, NEVER become its successor.
        owners = safety().assert_owner(args.pid)
        print(json.dumps(dict(status='PASS', mutex='expected_service_exclusive_owner',
                              expected_pid=args.pid, owners=owners, time=time.time())))



if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'FAIL', 'category': type(error).__name__,
                          'code': str(error) if type(error) is RuntimeError else 'details_suppressed'}))
        raise SystemExit(1) from None
