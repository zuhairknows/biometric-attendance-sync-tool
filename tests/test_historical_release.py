"""Synthetic release gates and real loopback TLS. No production config or SCM."""
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from durable_startup import activation_path
from historical_attendance import recovery, release
from historical_attendance.https_transport import HTTPS
from historical_attendance.service_recovery import Budget

OPENSSL = shutil.which('openssl') or r'C:\Program Files\Git\usr\bin\openssl.exe'


def authorization(root, ledger, site, contract_hash):
    value = dict(schema='attendance-service-release-v1', enabled=True,
        site_url=site, contract_sha256=contract_hash, ledger=str(Path(ledger).resolve()),
        activation_sha256=recovery.file_hash(activation_path(Path(ledger))), address='127.0.0.1',
        approved_at=time.time() - 1, expires_at=time.time() + 3600,
        approval_reference='SYNTHETIC isolated acceptance',
        evidence={c: 'SYNTHETIC fixture evidence' for c in release.CLAIMS},
        code_sha256={name: recovery.file_hash(release.ROOT / name) for name in release.code_files()})
    path = root / 'release.json'
    path.write_text(json.dumps(value))
    return path, recovery.file_hash(path), value


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='recovery-release-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / 'synthetic.sqlite3'
        activation_path(self.ledger).write_text('synthetic activation')
        self.path, self.sha, self.value = authorization(self.root, self.ledger, 'https://release.example.test', 'a'*64)

    def validate(self, **changes):
        value = dict(self.value, **changes)
        self.path.write_text(json.dumps(value))
        return release.validate(self.path, recovery.file_hash(self.path), site='https://release.example.test',
            contract_hash='a'*64, ledger=self.ledger, storage_check=lambda _: None)

    def test_verified_release_is_site_contract_ledger_and_code_bound(self):
        self.assertEqual(self.validate()['address'], '127.0.0.1')
        for change in (dict(enabled=False), dict(site_url='https://different.example.test'),
                       dict(contract_sha256='b'*64), dict(activation_sha256='0'*64),
                       dict(ledger=str(self.root / 'other.sqlite3')), dict(code_sha256={}),
                       dict(address='requires-unbounded-dns.example.test')):
            with self.subTest(change=change), self.assertRaises(Exception):
                self.validate(**change)

    def test_every_prerequisite_is_independently_required(self):
        for claim in release.CLAIMS:
            evidence = dict(self.value['evidence'])
            del evidence[claim]
            with self.subTest(claim=claim), self.assertRaisesRegex(Exception, 'prerequisites'):
                self.validate(evidence=evidence)

    def test_expired_future_nonfinite_and_overlong_release_refused(self):
        for change in (dict(expires_at=time.time()-1), dict(approved_at=time.time()+60),
                       dict(expires_at=float('nan')), dict(expires_at=time.time()+90000)):
            with self.subTest(change=change), self.assertRaises(Exception):
                self.validate(**change)

    def test_hash_and_storage_checks_are_mandatory(self):
        with self.assertRaises(Exception):
            release.validate(self.path, '0'*64, site='https://release.example.test', contract_hash='a'*64,
                             ledger=self.ledger, storage_check=lambda _: None)
        with self.assertRaises(PermissionError):
            release.validate(self.path, self.sha, site='https://release.example.test', contract_hash='a'*64,
                             ledger=self.ledger, storage_check=mock.Mock(side_effect=PermissionError))

    def production_investigation(self, *, missing_visibility=False):
        import contextlib
        import io
        import test_historical_investigation as fixtures
        from scripts import investigate_attendance as cli
        h = fixtures.InvestigationTests()
        h.setUp()
        self.addCleanup(h.doCleanups)
        # A production-labelled synthetic contract must still require an exact
        # acknowledgement and independently pinned release; no real URL is used.
        h.policy.update(destination_environment='production')
        h.policy['verification']['read_only_api_verified'] = True
        if missing_visibility:
            h.policy['verification']['unfiltered_employee_and_checkin_visibility'] = False
        args = h.cli_args()
        canonical = h.root / 'canonical' / 'live.sqlite3'
        canonical.parent.mkdir()
        shutil.copyfile(h.path, canonical)
        activation_path(canonical).write_text('SYNTHETIC canonical activation')
        directory = h.root / 'release'
        directory.mkdir()
        contract_path = h.root / 'input' / 'contract.json'
        path, sha, _ = authorization(directory, canonical, h.policy['site_url'], recovery.file_hash(contract_path))
        args.extend(['--production-read-only', '--acknowledge-production', h.policy['site_url'],
            '--reconciliation-release', str(path), '--reconciliation-release-sha256', sha,
            '--canonical-ledger', str(canonical)])
        before = h.path.read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.dict('os.environ',
                INVESTIGATION_API_KEY='SYNTHETIC', INVESTIGATION_API_SECRET='SYNTHETIC'):
            code = cli.main(args, request=h.request, acl_check=lambda _: None,
                            runner=lambda task, options, **_: task(**options))
        self.assertEqual(h.path.read_bytes(), before)
        summary = json.loads(next((h.root / 'output').glob('investigation_summary_*.json')).read_text())
        return h, code, summary

    def test_approved_production_investigation_compares_without_post(self):
        h, code, summary = self.production_investigation()
        self.assertEqual(code, 0)
        self.assertEqual(summary['totals']['potentially_missing'], 1)
        self.assertNotIn('compatibility_status', summary)
        self.assertFalse(summary['recovery_authorized'])
        self.assertTrue(h.calls)
        self.assertTrue(all(call[0] == 'GET' for call in h.calls))

    def test_release_never_overrides_unknown_visibility(self):
        h, code, summary = self.production_investigation(missing_visibility=True)
        self.assertEqual(code, 2)
        self.assertEqual(summary['totals']['potentially_missing'], 0)
        self.assertEqual(h.calls, [])


@unittest.skipUnless(Path(OPENSSL).is_file(), 'Existing OpenSSL required for disposable TLS certificate')
class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='recovery-local-tls-')
        cls.root = Path(cls.temp.name)
        config = cls.root / 'request.cnf'
        config.write_text('[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n')
        subprocess.run([OPENSSL, 'req', '-quiet', '-x509', '-newkey', 'rsa:2048', '-noenc', '-days', '1',
            '-config', str(config), '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-keyout', str(cls.root / 'key.pem'),
            '-out', str(cls.root / 'cert.pem')], check=True, capture_output=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def server(self, backend=None):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_request('GET')

            def do_POST(self):
                self.handle_request('POST')

            def handle_request(self, method):
                from urllib.parse import parse_qs, urlsplit
                calls.append(method)
                if backend:
                    parts = urlsplit(self.path)
                    options = dict(params={k: int(v[0]) if k in ('limit_start', 'limit_page_length') else v[0]
                                           for k, v in parse_qs(parts.query).items()})
                    if method == 'POST':
                        options['json'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    result = backend.request(method, backend.url + parts.path, **options)
                    body, status = result.content, result.status_code
                else:
                    body, status = b'{"message":"synthetic-only"}', 200
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    if self.path.startswith('/api/trickle'):
                        for byte in body:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(.025)
                    else:
                        self.wfile.write(body)
                except (OSError, ssl.SSLError):
                    pass  # Expected peer closure on cancellation/deadline.
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.root / 'cert.pem', self.root / 'key.pem')
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(cleanup)
        client_context = ssl.create_default_context(cafile=str(self.root / 'cert.pem'))
        url = 'https://localhost:' + str(server.server_port)
        return url, client_context, calls

    def test_real_tls_json_and_no_dns_proxy_or_redirect(self):
        url, context, calls = self.server()
        client = HTTPS(url, '127.0.0.1', context=context)
        with mock.patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            result = client.request('GET', url + '/api/test', allow_redirects=False, _remaining=1)
        self.assertEqual(result.json(), dict(message='synthetic-only'))
        self.assertEqual(calls, ['GET'])
        for method, destination, redirect in [('POST', 'https://other.example.test/api/', False),
                                              ('DELETE', url + '/api/test', False),
                                              ('GET', url + '/api/test', True)]:
            with self.assertRaises(ValueError):
                client.request(method, destination, allow_redirects=redirect)
        self.assertEqual(calls, ['GET'])

    def test_trickling_body_is_interrupted_at_absolute_deadline(self):
        url, context, calls = self.server()
        client = HTTPS(url, '127.0.0.1', context=context)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            client.request('GET', url + '/api/trickle', allow_redirects=False, _remaining=.15)
        self.assertLess(time.monotonic() - started, .6)
        self.assertEqual(calls, ['GET'])

    def test_inflight_cancellation_closes_socket_without_retry(self):
        url, context, calls = self.server()
        stopped = threading.Event()
        client = HTTPS(url, '127.0.0.1', stopped=stopped.is_set, context=context)
        timer = threading.Timer(.12, stopped.set)
        timer.start()
        self.addCleanup(timer.join)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            client.request('GET', url + '/api/trickle', allow_redirects=False, _remaining=1)
        self.assertLess(time.monotonic()-started, .6)
        self.assertEqual(calls, ['GET'])

    def test_untrusted_certificate_and_disabled_verification_are_refused(self):
        url, _, calls = self.server()
        client = HTTPS(url, '127.0.0.1')
        with self.assertRaises(ssl.SSLError):
            client.request('POST', url + '/api/test', allow_redirects=False, _remaining=1)
        self.assertEqual(calls, [])
        with self.assertRaises(ValueError):
            HTTPS(url, '127.0.0.1', context=ssl._create_unverified_context())

    def test_budget_passes_absolute_remaining_to_service_transport(self):
        url, context, _ = self.server()
        client = HTTPS(url, '127.0.0.1', context=context)
        budget = Budget(client.request, dict(rate=100), stopped=lambda: False)
        budget.end = time.monotonic() + .15
        with self.assertRaises(TimeoutError):
            budget.request('GET', url + '/api/trickle')
        self.assertEqual(budget.used, 1)

    def test_real_manager_http_to_existing_service_cycle_and_verified_tls_result(self):
        """Real browser HTTP, existing polling cycle, durable intent and real TLS.

        Only device SDK reads, Windows ACL attestations and the destination's
        business database are synthetic. No service registration is performed.
        """
        import http.cookiejar
        import re
        from types import SimpleNamespace
        from urllib.parse import urlencode
        from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request
        from urllib.error import HTTPError
        from werkzeug.serving import make_server, WSGIRequestHandler
        from durable_startup import destination_key
        from durable_sync import run
        from historical_attendance.recovery_simulation import SimulationTarget
        from sync_ledger import Ledger
        import test_manager_service_recovery as fixtures

        h = fixtures.OperatorRecoveryTests('test_preview_selection_approval_dispatch_service_result_end_to_end')
        h.setUp()
        self.addCleanup(h.doCleanups)
        backend = SimulationTarget(h.values['simulation'], max_requests=10000)
        url, context, calls = self.server(backend)
        contract_path = Path(h.values['contract'])
        contract = json.loads(contract_path.read_text())
        contract.update(site_url=url, destination_environment='staging')
        contract_path.write_text(json.dumps(contract))
        contract_hash = recovery.file_hash(contract_path)
        summary_path = Path(h.values['recovery_summary'])
        summary = json.loads(summary_path.read_text())
        summary.update(contract_sha256=recovery.digest(contract), contract_file_sha256=contract_hash)
        summary_path.write_text(json.dumps(summary))
        marker = activation_path(h.live)
        activation = json.loads(marker.read_text())
        activation['destination_sha256'] = destination_key(url, 15)
        marker.write_text(json.dumps(activation))
        release_dir = h.root / 'release'
        release_dir.mkdir()
        release_path, release_hash, _ = authorization(release_dir, h.live, url, contract_hash)
        h.values.update(contract_hash=contract_hash, recovery_summary_hash=recovery.file_hash(summary_path),
                        recovery_release=str(release_path), recovery_release_hash=release_hash, site=url)
        h.state.values.update(h.values)
        from manager_app.web import create_app
        preview = h.state.recovery_preview
        h.app = create_app(h.controller, h.values)
        h.state = h.app.extensions['manager']
        h.state.recovery_preview = preview
        self.addCleanup(h.state.close)

        class Quiet(WSGIRequestHandler):
            def log(self, *args, **kwargs):
                pass
        server = make_server('127.0.0.1', 8765, h.app, request_handler=Quiet)
        receiver = threading.Thread(target=server.serve_forever, daemon=True)
        receiver.start()
        def close_manager():
            server.shutdown()
            server.server_close()
            receiver.join(5)
        self.addCleanup(close_manager)
        browser = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        origin = 'http://127.0.0.1:8765'
        with self.assertRaises(HTTPError) as denied:
            browser.open(origin + '/recovery')
        self.assertEqual(denied.exception.code, 403)
        entry = h.app.extensions['operator_entry']
        with browser.open(origin + '/unlock?token=' + entry) as response:
            page = response.read().decode()
        with self.assertRaises(HTTPError) as replay:
            browser.open(origin + '/unlock?token=' + entry)
        self.assertEqual(replay.exception.code, 403)
        csrf = re.search('name="csrf" value="([^"]+)"', page).group(1)
        def post(path, **fields):
            data = dict(csrf=csrf, **fields)
            request = Request(origin + path, urlencode(data, doseq=True).encode(), headers={'Origin': origin})
            with browser.open(request, timeout=15) as response:
                response.read()
            h.state.thread.join(20)
            self.assertFalse(h.state.thread.is_alive())
            self.assertEqual(h.state.job['status'], 'complete', h.state.job)
        post('/recovery/service/prepare', ids=['1'], receipt='SYNTHETIC independent review',
             preview_hash=h.values['backup_hash'])
        identity = h.state.job['destination'].rsplit('/', 1)[1]
        post('/recovery/service/' + identity + '/approve', ids=['1'], confirmed='1',
             fingerprint=recovery.file_hash(h.inbox / (identity+'.draft.json')), reference='SYNTHETIC approval')
        self.assertEqual(list(h.inbox.glob('*.request.json')), [])
        post('/recovery/service/' + identity + '/dispatch', confirmed='1',
             fingerprint=recovery.file_hash(h.inbox / (identity+'.approved.json')), reference='SYNTHETIC dispatch')
        self.assertEqual(calls, [])  # Every operator step is file-only, even over HTTP.
        self.assertEqual(h.live.read_bytes(), h.live_original)

        config = SimpleNamespace(ERPNEXT_URL=url, ERPNEXT_VERSION=15, ERPNEXT_API_KEY='SYNTHETIC',
            ERPNEXT_API_SECRET='SYNTHETIC', ERPNEXT_REQUEST_TIMEOUT=10, IMPORT_START_DATE='20261011',
            DURABLE_DB_PATH=str(h.live), PULL_FREQUENCY=60, DURABLE_REQUESTS_PER_SECOND=10000,
            HISTORICAL_RECOVERY_COORDINATION=True, HISTORICAL_RECOVERY_ENABLED=True,
            HISTORICAL_RECOVERY_INBOX=str(h.inbox), HISTORICAL_RECOVERY_CONTRACT_SHA256=contract_hash,
            HISTORICAL_RECOVERY_RELEASE=str(release_path), HISTORICAL_RECOVERY_RELEASE_SHA256=release_hash)
        config.devices = [dict(device_id=f'FP{1 if i < 8 else 2}_DEVICE_{i:02}', ip='127.0.0.1',
            port=4370, password=0, clear_from_device_on_fetch=False, punch_direction=None) for i in range(1, 13)]
        poll_calls = []
        def fetch(ip, **options):
            self.assertFalse(options['clear_from_device_on_fetch'])
            poll_calls.append(options['device_id'])
            return [dict(user_id='100', timestamp=dt.datetime(2026, 10, 12, 9, 0, len(poll_calls)),
                         uid=900+len(poll_calls), punch=0, status=1)]
        client = HTTPS(url, '127.0.0.1', context=context)
        engine = SimpleNamespace(config=config, validate_unique_device_ids=lambda _: None,
            normalize_device_config=lambda d: d, device_punch_values_IN=[0, 4], device_punch_values_OUT=[1, 5],
            get_all_attendance_from_device=fetch, get_dump_file_name_and_directory=lambda _: h.root / 'absent.dump',
            requests=SimpleNamespace(request=client.request), info_logger=mock.Mock(), error_logger=mock.Mock(),
            status=SimpleNamespace(usable=False))
        with mock.patch('durable_sync.private_storage'), mock.patch('historical_attendance.service_recovery.private_storage', h.controller.storage_check), \
             mock.patch('historical_attendance.https_transport.ssl.create_default_context', return_value=context), \
             mock.patch('historical_attendance.release.private_storage', h.controller.storage_check):
            # service_tick's default ACL callback is bound at definition time.
            import historical_attendance.service_recovery as lane
            original_tick = lane.service_tick
            def tick(*args, **kwargs):
                return original_tick(*args, storage_check=h.controller.storage_check, **kwargs)
            with mock.patch.object(lane, 'service_tick', side_effect=tick):
                run(engine)
                first_posts = calls.count('POST')
                run(engine)  # Restart/tick reopens the ledger; no historical repost.
        self.assertEqual(len(set(poll_calls)), 12)
        self.assertEqual(first_posts, 15)  # One selected recovery + 12 new + 2 unselected live rows.
        self.assertEqual(calls.count('POST'), first_posts)
        engine.error_logger.error.assert_not_called()
        with Ledger(h.live, readonly=True) as ledger:
            self.assertEqual(ledger.db.execute('SELECT status FROM sr_items WHERE request_id=?', (identity,)).fetchone()[0], 'Recovered')
            self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM sr_intents').fetchone()[0], 1)
            self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM device_state').fetchone()[0], 12)
        with browser.open(origin + '/recovery/service/' + identity) as response:
            self.assertIn(b'Recovered', response.read())
        post('/recovery/service/' + identity + '/export', confirmed='1')
        report = next(Path(h.values['output']).glob('recovery_service_*.json'))
        self.assertEqual(json.loads(report.read_text())['counts'], {'Recovered': 1})

        # Release revocation must close recovery while unrelated ordinary
        # synchronization keeps working. Never clear persisted submission intents.
        release_path.write_text('{}')
        from punch_normalization import normalize
        with Ledger(h.live) as ledger:
            ledger.ingest([normalize(dict(user_id='100', timestamp=dt.datetime(2026, 10, 12, 10),
                uid=9999, punch=0, status=1), config.devices[0])], config.devices[0]['device_id'])
        with mock.patch('durable_sync.private_storage'), mock.patch.object(lane, 'service_tick', side_effect=tick):
            run(engine)
        self.assertEqual(calls.count('POST'), first_posts + 1)
        engine.error_logger.error.assert_called_once()
        with Ledger(h.live, readonly=True) as ledger:
            self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM sr_intents').fetchone()[0], 1)
        post('/recovery/service/' + identity + '/cancel', confirmed='1', reference='SYNTHETIC revoked release cancellation')
        self.assertTrue((h.inbox / (identity + '.cancel.json')).is_file())
