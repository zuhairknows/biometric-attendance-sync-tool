"""Synthetic live-ledger monitoring; no SCM, production configuration or network."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest import mock

from manager_app.demo import create_demo
from sync_ledger import Ledger
from historical_attendance.diagnostics import STAGES, CODES
from historical_attendance.diagnostics import Trace
from historical_attendance.diagnostics import MONITOR_OPEN_OPERATIONS, DiagnosticError
from historical_attendance.diagnostics import MONITOR_SOURCE_BINDING_FIELDS
from manager_app.monitoring import _OPEN_STATEMENTS


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.controller, self.values = create_demo(monitor_only=True)
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.values['monitor_ledger'])
        self.logs = Path(self.temp.name) / 'private-diagnostics'
        self.logs.mkdir()
        self.values['diagnostics_directory'] = str(self.logs)

    def refresh(self):
        return self.controller.dashboard(str(self.source), values=self.values)

    def records(self):
        records = [json.loads(line) for p in self.logs.glob('monitor-*.jsonl') for line in p.read_text().splitlines()]
        self.assertTrue(records)
        for r in records:
            expected = {'reference', 'stage', 'code', 'event'}
            if 'operation' in r:
                expected |= {'operation', 'sqlite_code', 'sqlite_primary'}
                self.assertEqual((r['stage'], r['event']), ('monitor_open_schema', 'failed'))
                self.assertIn(r['operation'], MONITOR_OPEN_OPERATIONS)
                self.assertIs(type(r['sqlite_code']), int)
                self.assertEqual(r['sqlite_primary'], r['sqlite_code'] & 255)
            if 'source_binding' in r:
                expected.add('source_binding')
                self.assertEqual(r['operation'], 'source_connect')
                self.assertEqual(set(r['source_binding']), MONITOR_SOURCE_BINDING_FIELDS)
                self.assertTrue(all(v is None or type(v) is bool for v in r['source_binding'].values()))
            self.assertEqual(set(r), expected)
            self.assertIn(r['stage'], STAGES)
            self.assertIn(r['code'], CODES)
        encoded = json.dumps(records)
        for private in (str(self.source), 'PRIVATE_SECRET', 'SYNTHETIC_EMP', '2026-10-11', 'source_evidence'):
            self.assertNotIn(private, encoded)
        return records

    def assert_failure(self, result, stage, code):
        self.assertEqual(result['integrity'], 'Unavailable')
        self.assertTrue(any(stage + ' / ' + code in s for s in result['errors']))
        self.assertTrue(any(r['stage'] == stage and r['code'] == code and r['event'] == 'failed' for r in self.records()))

    def test_success_logs_only_allowlisted_fields_and_uses_readonly_ledger(self):
        before = self.source.read_bytes()
        queries = []
        def opened(path, readonly=False):
            self.assertTrue(readonly)
            ledger = Ledger(path, readonly=readonly)
            ledger.db.set_trace_callback(queries.append)
            return ledger
        with mock.patch('manager_app.controller.Ledger', side_effect=opened), mock.patch('durable_startup.worker_lock', side_effect=AssertionError('no writer mutex')):
            result = self.refresh()
        self.assertEqual(result['integrity'], 'OK')
        self.assertEqual(result['errors'], [])
        self.assertTrue(any(r['event'] == 'complete' for r in self.records()))
        self.assertFalse(any(q.startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP')) for q in queries))
        self.assertEqual(self.source.read_bytes(), before)

    def test_each_constructor_sqlite_operation_preserves_extended_code_and_privacy(self):
        import gc
        self.addCleanup(gc.collect)  # Fault-injected cursor tracebacks can retain fixture handles.
        # Fail at actual Ledger statements, rather than fabricating traceback frames.
        # The injected errors reproduce locations, not the unproven production cause.
        cases = [
            ('source_connect', 'connect', False, ''),
            ('reference_connect', 'connect', True, ''),
            ('source_busy_timeout', 'execute', False, 'PRAGMA busy_timeout=10000'),
            ('source_foreign_keys', 'execute', False, 'PRAGMA foreign_keys=ON'),
            ('source_tables', 'execute', False, "SELECT name FROM sqlite_master WHERE type='table'"),
            ('source_schema_version', 'execute', False, 'SELECT version FROM schema_version'),
            ('reference_schema_build', 'execute', True, 'CREATE TABLE schema_version (version INTEGER NOT NULL)'),
            ('reference_schema_read', 'fetchall', True, 'SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL'),
            ('source_schema_compare', 'fetchone', False, 'SELECT sql FROM sqlite_master WHERE type=? AND name=?'),
            ('source_destination_state', 'fetchall', False, 'SELECT id,cooldown_until,prefer_retry,prefer_recent FROM destination_state'),
            ('reference_close', 'close', True, ''),
            ('source_close_on_error', 'close', False, ''),
        ]
        actual_connect = sqlite3.connect
        before, entries = self.source.read_bytes(), set(self.source.parent.iterdir())
        for operation, call, reference, statement in cases:
            with self.subTest(operation=operation):
                def fail():
                    error = sqlite3.OperationalError('PRIVATE_SECRET SYNTHETIC_EMP 2026-10-11 ' + str(self.source))
                    error.sqlite_errorcode = 782
                    error.sqlite_errorname = 'SQLITE_CANTOPEN_FULLPATH'
                    raise error

                class Cursor:
                    def __getattr__(cursor, name):
                        if name == call:
                            fail()
                        return getattr(cursor.actual, name)

                class Connection(sqlite3.Connection):
                    def execute(db, sql, *args):
                        # Force initialization to unwind so its source close can fail.
                        if operation == 'source_close_on_error' and not db.reference:
                            raise ValueError('PRIVATE_SECRET')
                        targeted = db.reference == reference and sql == statement
                        if targeted and call == 'execute':
                            fail()
                        result = super().execute(sql, *args)
                        if targeted and call.startswith('fetch'):
                            cursor = Cursor()
                            cursor.actual = result
                            return cursor
                        return result

                    def close(db):
                        super().close()
                        if call == 'close' and db.reference == reference:
                            fail()

                opened = []
                def connect(target, **options):
                    is_reference = target == ':memory:'
                    opened.append(target)
                    if not is_reference:
                        self.assertEqual(options, dict(uri=True, timeout=10))
                        self.assertTrue(target.endswith('?mode=ro'))
                    if call == 'connect' and is_reference == reference:
                        fail()
                    db = actual_connect(target, **options, factory=Connection)
                    db.reference = is_reference
                    return db

                with mock.patch('sync_ledger.sqlite3.connect', side_effect=connect):
                    result = self.refresh()
                self.assert_failure(result, 'monitor_open_schema', 'sqlite_cantopen')
                record = [r for r in self.records() if r['event'] == 'failed'
                    and r['reference'] in str(result['errors'])][-1]
                self.assertEqual((record['operation'], record['sqlite_code'], record['sqlite_primary']),
                    (operation, 782, 14))
                self.assertIn('Operation: ' + operation, str(result['errors']))
                self.assertIn('SQLite code: 782 (primary 14)', str(result['errors']))
                self.assertNotIn('PRIVATE_SECRET', str(result))
                self.assertLessEqual(len(opened), 2)  # No diagnostic/retry connection.
                self.assertEqual(self.source.read_bytes(), before)
                self.assertEqual(set(self.source.parent.iterdir()), entries)

    def test_statement_labels_are_bound_to_the_reviewed_ledger_source(self):
        import inspect
        for function, statements in _OPEN_STATEMENTS:
            lines = {s.strip() for s in inspect.getsource(function).splitlines()}
            self.assertTrue(set(statements) <= lines)
            self.assertTrue(set(statements.values()) <= MONITOR_OPEN_OPERATIONS)

    def test_probe_target_binding_is_the_same_as_the_existing_standalone_probe(self):
        from scripts.probe_manager_ledger import TARGET
        from manager_app.monitoring import PROBE_TARGET
        self.assertEqual(PROBE_TARGET, TARGET)

    def test_real_source_connect_failure_reports_same_target_without_another_connection(self):
        # A missing fixture produces a genuine SQLite CANTOPEN, without wrapping
        # sqlite3.connect (which the diagnostic would correctly flag as overridden).
        self.source.rename(self.source.with_suffix('.parked'))
        entries = set(self.source.parent.iterdir())
        with mock.patch('manager_app.monitoring.PROBE_TARGET', self.source):
            result = self.refresh()
        record = [r for r in self.records() if r['event'] == 'failed'][-1]
        self.assertEqual(record['sqlite_code'], 14)
        flags = record['source_binding']
        self.assertEqual(flags, {name: name != 'background_thread' for name in MONITOR_SOURCE_BINDING_FIELDS})
        self.assertNotIn(str(self.source), str(result))
        self.assertEqual(set(self.source.parent.iterdir()), entries)

    def test_source_substitution_is_compared_using_the_actual_failed_ledger_frame(self):
        other = self.source.parent / 'PRIVATE_SECRET-other.sqlite3'
        def redirected(path, readonly):
            self.assertEqual(Path(path), self.source)
            self.assertTrue(readonly)
            return Ledger(other, readonly=True)
        with mock.patch('manager_app.controller.Ledger', side_effect=redirected), \
                mock.patch('manager_app.monitoring.PROBE_TARGET', self.source):
            result = self.refresh()
        flags = self.records()[-1]['source_binding']
        self.assertFalse(flags['argument_equals_profile'])
        self.assertFalse(flags['resolved_equals_profile'])
        self.assertFalse(flags['resolved_equals_probe_target'])
        self.assertFalse(flags['uri_equals_probe'])
        self.assertTrue(flags['readonly_argument'])
        self.assertTrue(flags['builtin_sqlite_connect'])
        self.assertNotIn('PRIVATE_SECRET', str(result))
        self.assertFalse(other.exists())

    def test_binding_metadata_cannot_log_strings_or_appear_on_other_operations(self):
        for operation, values in [('source_connect', dict.fromkeys(MONITOR_SOURCE_BINDING_FIELDS, 'PRIVATE_SECRET')),
                ('source_tables', dict.fromkeys(MONITOR_SOURCE_BINDING_FIELDS, True)),
                ('source_connect', {'path': str(self.source)})]:
            trace = Trace(self.values, self.controller.acl_check, kind='monitor')
            trace.stage = 'monitor_open_schema'
            before = trace.path.read_bytes()
            with self.assertRaises(DiagnosticError):
                trace.emit('failed', 'sqlite_cantopen', monitor_operation=operation,
                    sqlite_code=14, monitor_source=values)
            self.assertEqual(trace.path.read_bytes(), before)

    def test_unavailable_source_metadata_keeps_the_actual_sqlite_error(self):
        self.source.rename(self.source.with_suffix('.parked'))
        with mock.patch('manager_app.monitoring.inspect.getsourcelines', side_effect=OSError()):
            result = self.refresh()
        self.assert_failure(result, 'monitor_open_schema', 'sqlite_cantopen')
        self.assertEqual(self.records()[-1]['operation'], 'unknown')

    def test_monitor_metadata_is_typed_allowlisted_and_unavailable_to_offline_logs(self):
        for kind, operation, number in [('offline', 'source_connect', 14),
                ('monitor', 'PRIVATE_SECRET', 14), ('monitor', 'source_connect', 'PRIVATE_SECRET'),
                ('monitor', 'source_connect', True), ('monitor', 'source_connect', -1)]:
            with self.subTest(kind=kind, operation=operation, number=number):
                trace = Trace(self.values, self.controller.acl_check, kind=kind)
                trace.stage = 'monitor_open_schema'
                before = trace.path.read_bytes()
                with self.assertRaises(DiagnosticError):
                    trace.emit('failed', 'sqlite_cantopen', monitor_operation=operation, sqlite_code=number)
                self.assertEqual(trace.path.read_bytes(), before)

    def test_missing_database_after_boot_is_not_created_and_manual_refresh_recovers(self):
        ready = self.source.with_suffix('.parked')
        self.source.rename(ready)
        result = self.refresh()
        self.assert_failure(result, 'monitor_open_schema', 'sqlite_cantopen')
        self.assertFalse(self.source.exists())
        ready.rename(self.source)
        self.assertEqual(self.refresh()['integrity'], 'OK')

    def test_quick_check_ok_does_not_bypass_exact_schema_validation(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('DROP INDEX due_punches')
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        before = self.source.read_bytes()
        self.assert_failure(self.refresh(), 'monitor_open_schema', 'source_schema_invalid')
        self.assertEqual(self.source.read_bytes(), before)

    def test_quick_check_ok_does_not_bypass_foreign_key_validation(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("INSERT INTO delivery_attempts(punch_id,attempted_at,outcome) VALUES(99999,1,'retry')")
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        before = self.source.read_bytes()
        self.assert_failure(self.refresh(), 'monitor_integrity', 'source_foreign_keys_failed')
        self.assertEqual(self.source.read_bytes(), before)

    def test_reserved_service_writer_lock_does_not_block_readonly_dashboard(self):
        before = self.source.read_bytes()
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE punches SET state='retry' WHERE id=1")
            result = self.refresh()
            self.assertEqual((result['integrity'], result['pending'], result['retry']), ('OK', 3, 0))
            writer.rollback()
        self.assertEqual(self.source.read_bytes(), before)

    def test_exclusive_writer_timeout_is_identified_without_retry_or_source_change(self):
        before = self.source.read_bytes()
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            result = self.refresh()  # Exercise the real existing ten-second busy timeout.
            self.assert_failure(result, 'monitor_open_schema', 'sqlite_busy')
            writer.rollback()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.refresh()['integrity'], 'OK')

    def test_wal_service_writer_is_read_without_checkpoint_or_journal_mode_change(self):
        with closing(sqlite3.connect(self.source)) as writer:
            self.assertEqual(writer.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            writer.execute("UPDATE punches SET state='retry' WHERE id=1")
            writer.commit()
            self.assertTrue(Path(str(self.source)+'-wal').exists())
            before = self.source.read_bytes()
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE punches SET state='synced' WHERE id=1")
            result = self.refresh()
            self.assertEqual((result['integrity'], result['pending'], result['retry']), ('OK', 2, 1))
            self.assertEqual(writer.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(self.source.read_bytes(), before)
            writer.rollback()

    def test_committing_service_writer_observes_consistent_snapshot_and_is_released(self):
        entered, commit_started, committed = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def writer():
            try:
                with closing(sqlite3.connect(self.source, timeout=5)) as db:
                    self.assertTrue(entered.wait(5))
                    db.execute("UPDATE punches SET state='retry' WHERE id=1")
                    commit_started.set()
                    db.commit()
                    committed.set()
            except BaseException as error:
                errors.append(type(error).__name__)
                commit_started.set()
        original = Ledger.verify_integrity
        def integrity(ledger):
            result = original(ledger)  # Establish the read transaction's snapshot.
            entered.set()
            self.assertTrue(commit_started.wait(5))
            self.assertFalse(committed.is_set())
            return result
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            with mock.patch.object(Ledger, 'verify_integrity', integrity):
                result = self.refresh()
        finally:
            entered.set()
            thread.join(8)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(committed.is_set())
        self.assertEqual((result['pending'], result['retry']), (3, 0))
        self.assertEqual(self.refresh()['retry'], 1)

    def test_logging_failure_cannot_hide_ledger_failure_or_use_public_fallback(self):
        self.values['diagnostics_directory'] = str(self.source.parent)
        with mock.patch('manager_app.controller.Ledger', side_effect=PermissionError('PRIVATE_SECRET')):
            result = self.refresh()
        self.assertTrue(any('monitor_open_schema / permission_denied' in s for s in result['errors']))
        self.assertTrue(any('monitoring log unavailable' in s for s in result['errors']))
        self.assertNotIn('PRIVATE_SECRET', str(result))
        self.assertEqual(list(self.source.parent.glob('monitor-*.jsonl')), [])

    def test_unexpected_exception_text_does_not_reach_logs_or_browser(self):
        with mock.patch.object(Ledger, 'diagnostics', side_effect=RuntimeError('PRIVATE_SECRET employee attendance timestamp')):
            result = self.refresh()
        self.assert_failure(result, 'monitor_counts', 'unexpected_error')
        self.assertNotIn('PRIVATE_SECRET', str(result))

    def test_mid_refresh_log_failure_is_visible_without_corrupting_ledger_result(self):
        emit = Trace.emit
        def lost_access(trace, event, code):
            if trace.stage == 'monitor_counts':
                raise PermissionError('PRIVATE_SECRET')
            return emit(trace, event, code)
        before = self.source.read_bytes()
        with mock.patch.object(Trace, 'emit', lost_access):
            result = self.refresh()
        self.assertEqual(result['integrity'], 'OK')
        self.assertTrue(any('monitoring log unavailable (permission_denied)' in s for s in result['errors']))
        self.assertNotIn('PRIVATE_SECRET', str(result))
        self.assertEqual(self.source.read_bytes(), before)
        self.records()

    def test_relative_path_is_classified_without_creating_database(self):
        result = self.controller.dashboard('relative.sqlite3', values=self.values)
        self.assert_failure(result, 'monitor_path', 'absolute_approved_path_required')

    def test_display_failure_cannot_publish_healthy_partial_results(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('UPDATE device_state SET last_successful_delivery=1700000000')
            db.execute("UPDATE device_state SET last_successful_delivery=-1e100 WHERE device_id='FP2_DEVICE_11'")
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        original = self.source.read_bytes()
        result = self.refresh()
        self.assertTrue(result['errors'])
        self.assertEqual(result['integrity'], 'Unavailable')
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['devices'], [])
        self.assertEqual(self.source.read_bytes(), original)
        self.assert_failure(result, 'monitor_display', 'diagnostic_value_invalid')


if __name__ == '__main__':
    unittest.main()
