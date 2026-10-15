"""Repeatable offline integration: fixture punches -> SQLite -> loopback fake Frappe.

No production configuration is imported. No configurable external URL/device is accepted.
Run: py scripts/phase1_http_harness.py
"""
import datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
import json as jsonlib
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durable_delivery import FrappeClient, deliver_batch
from punch_normalization import normalize
from sync_ledger import Ledger


def loopback_request(method, url, headers, json=None, timeout=1, params=None, allow_redirects=False):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
        raise ValueError('Harness only permits 127.0.0.1 HTTP')
    if params:
        url += '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, data=jsonlib.dumps(json).encode() if method == 'POST' else None,
                                     headers=dict(headers, **{'Content-Type': 'application/json'}), method=method)
    # Disable environment proxies, so loopback fixtures can never be sent through a proxy.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return types.SimpleNamespace(status_code=response.code, content=response.read(), headers=response.headers)


class FakeFrappeServer:
    def __init__(self):
        self.mode = 'normal'
        self.documents = {}
        self.request_count = 0
        self.drop_next_response = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Request/header logging could expose fixture tokens unnecessarily.

            def reply(self, status, body, retry_after=None):
                raw = jsonlib.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    if retry_after:
                        self.send_header('Retry-After', str(retry_after))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Expected when the timeout/lost-response scenario closes the socket.

            def do_POST(self):
                owner.request_count += 1
                if not self.path.endswith('.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field'):
                    return self.reply(404, {'exception': 'Fixture route not found'})
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 16384:
                    return self.reply(400, {'exception': 'Invalid fixture payload size'})
                payload = jsonlib.loads(self.rfile.read(length))
                if owner.mode == 'slow':
                    time.sleep(0.15)
                    return self.reply(503, {'exception': 'Fixture timeout'})
                if owner.mode in ('down', 'auth', 'forbidden', 'limited'):
                    status = {'down': 503, 'auth': 401, 'forbidden': 403, 'limited': 429}[owner.mode]
                    return self.reply(status, {'exception': 'Fixture destination failure'}, retry_after=2 if status == 429 else None)
                user = payload.get('employee_field_value', '')
                if user == 'fixture-missing':
                    return self.reply(417, {'exc_type': 'ValidationError', 'exception': 'No Employee found for attendance_device_id'})
                if user == 'fixture-inactive':
                    return self.reply(417, {'exc_type': 'ValidationError', 'exception': 'Transactions cannot be created for an Inactive Employee'})
                if set(payload) != {'employee_field_value', 'timestamp', 'device_id', 'log_type', 'latitude', 'longitude'}:
                    return self.reply(417, {'exception': 'Invalid fixture fields'})
                identity = (user, payload['timestamp'])
                if identity in owner.documents:
                    return self.reply(417, {'exc_type': 'ValidationError', 'exception': 'This employee already has a log with the same timestamp'})
                name = 'FAKE-CHECKIN-' + str(len(owner.documents) + 1)
                owner.documents[identity] = dict(payload, name=name)
                if owner.drop_next_response:
                    owner.drop_next_response = False
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                return self.reply(200, {'message': {'name': name}})

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                filters = jsonlib.loads(query['filters'][0])
                if parsed.path.endswith('/Employee'):
                    user = filters[0][2]
                    return self.reply(200, {'data': [dict(name='EMP-' + user, status='Active', attendance_device_id=user)]})
                if parsed.path.endswith('/Employee%20Checkin'):
                    values = {field: value for field, operator, value in filters}
                    user = values['employee'][4:]
                    doc = owner.documents.get((user, values['time']))
                    rows = [dict(name=doc['name'], employee=values['employee'], time=doc['timestamp'],
                                 log_type=doc['log_type'] or '')] if doc else []
                    return self.reply(200, {'data': rows})
                return self.reply(404, {})

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def require(condition):
    # Unlike assert, these integration checks still run if Python was started with -O.
    if not condition:
        raise AssertionError('Loopback HTTP scenario did not meet its expected ledger outcome')


def run_scenarios():
    results = []
    with tempfile.TemporaryDirectory(prefix='phase1-loopback-') as directory, FakeFrappeServer() as server:
        path = Path(directory) / 'fixture.sqlite3'
        config = types.SimpleNamespace(ERPNEXT_URL=server.url, ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY='fixture-key', ERPNEXT_API_SECRET='fixture-secret', ERPNEXT_REQUEST_TIMEOUT=1,
            DURABLE_CONFIRM_READS_VERIFIED=True, DURABLE_SERVER_REVIEW_REFERENCE='SYNTHETIC LOOPBACK ONLY')
        client = FrappeClient(config, loopback_request)
        tick = [1000.0]
        device = {'device_id': 'FIXTURE_DEVICE', 'punch_direction': None}

        with Ledger(path) as ledger:
            def add(user):
                source = dict(user_id=user, timestamp=dt.datetime(2026, 10, 13, 8), punch=0, status=1, uid=1)
                return ledger.ingest([normalize(source, device)], device['device_id'])

            def deliver():
                return deliver_batch(ledger, client, clock=lambda: tick[0], sleep=lambda _: None,
                                     rate=100, base=1, maximum=4, auth_cooldown=10)

            def state(user):
                return ledger.db.execute('SELECT state FROM punches WHERE user_id=?', (user,)).fetchone()[0]

            add('fixture-ok')
            require(deliver() == 1 and state('fixture-ok') == 'synced')
            before = server.request_count
            add('fixture-ok')
            require(deliver() == 0 and server.request_count == before)
            results.append('capture_create_repeat_dedup')

            for mode in ('down', 'auth', 'forbidden', 'limited', 'slow'):
                user = 'fixture-' + mode
                add(user)
                server.mode = mode
                if mode == 'slow':
                    client.timeout = 0.03
                require(deliver() == 1 and state(user) == 'retry')
                before = server.request_count
                require(deliver() == 0 and server.request_count == before)
                if mode == 'slow':
                    time.sleep(0.2)
                    client.timeout = 1
                server.mode = 'normal'
                tick[0] += 20
                require(deliver() == 1 and state(user) == ('retry' if mode in ('down', 'slow') else 'synced'))
                if mode in ('down', 'slow'):
                    # No match is not proof of no remote commit. Retain uncertainty, then
                    # supply synthetic positive remote evidence to exercise safe resolution.
                    require(ledger.db.execute('SELECT error_category FROM punches WHERE user_id=?', (user,)).fetchone()[0] == 'confirmation_pending')
                    server.documents[(user, '2026-10-13 08:00:00')] = dict(name='FIXTURE-CONFIRMED-' + mode,
                        timestamp='2026-10-13 08:00:00', log_type=None)
                    tick[0] += 20
                    require(deliver() == 1 and state(user) == 'duplicate')
                results.append(mode + '_cooldown_recovery')

            for user, expected in (('fixture-missing', 'unmatched_employee'), ('fixture-inactive', 'rejected')):
                add(user)
                require(deliver() == 1 and state(user) == expected)
            results.append('permanent_employee_quarantine')

            add('fixture-lost-response')
            server.drop_next_response = True
            require(deliver() == 1 and state('fixture-lost-response') == 'retry')
            require(('fixture-lost-response', '2026-10-13 08:00:00') in server.documents)
            tick[0] += 20
            require(deliver() == 1 and state('fixture-lost-response') == 'duplicate')
            results.append('remote_creation_lost_response_duplicate')

            add('fixture-client-crash')
            def crash_after_post(*args, **kwargs):
                loopback_request(*args, **kwargs)
                raise SystemExit('Injected client crash after remote creation')
            crashing = FrappeClient(config, crash_after_post)
            try:
                deliver_batch(ledger, crashing, clock=lambda: tick[0], sleep=lambda _: None)
                raise AssertionError('Expected injected crash')
            except SystemExit:
                pass
            require(state('fixture-client-crash') == 'delivering')
            tick[0] += 121
            with Ledger(path) as restarted:
                require(deliver_batch(restarted, client, clock=lambda: tick[0], sleep=lambda _: None) == 1)
            require(state('fixture-client-crash') == 'duplicate')
            results.append('remote_commit_client_crash_restart')

            add('fixture-pending-restart')
            with Ledger(path) as restarted:
                require(deliver_batch(restarted, client, clock=lambda: tick[0], sleep=lambda _: None) == 1)
            require(state('fixture-pending-restart') == 'synced')
            results.append('pending_restart')
            require(ledger.verify_integrity()['integrity'] == 'ok')
        return {'result': 'passed', 'transport': '127.0.0.1 real HTTP', 'scenarios': results,
                'scenario_count': len(results), 'fake_checkins_created': len(server.documents),
                'live_hardware': 'NOT EXECUTED', 'staging_frappe': 'NOT EXECUTED', 'windows_scm': 'NOT EXECUTED'}


if __name__ == '__main__':
    print(jsonlib.dumps(run_scenarios(), indent=2))
