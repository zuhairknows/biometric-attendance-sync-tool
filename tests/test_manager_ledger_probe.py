"""Isolated reproduction of the deployed schema-open path; no production access."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from scripts import probe_manager_ledger as diagnostic
from sync_ledger import Ledger


class LedgerProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manager = Path(__file__).resolve().parents[1]
        self.source = self.root / 'ledger space # percent%.sqlite3'
        with Ledger(self.source):
            pass
        self.profile = self.root / 'profile.json'
        self.profile.write_text(json.dumps(dict(monitor_ledger=str(self.source),
            private_value='PRIVATE_SECRET')), encoding='utf-8')
        self.output = []

    def run_probe(self):
        result = diagnostic.probe(self.manager, self.profile,
            expected_ledger=self.source, emit=self.output.append)
        encoded = '\n'.join(self.output)
        for private in (str(self.root), 'PRIVATE_SECRET', 'raw unsafe error'):
            self.assertNotIn(private, encoded)
        return result, [json.loads(line) for line in self.output]

    def test_exact_constructor_accepts_user_version_zero_without_file_changes(self):
        with closing(sqlite3.connect(self.source.as_uri() + '?mode=ro', uri=True)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 0)
        before = self.source.read_bytes()
        entries = set(self.root.iterdir())
        actual_connect = sqlite3.connect
        with mock.patch.object(diagnostic.sqlite3, 'connect', wraps=actual_connect) as connect:
            result, records = self.run_probe()
        self.assertEqual(result, 0)
        self.assertTrue(all(r['status'] == 'PASS' for r in records))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), entries)
        for call in connect.call_args_list:
            if call.args[0] != ':memory:':
                self.assertTrue(call.args[0].endswith('?mode=ro'))
                self.assertEqual(call.kwargs, dict(uri=True, timeout=10))

    def test_minimal_read_and_quick_check_do_not_prove_expected_schema(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('DROP INDEX due_punches')
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        before = self.source.read_bytes()
        result, records = self.run_probe()
        self.assertEqual(result, 1)
        self.assertEqual(records[-1]['step'], 'exact_ledger_constructor')
        self.assertEqual(records[-1]['ledger_method'], '_validate_schema')
        self.assertEqual(records[-1]['category'], 'validation_or_access')
        self.assertTrue(all(r['status'] == 'PASS' for r in records[:-1]))
        self.assertEqual(self.source.read_bytes(), before)

    def test_extended_cantopen_in_reference_database_is_distinguished(self):
        # Simulate a constructor-only failure; this is not a claimed production cause.
        actual_connect = sqlite3.connect
        def connect(target, **kwargs):
            if target == ':memory:':
                error = sqlite3.OperationalError('raw unsafe error PRIVATE_SECRET')
                error.sqlite_errorcode = 782
                error.sqlite_errorname = 'SQLITE_CANTOPEN_FULLPATH'
                raise error
            return actual_connect(target, **kwargs)
        with mock.patch.object(diagnostic.sqlite3, 'connect', side_effect=connect):
            result, records = self.run_probe()
        self.assertEqual(result, 1)
        error = records[-1]
        self.assertEqual((error['step'], error['ledger_method']),
            ('exact_ledger_constructor', '_validate_schema'))
        self.assertEqual((error['sqlite_code'], error['sqlite_primary']), (782, 14))
        self.assertEqual(error['sqlite_name'], 'SQLITE_CANTOPEN_FULLPATH')
        self.assertTrue(all(r['status'] == 'PASS' for r in records[:-1]))

    def test_missing_source_is_not_created(self):
        self.source.unlink()
        result, records = self.run_probe()
        self.assertEqual(result, 1)
        self.assertFalse(self.source.exists())
        self.assertEqual(records[1]['step'], 'manager_uri_connect')
        self.assertEqual(records[1]['sqlite_primary'], 14)
        self.assertEqual(records[-1]['ledger_method'], '__init__')

    def test_wrong_target_fails_before_open(self):
        self.profile.write_text(json.dumps(dict(monitor_ledger=str(self.root / 'other.sqlite3'))))
        with mock.patch.object(diagnostic.sqlite3, 'connect') as connect:
            result, records = self.run_probe()
        connect.assert_not_called()
        self.assertEqual(result, 1)
        self.assertEqual(records[-1]['step'], 'profile_and_source_binding')

    def test_unreviewed_module_is_never_executed(self):
        self.manager = self.root / 'unreviewed'
        self.manager.mkdir()
        (self.manager / 'sync_ledger.py').write_text('raise AssertionError("PRIVATE_SECRET")')
        with mock.patch.object(diagnostic.sqlite3, 'connect') as connect:
            result, records = self.run_probe()
        connect.assert_not_called()
        self.assertEqual(result, 1)
        self.assertEqual(records[-1]['step'], 'profile_and_source_binding')

    def test_reserved_service_writer_allows_schema_read_without_writer_change(self):
        before = self.source.read_bytes()
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('UPDATE destination_state SET prefer_retry=1 WHERE id=1')
            result, records = self.run_probe()
            self.assertEqual(result, 0)
            writer.rollback()
        self.assertEqual(self.source.read_bytes(), before)

    def test_no_arguments_returns_safe_failure(self):
        with mock.patch('builtins.print') as output:
            self.assertEqual(diagnostic.main([]), 1)
        output.assert_called_once_with('{"step":"arguments","status":"FAIL"}')


if __name__ == '__main__':
    unittest.main()
