"""Real loopback HTTP/Waitress/State tests in a cold Windows manager process.

Only the parent prepares SQLite fixtures. The child cannot open any other source
or connect off loopback. No production profile, ledger, service or device is used.
"""
import http.cookiejar
from html.parser import HTMLParser
import json
from pathlib import Path
import queue
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'


def fixture_server(root):
    # This branch never creates a ledger or calls create_demo: SQLite's first
    # connection in the child must come from the real HTTP refresh job.
    from manager_app.controller import Controller
    from manager_app.demo import DemoACL
    from manager_app.web import create_app
    from manager_app import runtime
    import manager_thread_diagnostic_fixture as thread_diagnostic
    from manager_app import monitoring
    from scripts import capture_history_safety
    root = Path(root).resolve()
    values = json.loads((root / 'http-profile.json').read_text(encoding='utf-8'))
    native_scm = values.pop('native_scm')
    for name in ('monitor_ledger', 'device_config', 'diagnostics_directory'):
        if not Path(values[name]).resolve().is_relative_to(root):
            raise ValueError('synthetic_paths_required')
    source = Path(values['monitor_ledger'])
    expected_uri = source.as_uri() + '?mode=ro'
    events = []
    def audit(event, args):
        if event == 'sqlite3.connect':
            if args[0] not in (expected_uri, ':memory:'):
                raise AssertionError('nonfixture_sqlite_forbidden')
            events.append(dict(source=args[0] != ':memory:', main=threading.current_thread() is threading.main_thread()))
        if event == 'socket.connect' and args[1][0] not in ('127.0.0.1', '::1'):
            raise AssertionError('external_network_forbidden')
    sys.addaudithook(audit)  # Test-process-only observation, not a connect replacement.
    capture_history_safety.SERVICE_NAME = 'EventLog'  # Read-only local SCM call; no service control.
    controller = Controller(service_query=capture_history_safety.service_state if native_scm else lambda: 'SYNTHETIC',
        acl_check=DemoACL(root), sandbox_root=root)
    app = create_app(controller, values, monitor_only=True)
    state = app.extensions['manager']
    monitoring.PROBE_TARGET = source
    original_server = runtime.create_server
    restore_signal = None

    def create_server(*args, **options):
        nonlocal restore_signal
        server = original_server(*args, **options)  # Real fixed-port Waitress listener.
        restore_signal = thread_diagnostic.install(state)
        def control():
            for command in sys.stdin:
                if command.strip() == 'compare':
                    signal.raise_signal(signal.SIGBREAK)
                    server.pull_trigger()  # Wake main select so Python delivers its signal.
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        if state.job and state.job['label'] == 'Read-only SQLite thread comparison':
                            state.thread.join(21)
                            if not state.thread.is_alive():
                                print('LOCAL_COMPARISON_DONE', flush=True)
                                break
                        time.sleep(.05)
                elif command.strip() == 'stop':
                    signal.raise_signal(signal.SIGINT)
                    server.pull_trigger()
                    break
        threading.Thread(target=control, daemon=True).start()
        print('LOCAL_HTTP_READY', flush=True)
        return server
    runtime.create_server = create_server  # Listener capture only; server.run stays on main.
    try:
        runtime.serve(app, open_browser=False)
    finally:
        if restore_signal:
            restore_signal()
        (root / 'http-thread-observations.json').write_text(json.dumps(dict(events=events,
            native_scm_success=bool(state.dashboard and state.dashboard['service'] == 'RUNNING'))), encoding='utf-8')


class CSRF(HTMLParser):
    token = ''
    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == 'input' and attributes.get('name') == 'csrf':
            self.token = attributes['value']


class LocalRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        if not newurl.startswith(ORIGIN + '/'):
            raise AssertionError('nonloopback_redirect_forbidden')
        return super().redirect_request(request, response, code, message, headers, newurl)


@unittest.skipUnless(sys.platform == 'win32', 'Real Windows manager HTTP/process test')
class ManagerHTTPSQLiteTests(unittest.TestCase):
    def wait_marker(self, marker):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if self.lines.get(timeout=.2) == marker:
                    return
            except queue.Empty:
                if self.process.poll() is not None:
                    break
        self.fail('Owned local HTTP fixture did not reach ' + marker + '; check port 8765 availability.')

    def get(self, path, data=None):
        with self.http.open(ORIGIN + path, data=data, timeout=5) as response:
            self.assertTrue(response.url.startswith(ORIGIN + '/'))
            return response.url, response.read().decode('utf-8')

    def refresh(self, expected='OK', destination='dashboard_page', verify_bytes=True):
        before = self.source.read_bytes() if verify_bytes else None
        _, page = self.get('/')
        parser = CSRF()
        parser.feed(page)
        self.assertTrue(parser.token)
        job_url, _ = self.get('/refresh', urlencode(dict(csrf=parser.token, destination=destination)).encode())
        self.assertIn('/jobs/', job_url)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            _, receipt = self.get(job_url.removeprefix(ORIGIN) + '/status')
            status = json.loads(receipt)['status']
            if status != 'running':
                self.assertEqual(status, 'complete')
                break
            time.sleep(.05)
        else:
            self.fail('Local refresh exceeded test budget')
        _, page = self.get('/' if destination == 'dashboard_page' else '/devices')
        if expected == 'OK':
            self.assertNotIn('Ledger unavailable', page)
            self.assertIn('FP1_DEVICE_01', page)
        else:
            self.assertIn(expected, page)
        if verify_bytes:
            self.assertEqual(self.source.read_bytes(), before)
        return page

    def exercise(self, native_scm=False):
        from manager_app.demo import create_demo
        temporary, controller, values = create_demo(monitor_only=True)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = Path(values['monitor_ledger'])
        if native_scm:
            renamed = self.source.with_name('synthetic space #% é.sqlite3')
            self.source.rename(renamed)
            self.source = renamed
            values['monitor_ledger'] = str(renamed)
        (root / 'http-profile.json').write_text(json.dumps(dict(values, native_scm=native_scm)), encoding='utf-8')
        # Cold process uses the actual venv interpreter, imports and main Waitress
        # loop. Parent fixture creation cannot initialize the child's SQLite.
        self.process = subprocess.Popen([sys.executable, '-u', '-B', __file__, '--fixture-server', str(root)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8')
        self.lines = queue.Queue()
        def output():
            for line in self.process.stdout:
                self.lines.put(line.strip())
        reader = threading.Thread(target=output, daemon=True)
        reader.start()
        def cleanup():
            if self.process.poll() is None:
                try:
                    self.process.stdin.write('stop\n')
                    self.process.stdin.flush()
                except OSError:
                    pass  # The owned child may have exited after a startup failure.
                try:
                    self.process.wait(10)
                except subprocess.TimeoutExpired:
                    self.process.terminate()  # Only the synthetic child created by this test.
                    self.process.wait(5)
            reader.join(2)
            self.process.stdin.close()
            self.process.stdout.close()
            self.process.stderr.close()
        self.addCleanup(cleanup)
        self.wait_marker('LOCAL_HTTP_READY')  # Never attach to someone else's occupied port.
        self.http = build_opener(ProxyHandler({}), LocalRedirects(), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        for destination in ('dashboard_page', 'devices_page', 'dashboard_page'):
            self.refresh(destination=destination)
        if native_scm:
            with sqlite3.connect(str(self.source)) as writer:
                writer.execute('BEGIN IMMEDIATE')
                writer.execute("UPDATE punches SET state='retry' WHERE id=1")
                self.refresh()
                writer.rollback()
                writer.execute('BEGIN EXCLUSIVE')
                self.refresh(expected='sqlite_busy')
                writer.rollback()
            writer.close()
            self.refresh()
            # Real Windows sharing violation, not a fabricated sqlite exception:
            # hold ONLY the synthetic file read-only, denying other data opens.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle.restype = wintypes.BOOL
            before = self.source.read_bytes()
            handle = kernel.CreateFileW(str(self.source), 0x80000000, 0, None, 3, 0x80, None)
            self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
            try:
                page = self.refresh(expected='sqlite_cantopen', verify_bytes=False)
                self.assertIn('Operation: source_connect', page)
                blocked = kernel.CreateFileW(str(self.source), 0x80000000, 7, None, 3, 0x80, None)
                error_number = ctypes.get_last_error()
                if blocked != wintypes.HANDLE(-1).value:
                    kernel.CloseHandle(blocked)
                    self.fail('Synthetic sharing denial was not established')
                self.assertEqual(error_number, 32)  # ERROR_SHARING_VIOLATION, independently verified.
            finally:
                self.assertTrue(kernel.CloseHandle(handle))
            self.assertEqual(self.source.read_bytes(), before)
            failures = [json.loads(line) for report in Path(values['diagnostics_directory']).glob('monitor-*.jsonl')
                for line in report.read_text().splitlines() if 'sqlite_cantopen' in line]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]['sqlite_code'], 14)
            self.assertTrue(all(value is True for value in failures[0]['source_binding'].values()))
            self.refresh()  # Release alone restores service; no app/database correction.
        for path in ('/investigation', '/recovery'):
            with self.assertRaises(HTTPError) as denied:
                self.get(path)
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()
        self.process.stdin.write('compare\n')
        self.process.stdin.flush()
        self.wait_marker('LOCAL_COMPARISON_DONE')
        reports = list(Path(values['diagnostics_directory']).glob('sqlite-thread-open-*.jsonl'))
        self.assertEqual(len(reports), 1)
        pair = [json.loads(line) for line in reports[0].read_text().splitlines()]
        self.assertEqual([r['operation'] for r in pair], ['main_open', 'worker_open'])
        self.assertEqual([r['result'] for r in pair], ['passed', 'passed'])
        self.refresh()
        self.process.stdin.write('stop\n')
        self.process.stdin.flush()
        self.assertEqual(self.process.wait(10), 0)
        observed = json.loads((root / 'http-thread-observations.json').read_text())
        self.assertEqual(observed['events'][:2], [dict(source=True, main=False), dict(source=False, main=False)])
        self.assertEqual(sum(e['main'] for e in observed['events']), 1)  # Only the explicit main open.
        if native_scm:
            self.assertTrue(observed['native_scm_success'], 'Local EventLog read-only SCM query was not verified')

    def test_cold_manager_first_open_is_http_worker_then_main_worker_pair(self):
        self.exercise()

    def test_native_scm_unicode_uri_and_concurrent_delete_journal_writer(self):
        self.exercise(native_scm=True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--fixture-server':
        fixture_server(sys.argv[2])
    else:
        unittest.main()
