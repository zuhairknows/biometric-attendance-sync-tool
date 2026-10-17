"""Step 2 fixture: synthetic localhost HTTPS with process-local isolation. No service code."""
import argparse
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.request


class IsolationError(RuntimeError):
    pass


class Isolation:
    """A non-removable audit hook for this trusted fixture, not a host security sandbox."""
    def __init__(self, root):
        self.root = root.resolve()
        self.runtime = {Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()}
        self.port = None
        self.blocked = set()

    def deny(self, reason):
        self.blocked.add(reason)
        raise IsolationError(reason)

    def inside(self, path, root):
        return path == root or root in path.parents

    def audit(self, event, args):
        if event == 'import' and args[0].split('.')[0] in {
                'local_config', 'erpnext_sync', 'erpnext_sync_win', 'SMWinservice', 'zk',
                'servicemanager', 'win32service', 'win32serviceutil', 'ctypes'}:
            self.deny('production_or_native_import')
        if event.startswith(('subprocess.', 'os.exec', 'os.spawn', 'ctypes.', 'winreg.')) or event == 'os.system':
            self.deny('process_or_native_access')
        if event == 'socket.__new__':
            if args[1] not in (socket.AF_INET, socket.AF_INET6) or args[2] != socket.SOCK_STREAM:
                self.deny('non_tcp_socket')
        if event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyname_ex', 'socket.gethostbyaddr'):
            if args[0] != '127.0.0.1':
                self.deny('external_dns')
        if event == 'socket.connect':
            address = args[1]
            if not isinstance(address, tuple) or address[:2] != ('127.0.0.1', self.port):
                self.deny('external_or_device_connect')
        if event == 'socket.bind' and args[1] != ('127.0.0.1', 0):
            self.deny('non_loopback_bind')
        if event in ('socket.sendto', 'socket.sendmsg'):
            self.deny('datagram_send')
        if event == 'open' and not isinstance(args[0], int):
            path = Path(os.fsdecode(args[0])).resolve()
            writing = bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if self.inside(path, self.root):
                return
            if writing or path.name == 'local_config.py' or not any(self.inside(path, r) for r in self.runtime):
                self.deny('outside_staging_file_access')
        if event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.rename', 'os.replace'):
            paths = args[:2] if event in ('os.rename', 'os.replace') else args[:1]
            if any(not self.inside(Path(os.fsdecode(p)).resolve(), self.root) for p in paths):
                self.deny('outside_staging_mutation')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def self_test(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not root.name.startswith('FPF-Phase1-Staging-') or str(root).startswith('\\\\'):
        raise ValueError('invalid_staging_directory')
    cert, key = root / 'localhost-cert.pem', root / 'localhost-key.pem'
    if cert.resolve().parent != root or key.resolve().parent != root:
        raise ValueError('certificate_path_escape')
    # Load only these local files. No Windows trust-store installation or TLS bypass.
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.minimum_version = ssl.TLSVersion.TLSv1_2
    server_tls.load_cert_chain(cert, key)
    client_tls = ssl.create_default_context(cafile=str(cert))
    client_tls.minimum_version = ssl.TLSVersion.TLSv1_2
    policy = Isolation(root)
    sys.addaudithook(policy.audit)
    documents = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log request headers or attendance values.

        def reply(self, status, data):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == '/health':
                return self.reply(200, dict(mock=True, records=len(documents)))
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', 'https://erp.myfpf.com/')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.reply(404, dict(mock=True))

        def do_POST(self):
            if self.path != '/api/method/hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field':
                return self.reply(404, dict(mock=True))
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                return self.reply(400, dict(mock=True))
            value = json.loads(self.rfile.read(length))
            if value.get('employee_field_value') != 'STAGE-SYNTHETIC-001' or value.get('device_id') != 'STAGE-DEVICE':
                return self.reply(400, dict(mock=True))
            documents.append(value)
            self.reply(200, dict(message=dict(name='MOCK-CHECKIN-001'), mock=True))

    server = HTTPServer(('127.0.0.1', 0), Handler)
    policy.port = server.server_port
    server.socket = server_tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=client_tls))
    url = 'https://127.0.0.1:' + str(policy.port)
    checks = {}
    try:
        with opener.open(url + '/health', timeout=3) as response:
            checks['https_health'] = json.load(response) == dict(mock=True, records=0)
        payload = dict(employee_field_value='STAGE-SYNTHETIC-001', device_id='STAGE-DEVICE',
            timestamp=str(dt.datetime.now().replace(microsecond=0)), log_type=None, latitude=None, longitude=None)
        (root / 'synthetic-attendance.json').write_text(json.dumps([payload], indent=2), encoding='utf-8')
        request = urllib.request.Request(url + '/api/method/hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field',
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with opener.open(request, timeout=3) as response:
            checks['synthetic_mock_post'] = json.load(response).get('mock') is True and documents == [payload]
        checks['tls_certificate_and_hostname_verification'] = client_tls.check_hostname and client_tls.verify_mode == ssl.CERT_REQUIRED
        try:
            opener.open(url + '/redirect', timeout=3)
        except urllib.error.HTTPError as error:
            checks['redirect_disabled'] = error.code == 302
            error.close()
        probes = {
            'production_dns_blocked': lambda: socket.getaddrinfo('erp.myfpf.com', 443),
            'device_dns_blocked': lambda: socket.getaddrinfo('stage-device.invalid', 4370),
            'udp_blocked': lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM),
            'production_config_import_blocked': lambda: __import__('local_config'),
            'device_sdk_import_blocked': lambda: __import__('zk'),
            'subprocess_blocked': lambda: __import__('subprocess').run(['must-not-run']),
            'production_file_read_blocked': lambda: (root.parent / 'local_config.py').read_bytes(),
            'outside_write_blocked': lambda: (root.parent / 'MUST-NOT-CREATE.txt').write_text('blocked'),
        }
        def other_tcp():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.connect(('192.0.2.1', 4370))  # Audit rejects BEFORE any network call.
        probes['external_tcp_blocked'] = other_tcp
        for name, probe in probes.items():
            try:
                probe()
                checks[name] = False
            except IsolationError:
                checks[name] = True
        report = dict(schema='phase1-local-https-preflight-v1', status='PASS' if all(checks.values()) else 'FAIL',
            checks=checks, mock_records=len(documents), certificate_sha256=hashlib.sha256(cert.read_bytes()).hexdigest(),
            server_stopped_after_test=True, service_operations=False, production_access=False,
            scope='this_fixture_process_only_not_machine_or_future_service_isolation')
        (root / 'https-self-test.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--stage-root', required=True)
    args = parser.parse_args()
    try:
        if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
            raise ValueError('isolated_python_flags_required')
        report = self_test(args.stage_root)
        print(json.dumps(report, sort_keys=True))
        return 0 if report['status'] == 'PASS' else 1
    except Exception:
        print(json.dumps(dict(status='FAIL', details_suppressed=True)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
