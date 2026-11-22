"""Dashboard snapshot refresh: read-only copy of the ledger, secrets removed. Temporary files only."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('monitor_snapshot_refresh', ROOT / 'deployment' / 'monitor_snapshot_refresh.py')
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class MonitorSnapshotRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='biometric-snapshot-tests-')
        self.dir = Path(self.temp.name)
        self.live = self.dir / 'live.sqlite3'
        db = sqlite3.connect(self.live)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE punches (id INTEGER PRIMARY KEY, state TEXT)')
        db.executemany('INSERT INTO punches (state) VALUES (?)', [('synced',)] * 3)
        db.commit()
        db.close()
        self.config = self.dir / 'local_config.py'
        self.config.write_text(
            "﻿ERPNEXT_API_KEY = 'SECRET-KEY'\nERPNEXT_API_SECRET = 'SECRET-SECRET'\n"
            f"DURABLE_DB_PATH = {str(self.live)!r}\nIMPORT_START_DATE = '20260910'\n"
            "devices = [{'device_id': 'FP1_DEVICE_01', 'ip': '192.0.2.10', 'port': 4371, 'password': 1234}]\n"
            "SHIFT_SYNC_IGNORED_DEVICES = ['FP1_DEVICE_06']\n"
            "import os\nDERIVED = os.getcwd()\n", encoding='utf-8')
        self.out = self.dir / 'snapshot'
        self.out.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_refresh(self):
        code = refresh.main(['x', str(self.config), str(self.out)])
        return code, json.loads((self.out / 'refresh-status.json').read_text(encoding='utf-8'))

    def test_copies_ledger_and_strips_secrets(self):
        code, status = self.run_refresh()
        self.assertEqual((code, status['ok'], status['punches'], status['quick_check']), (0, True, 3, 'ok'))
        copy = sqlite3.connect(self.out / 'sync.sqlite3')
        self.assertEqual(copy.execute('PRAGMA journal_mode').fetchone()[0].lower(), 'delete')
        self.assertEqual(copy.execute('SELECT COUNT(*) FROM punches').fetchone()[0], 3)
        copy.close()
        text = (self.out / 'local_config.monitor.py').read_text(encoding='utf-8')
        for secret in ('SECRET', 'password', '1234', 'DERIVED', 'import'):
            self.assertNotIn(secret, text)
        values = {}
        exec(compile(text, 'monitor', 'exec'), {}, values)
        self.assertEqual(values['devices'], [{'device_id': 'FP1_DEVICE_01', 'ip': '192.0.2.10', 'port': 4371}])
        self.assertEqual(values['SHIFT_SYNC_IGNORED_DEVICES'], ['FP1_DEVICE_06'])
        self.assertFalse(list(self.out.glob('.staging*')))

    def test_refresh_replaces_copy_and_leaves_live_ledger_unchanged(self):
        self.run_refresh()
        db = sqlite3.connect(self.live)
        db.execute("INSERT INTO punches (state) VALUES ('pending')")
        db.commit()
        db.close()
        before = self.live.read_bytes()
        code, status = self.run_refresh()
        self.assertEqual((code, status['punches']), (0, 4))
        self.assertEqual(self.live.read_bytes(), before)

    def test_failure_is_recorded_and_keeps_previous_copy(self):
        self.run_refresh()
        self.config.write_text(f"DURABLE_DB_PATH = {str(self.dir / 'missing.sqlite3')!r}\n", encoding='utf-8')
        code, status = self.run_refresh()
        self.assertEqual((code, status['ok']), (1, False))
        self.assertIn('OperationalError', status['error'])
        copy = sqlite3.connect(self.out / 'sync.sqlite3')
        self.assertEqual(copy.execute('SELECT COUNT(*) FROM punches').fetchone()[0], 3)
        copy.close()


if __name__ == '__main__':
    unittest.main()
