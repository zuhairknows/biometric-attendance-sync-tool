"""Step 5B: one synthetic durable-engine cycle; literal-loopback HTTPS only.

Trusted-code application isolation, not an OS sandbox. No service operations.
Every invocation retains a new private attempt; earlier evidence is read-only.
"""
import argparse
import ctypes
from ctypes import wintypes
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request
import uuid


REVISION = '4256cd6201b17dcda3f9d078552acf1d3c58b3f0'
HASHES = {
    'durable_sync.py': '80cba72cfe80326b430f1fee99a544066b5fa8691976659f9bcf97bc079acf9e',
    'durable_startup.py': 'c657f64d327ce0b9f89560cef94bf49c556b6a05d7059b7631cc4d41ee5456ce',
    'durable_delivery.py': 'd549e943fc0b0fc4751fe3ae960c77d1e3b1660b797798d09479705764119389',
    'sync_ledger.py': 'becff8ae23d6bb1acb12878c48ecbd9948d14d2b883c05a9b8031191d1865fd0',
    'punch_normalization.py': 'a70d7eebb2e58cf46a31c9872aa8480e24f0c1589ce53bfc52dc358279cb44d8',
}
METHOD = '/api/method/hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field'
PHASE = 'preparation'


class Blocked(RuntimeError):
    pass


class Policy:
    def __init__(self, attempt, certificate, key, ledger, acl_script):
        self.attempt = attempt
        self.read_only = {certificate, key}
        self.runtime = {Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()}
        self.ledger = ledger
        self.acl_script = acl_script
        self.port = None
        self.acl_checks = 0
        self.powershell_directory = str(Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0')

    @staticmethod
    def inside(path, root):
        return path == root or root in path.parents

    def deny(self):
        raise Blocked(self.event)

    def audit(self, event, args):
        self.event = event
        if event == 'import' and args[0].split('.')[0] in {
                'local_config', 'erpnext_sync', 'erpnext_sync_win', 'SMWinservice',
                'zk', 'requests', 'win32service', 'win32serviceutil', 'servicemanager'}:
            self.deny()
        if event == 'subprocess.Popen':
            # The actual engine's fixed, read-only ACL verifier is the ONLY child.
            executable, argv, cwd, env = args
            expected = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', self.acl_script]
            # CPython's Windows audit event contains the serialized command line.
            if (executable not in (None, 'powershell.exe') or argv not in (expected, subprocess.list2cmdline(expected))
                    or cwd is not None or not isinstance(env, dict)
                    or env.get('PATH') != self.powershell_directory
                    or env.get('FPF_LEDGER_DIRECTORY') != str(self.ledger.parent)
                    or env.get('FPF_LEDGER_SERVICE_SID') != ''):
                self.deny()
            self.acl_checks += 1
        if event.startswith(('os.exec', 'os.spawn', 'winreg.')) or event == 'os.system':
            self.deny()
        if event == 'ctypes.dlopen' and args[0] != 'kernel32':
            self.deny()
        if event == 'ctypes.dlsym' and args[1] not in {
                'CreateMutexW', 'WaitForSingleObject', 'ReleaseMutex', 'CloseHandle'}:
            self.deny()
        if event == 'socket.__new__' and (args[1] != socket.AF_INET or args[2] != socket.SOCK_STREAM):
            self.deny()
        if event in {'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyname_ex', 'socket.gethostbyaddr'}:
            if args[0] != '127.0.0.1':
                self.deny()
        if event == 'socket.connect' and args[1] != ('127.0.0.1', self.port):
            self.deny()
        if event == 'socket.bind' and args[1] != ('127.0.0.1', 0):
            self.deny()
        if event in {'socket.sendto', 'socket.sendmsg'}:
            self.deny()
        if event == 'open' and not isinstance(args[0], int):
            path = Path(os.fsdecode(args[0])).resolve()
            writing = args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if self.inside(path, self.attempt):
                # Frozen source is read-only even to the trusted harness.
                if writing and self.inside(path, self.attempt / 'code'):
                    self.deny()
                return
            if writing or not (path in self.read_only or any(self.inside(path, r) for r in self.runtime)):
                self.deny()
        if event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.rename', 'os.replace', 'os.chmod', 'os.link', 'os.symlink'}:
            paths = args[:2] if event in {'os.rename', 'os.replace', 'os.link', 'os.symlink'} else args[:1]
            if any(not self.inside(Path(os.fsdecode(p)).resolve(), self.attempt / 'data') for p in paths):
                self.deny()
        if event == 'sqlite3.connect':
            target = args[0]
            allowed = {str(self.ledger), self.ledger.as_uri() + '?mode=ro', ':memory:'}
            if target not in allowed:
                self.deny()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def transport(opener, origin):
    def request(method, url, *, headers=None, json=None, params=None, timeout=3, allow_redirects=False):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != origin.removeprefix('https://')
                or parsed.username or parsed.password or parsed.fragment or parsed.query
                or allow_redirects is not False or method not in {'GET', 'POST'}):
            raise Blocked('https_origin_required')
        if parsed.path not in {METHOD, '/health', '/redirect', '/api/resource/Employee', '/api/resource/Employee%20Checkin'}:
            raise Blocked('unexpected_endpoint')
        if method == 'POST' and (parsed.path != METHOD or not isinstance(json, dict)
                or json.get('employee_field_value') != 'STAGE-SYNTHETIC-001'
                or json.get('device_id') != 'STAGE-DEVICE'):
            raise Blocked('synthetic_payload_required')
        if params:
            url += '?' + urllib.parse.urlencode(params)
        raw = globals()['json'].dumps(json).encode() if json is not None else None
        req = urllib.request.Request(url, data=raw, headers=dict(headers or {}, **{'Content-Type': 'application/json'}), method=method)
        try:
            response = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return SimpleNamespace(status_code=response.code, content=response.read(16384), headers=dict(response.headers))
    return request


def prepare(stage, repository):
    # Validate before making a new attempt. Never load configuration or service modules.
    if sys.platform != 'win32' or not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise ValueError('windows_isolated_python_required')
    stage = stage.resolve(strict=True)
    expected_parent = Path(os.environ['LOCALAPPDATA']).resolve(strict=True)
    if stage.parent != expected_parent or not stage.name.startswith('FPF-Phase1-Staging-'):
        raise ValueError('unexpected_staging_root')
    for path in (stage, *stage.parents):
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('reparse_path')
    sources = {}
    for name, digest in HASHES.items():
        sources[name] = (repository / name).read_bytes()
        if hashlib.sha256(sources[name]).hexdigest() != digest:
            raise ValueError('pinned_source_hash_mismatch')
    for name in ('localhost-cert.pem', 'localhost-key.pem'):
        path = stage / name
        if not path.is_file() or path.resolve() != path or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('tls_artifact_invalid')
    attempt = stage / ('step5b-' + uuid.uuid4().hex)
    attempt.mkdir()
    # Set permissions ONLY on this newly created directory, never an existing artifact.
    script = r'''
$ErrorActionPreference='Stop'
$p=$env:FPF_NEW_ATTEMPT
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$acl=[Security.AccessControl.DirectorySecurity]::new()
$acl.SetOwner([Security.Principal.SecurityIdentifier]::new($sid))
$acl.SetAccessRuleProtection($true,$false)
foreach($s in @($sid,'S-1-5-18','S-1-5-32-544')) {
 $rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($s),'FullControl','ContainerInherit,ObjectInherit','None','Allow')
 $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $p -AclObject $acl
'''
    powershell = str(Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
    result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', script],
        env=dict(os.environ, FPF_NEW_ATTEMPT=str(attempt)), capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError('new_attempt_acl_failed')
    code, data = attempt / 'code', attempt / 'data'
    code.mkdir()
    data.mkdir()
    # The engine requires the ledger parent itself to have protected inheritance.
    result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command',
        "$a=Get-Acl -LiteralPath $env:FPF_NEW_DATA; $a.SetAccessRuleProtection($true,$true); Set-Acl -LiteralPath $env:FPF_NEW_DATA -AclObject $a"],
        env=dict(os.environ, FPF_NEW_DATA=str(data)), capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError('new_data_acl_failed')
    for name, content in sources.items():
        (code / name).write_bytes(content)
    (data / 'provenance.json').write_text(json.dumps(dict(revision=REVISION, source_sha256=HASHES,
        harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        certificate_sha256=hashlib.sha256((stage / 'localhost-cert.pem').read_bytes()).hexdigest()), indent=2))
    return attempt


def run(stage):
    global PHASE
    stage = Path(stage)
    attempt = prepare(stage, Path(__file__).resolve().parents[1])
    stage = stage.resolve()
    sys.path.insert(0, str(attempt / 'code'))
    import durable_sync
    import durable_startup
    from sync_ledger import Ledger
    # Extract the fixed ACL script from the hash-verified function; no configurable command.
    acl_script = next(v for v in durable_startup.private_storage.__code__.co_consts
                      if isinstance(v, str) and "$p=$env:FPF_LEDGER_DIRECTORY" in v)
    ledger = attempt / 'data' / 'synthetic.sqlite3'
    cert, key = stage / 'localhost-cert.pem', stage / 'localhost-key.pem'
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.minimum_version = ssl.TLSVersion.TLSv1_2
    server_tls.load_cert_chain(cert, key)
    client_tls = ssl.create_default_context(cafile=str(cert))
    client_tls.minimum_version = ssl.TLSVersion.TLSv1_2
    policy = Policy(attempt, cert, key, ledger, acl_script)
    # The pinned engine invokes powershell.exe by name. Restrict resolution to the
    # Windows installation and an empty fresh working directory for this process.
    os.environ['PATH'] = policy.powershell_directory
    os.chdir(attempt / 'data')
    sys.addaudithook(policy.audit)
    PHASE = 'mock_startup'
    accepted = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(302 if self.path == '/redirect' else 200)
            if self.path == '/redirect':
                self.send_header('Location', 'https://must-not-resolve.invalid/')
            self.send_header('Content-Length', '0')
            self.end_headers()
        def do_POST(self):
            size = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(size)) if 0 < size <= 4096 else {}
            if self.path != METHOD or payload != expected_payload:
                self.send_error(400)
                return
            accepted.append(payload)
            raw = b'{"message":{"name":"MOCK-CHECKIN-001"}}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = HTTPServer(('127.0.0.1', 0), Handler)
    policy.port = server.server_port
    server.socket = server_tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checks = {}
    origin = 'https://127.0.0.1:' + str(policy.port)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=client_tls))
    request = transport(opener, origin)
    try:
        PHASE = 'isolation_probes'
        probes = {
            'external_dns_blocked': lambda: socket.getaddrinfo('must-not-resolve.invalid', 443),
            'external_tcp_blocked': lambda: socket.create_connection(('192.0.2.1', 4370)),
            'other_loopback_port_blocked': lambda: socket.create_connection(('127.0.0.1', 4370 if policy.port != 4370 else 4371)),
            'udp_blocked': lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM),
            'device_sdk_blocked': lambda: __import__('zk'),
            'production_config_blocked': lambda: __import__('local_config'),
            'production_integration_blocked': lambda: __import__('erpnext_sync'),
            'arbitrary_child_blocked': lambda: subprocess.run(['must-not-run']),
            'native_library_blocked': lambda: ctypes.CDLL('must-not-load'),
            'prior_evidence_write_blocked': lambda: cert.write_bytes(b'must-not-write'),
            'outside_read_blocked': lambda: (stage.parent / 'local_config.py').read_bytes(),
            'outside_sqlite_blocked': lambda: sqlite3.connect(str(stage / 'MUST-NOT-CREATE.sqlite3')),
            'http_blocked': lambda: request('GET', origin.replace('https:', 'http:') + '/health'),
            'foreign_https_origin_blocked': lambda: request('GET', 'https://must-not-resolve.invalid/health'),
        }
        for name, probe in probes.items():
            try:
                probe()
                checks[name] = False
            except Blocked:
                checks[name] = True
        checks['redirect_disabled'] = request('GET', origin + '/redirect').status_code == 302
        checks['verified_tls'] = client_tls.check_hostname and client_tls.verify_mode == ssl.CERT_REQUIRED
        for name, context, hostname in (
                ('untrusted_certificate_rejected', ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), '127.0.0.1'),
                ('wrong_tls_hostname_rejected', client_tls, 'wrong-host.invalid')):
            try:
                with socket.create_connection(('127.0.0.1', policy.port), timeout=3) as connection:
                    with context.wrap_socket(connection, server_hostname=hostname):
                        checks[name] = False
            except ssl.SSLCertVerificationError:
                checks[name] = True
        if not all(checks.values()):
            raise ValueError('isolation_probe_failed')
        boundary = dt.datetime.now().replace(microsecond=0) + dt.timedelta(minutes=1)
        stamp = boundary + dt.timedelta(seconds=1)
        expected_payload = dict(employee_field_value='STAGE-SYNTHETIC-001', timestamp=str(stamp),
            device_id='STAGE-DEVICE', log_type=None, latitude=None, longitude=None)
        config = SimpleNamespace(DURABLE_DB_PATH=str(ledger), ERPNEXT_URL=origin, ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY='SYNTHETIC', ERPNEXT_API_SECRET='SYNTHETIC', ERPNEXT_REQUEST_TIMEOUT=3,
            IMPORT_START_DATE=None, PULL_FREQUENCY=1, shift_type_device_mapping=[],
            devices=[dict(device_id='STAGE-DEVICE', ip='SYNTHETIC-NO-SOCKET', port=4370,
                          password=0, punch_direction=None, clear_from_device_on_fetch=False)])
        logger = logging.getLogger('step5b')
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        def synthetic_fetch(*args, **kwargs):
            return [dict(user_id='STAGE-SYNTHETIC-001', timestamp=stamp, punch=0, status=1, uid=1)]
        engine = SimpleNamespace(config=config, requests=SimpleNamespace(request=request),
            info_logger=logger, error_logger=logger, status=SimpleNamespace(usable=True),
            device_punch_values_IN=[0, 4], device_punch_values_OUT=[1, 5],
            validate_unique_device_ids=lambda devices: None, normalize_device_config=dict,
            get_dump_file_name_and_directory=lambda _: str(attempt / 'data' / 'unused-synthetic-dump'),
            get_all_attendance_from_device=synthetic_fetch)
        # No startup guard is replaced: real private_storage, activation, mutex and run().
        PHASE = 'ledger_provisioning'
        durable_startup.provision(ledger, origin, 15, str(boundary), 'SYNTHETIC LOCALHOST STEP 5B ONLY')
        PHASE = 'actual_engine_cycle'
        durable_sync.run(engine)
        PHASE = 'ledger_verification'
        with Ledger(ledger, readonly=True) as db:
            db.verify_integrity()
            rows = db.db.execute('SELECT state,payload,checkin_name FROM punches').fetchall()
            checks['actual_engine_persisted_and_delivered'] = (len(rows) == 1 and rows[0]['state'] == 'synced'
                and json.loads(rows[0]['payload']) == expected_payload and rows[0]['checkin_name'] == 'MOCK-CHECKIN-001')
        checks['one_synthetic_https_submission'] = accepted == [expected_payload]
        checks['real_storage_checks_used'] = policy.acl_checks == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    PHASE = 'report_publication'
    checks['mock_stopped'] = not thread.is_alive()
    report = dict(status='PASS' if all(checks.values()) else 'FAIL', checks=checks,
        mock_records=len(accepted), revision=REVISION, service_operations=False,
        scope='foreground_operator_token_trusted_code_application_isolation_not_OS_sandbox',
        attempt=attempt.name)
    with (attempt / 'data' / 'result.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--stage-root', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.stage_root)
        print(json.dumps(report, sort_keys=True))
        return 0 if report['status'] == 'PASS' else 1
    except Exception as error:
        # Native, TLS and transport exceptions can contain local paths. Never echo them.
        print(json.dumps(dict(status='FAIL', phase=PHASE, category=type(error).__name__,
                              blocked_event=str(error) if isinstance(error, Blocked) else None,
                              details_suppressed=True, preserve_new_attempt=True)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
