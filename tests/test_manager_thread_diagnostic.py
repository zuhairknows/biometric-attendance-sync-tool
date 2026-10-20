"""Windows main-thread signal and actual State workers; synthetic SQLite only."""
import json
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import unittest
from unittest import mock

from manager_app.demo import create_demo
from manager_thread_diagnostic_fixture import ThreadOpenDiagnostic, install


@unittest.skipUnless(sys.platform == 'win32', 'Windows console/main-thread diagnostic')
class ThreadDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temporary, self.controller, self.values = create_demo(monitor_only=True)
        self.addCleanup(temporary.cleanup)
        from manager_app.web import create_app
        self.app = create_app(self.controller, self.values, monitor_only=True)
        self.state = self.app.extensions['manager']
        self.addCleanup(self.state.close)
        self.source = Path(self.values['monitor_ledger'])
        self.before = self.source.read_bytes()

    def finish(self):
        self.state.thread.join(5)
        self.assertFalse(self.state.thread.is_alive())
        reports = list(Path(self.values['diagnostics_directory']).glob('sqlite-thread-open-*.jsonl'))
        self.assertEqual(len(reports), 1)
        records = [json.loads(line) for line in reports[0].read_text().splitlines()]
        for record in records:
            self.assertEqual(set(record), {'operation', 'result', 'sqlite_code', 'sqlite_primary'})
        text = json.dumps(records)
        for private in ('PRIVATE_SECRET', str(self.source), 'SELECT', 'traceback', '2026-10-11'):
            self.assertNotIn(private, text)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertIsNone(self.state.dashboard)  # No health result was published.
        return records

    def test_real_windows_main_signal_then_worker_opens_are_serial_and_one_shot(self):
        actual_connect = sqlite3.connect
        opened, active = [], []
        class Connection(sqlite3.Connection):
            def close(db):
                super().close()
                active.remove(db)
        def connect(target, **options):
            self.assertEqual(options, dict(uri=True, timeout=10))
            self.assertEqual(target, self.source.as_uri() + '?mode=ro')
            self.assertEqual(active, [])
            opened.append(threading.current_thread() is threading.main_thread())
            db = actual_connect(target, **options, factory=Connection)
            # There must be no queries, including attempts at writes or pragmas.
            db.set_authorizer(lambda *args: sqlite3.SQLITE_DENY)
            active.append(db)
            return db
        previous = signal.getsignal(signal.SIGBREAK)
        with mock.patch('manager_thread_diagnostic_fixture.sqlite3.connect', side_effect=connect), \
                mock.patch('builtins.print'):
            restore = install(self.state)
            try:
                signal.raise_signal(signal.SIGBREAK)  # CRT signal, not a console-wide broadcast.
                records = self.finish()
                signal.raise_signal(signal.SIGBREAK)
            finally:
                restore()
        self.assertIs(signal.getsignal(signal.SIGBREAK), previous)
        self.assertEqual(opened, [True, False])
        self.assertEqual(active, [])
        self.assertEqual([r['operation'] for r in records], ['main_open', 'worker_open'])
        self.assertEqual([r['result'] for r in records], ['passed', 'passed'])

    def test_worker_only_failure_retains_primary_and_extended_codes(self):
        actual = sqlite3.connect
        def connect(target, **options):
            if threading.current_thread() is not threading.main_thread():
                error = sqlite3.OperationalError('PRIVATE_SECRET ' + str(self.source))
                error.sqlite_errorcode = 782
                raise error
            return actual(target, **options)
        with mock.patch('manager_thread_diagnostic_fixture.sqlite3.connect', side_effect=connect):
            ThreadOpenDiagnostic(self.state).trigger()
            records = self.finish()
        self.assertEqual(records[0]['result'], 'passed')
        self.assertEqual(records[1], dict(operation='worker_open', result='failed', sqlite_code=782, sqlite_primary=14))

    def test_identical_failures_are_reported_without_retry(self):
        error = sqlite3.OperationalError('PRIVATE_SECRET')
        error.sqlite_errorcode = 14
        with mock.patch('manager_thread_diagnostic_fixture.sqlite3.connect', side_effect=error) as connect:
            ThreadOpenDiagnostic(self.state).trigger()
            records = self.finish()
        self.assertEqual(connect.call_count, 2)
        self.assertEqual([r['result'] for r in records], ['failed', 'failed'])
        self.assertEqual([r['sqlite_code'] for r in records], [14, 14])

    def test_busy_job_has_no_additional_connection_and_binding_change_blocks_worker(self):
        self.state.lock.acquire()
        diagnostic = ThreadOpenDiagnostic(self.state)
        try:
            with mock.patch('manager_thread_diagnostic_fixture.sqlite3.connect') as opened, mock.patch('builtins.print'):
                diagnostic.trigger()
            opened.assert_not_called()
        finally:
            self.state.lock.release()
        actual = sqlite3.connect
        def changed(target, **options):
            db = actual(target, **options)
            self.state.values['monitor_ledger'] = str(self.source.parent / 'PRIVATE_SECRET.sqlite3')
            return db
        with mock.patch('manager_thread_diagnostic_fixture.sqlite3.connect', side_effect=changed) as opened:
            diagnostic.trigger()
            records = self.finish()
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(records[1]['result'], 'unavailable')

    def test_private_output_failure_and_non_main_trigger_never_open_database(self):
        diagnostic = ThreadOpenDiagnostic(self.state)
        with mock.patch('manager_thread_diagnostic_fixture.sqlite3.connect') as opened:
            thread = threading.Thread(target=diagnostic.trigger)
            thread.start()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertIsNone(self.state.thread)
            self.state.values['diagnostics_directory'] = str(self.source.parent)
            with mock.patch('builtins.print'):
                diagnostic.trigger()
            opened.assert_not_called()

    def test_comparison_cannot_be_constructed_without_the_synthetic_sandbox(self):
        self.controller.sandbox_root = None
        with mock.patch('manager_thread_diagnostic_fixture.sqlite3.connect') as opened:
            with self.assertRaises(ValueError):
                ThreadOpenDiagnostic(self.state)
            opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
