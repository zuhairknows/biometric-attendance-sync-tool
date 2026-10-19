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
            self.assertEqual(set(r), {'reference', 'stage', 'code', 'event'})
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
