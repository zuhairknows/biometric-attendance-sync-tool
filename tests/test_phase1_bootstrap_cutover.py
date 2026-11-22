"""The idle retry must not reuse an expired synthetic activation boundary."""
import contextlib
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import durable_startup
from scripts import phase1_staging_scm_host as host


class BootstrapCutoverTests(unittest.TestCase):
    def test_fresh_boundary_preserves_original_settings_and_second_precision(self):
        old = dict(port=12345, service_sid='S-1-5-80-1-2-3-4-5', cutover='2000-01-01 00:00:00')
        before = dict(old)
        now = dt.datetime(2026, 10, 16, 19, 30, 0, 123456)
        first = host.fresh_bootstrap_settings(old, now)
        later = host.fresh_bootstrap_settings(old, now + dt.timedelta(hours=1))
        self.assertEqual(old, before)
        self.assertIsNot(first, old)
        self.assertEqual(first['port'], old['port'])
        self.assertEqual(first['service_sid'], old['service_sid'])
        self.assertEqual(first['cutover'], '2026-10-16 19:32:00')
        self.assertEqual(later['cutover'], '2026-10-16 20:32:00')

    def test_pinned_provision_rejects_stale_boundary_and_accepts_fresh_synthetic_one(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-bootstrap-cutover-') as directory:
            root = Path(directory)
            settings = root / 'settings.json'
            settings.write_text(json.dumps(dict(port=12345, service_sid='S-1-5-80-1-2-3-4-5',
                                                cutover='2000-01-01 00:00:00')))
            original = settings.read_bytes()
            old = json.loads(original)
            ledger = root / 'synthetic.sqlite3'
            now = dt.datetime(2026, 10, 16, 19, 30)
            # No Windows mutex, ACL change, service operation or network call.
            with mock.patch.object(durable_startup, 'worker_lock', lambda _: contextlib.nullcontext()):
                with self.assertRaisesRegex(durable_startup.StartupError, '^new_activation_requires_future_cutover$'):
                    durable_startup.provision(ledger, 'https://127.0.0.1:12345', 15, old['cutover'],
                                             'SYNTHETIC TEST ONLY', storage_check=lambda _: None, now=now)
                self.assertFalse(ledger.exists())
                fresh = host.fresh_bootstrap_settings(old, now)
                result = durable_startup.provision(ledger, 'https://127.0.0.1:12345', 15, fresh['cutover'],
                                                  'SYNTHETIC TEST ONLY', storage_check=lambda _: None, now=now)
            self.assertTrue(ledger.exists())
            self.assertEqual(result['cutover_time'], fresh['cutover'])
            self.assertEqual(settings.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
