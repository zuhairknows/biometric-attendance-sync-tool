"""Regression tests for defects found in the final Phase 1 review."""
import datetime as dt
import importlib
import json
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from durable_delivery import deliver_batch, FrappeClient
from durable_sync import LegacyStatusSnapshot, update_shifts
from punch_normalization import normalize, import_boundary
from sync_ledger import Ledger
import test_durable_sync as fixtures
from test_erpnext_sync import load_sync_module, FakeZK, FakeAttendance


class ReleaseReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='phase1-release-review-')
        self.path = Path(self.temp.name) / 'review.sqlite3'
        self.ledger = Ledger(self.path)
        self.storage = mock.patch('durable_sync.private_storage')
        self.storage.start()
        self.addCleanup(self.storage.stop)

    def tearDown(self):
        import logging
        for name in list(logging.root.manager.loggerDict):
            for handler in list(logging.getLogger(name).handlers):
                handler.close()
                logging.getLogger(name).removeHandler(handler)
        self.ledger.close()
        self.temp.cleanup()

    def test_missing_table_index_and_changed_definition_fail_before_writes(self):
        for index, mutation in enumerate(('DROP TABLE shift_state', 'DROP INDEX due_punches',
                         'ALTER TABLE punches ADD COLUMN unexpected TEXT')):
            with self.subTest(mutation=mutation):
                path = Path(self.temp.name) / ('damaged-' + str(index) + '.sqlite3')
                with Ledger(path) as db:
                    db.db.execute(mutation)
                    db.db.commit()
                original = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'schema'):
                    Ledger(path)
                self.assertEqual(path.read_bytes(), original)

    def test_missing_destination_row_is_refused(self):
        self.ledger.db.execute('DELETE FROM destination_state')
        self.ledger.db.commit()
        with self.assertRaisesRegex(RuntimeError, 'destination'):
            Ledger(self.path)

    def test_changed_state_constraint_case_is_not_a_supported_schema(self):
        self.ledger.db.execute('PRAGMA writable_schema=ON')
        self.ledger.db.execute("UPDATE sqlite_master SET sql=REPLACE(sql,\"'pending'\",\"'PENDING'\") WHERE name='punches'")
        self.ledger.db.commit()
        self.ledger.db.execute('PRAGMA writable_schema=OFF')
        with self.assertRaisesRegex(RuntimeError, 'schema'):
            Ledger(self.path)

    def test_corrupted_file_is_not_initialized_or_overwritten(self):
        path = Path(self.temp.name) / 'not-sqlite.sqlite3'
        path.write_bytes(b'corrupted historical SQLite content')
        original = path.read_bytes()
        with self.assertRaises(sqlite3.Error):
            Ledger(path)
        self.assertEqual(path.read_bytes(), original)

    def test_integrity_verification_is_read_only(self):
        original = self.path.read_bytes()
        with Ledger(self.path, readonly=True) as db:
            self.assertEqual(db.verify_integrity()['integrity'], 'ok')
        self.assertEqual(self.path.read_bytes(), original)

    def test_fractional_boolean_and_negative_source_codes_are_quarantined(self):
        for field in ('uid', 'punch', 'status'):
            for value in (1.1, 1.9, True, -1, '1.5'):
                with self.subTest(field=field, value=value):
                    self.assertEqual(normalize(fixtures.record(**{field: value}), fixtures.DEVICE)['state'], 'invalid_record')
        first = normalize(fixtures.record(uid=1.1), fixtures.DEVICE)
        second = normalize(fixtures.record(uid=1.9), fixtures.DEVICE)
        self.assertNotEqual(first['identity_key'], second['identity_key'])

    def test_invalid_identity_does_not_collapse_truncated_evidence(self):
        first = normalize(fixtures.record(user_id='x' * 300 + 'a'), fixtures.DEVICE)
        second = normalize(fixtures.record(user_id='x' * 300 + 'b'), fixtures.DEVICE)
        self.assertEqual(first['source_evidence'], second['source_evidence'])
        self.assertNotEqual(first['identity_key'], second['identity_key'])

    def test_short_date_cannot_silently_expand_historical_import(self):
        for value in ('202611', '2026101', ' 20261013', 20261013):
            with self.subTest(value=value), self.assertRaises(ValueError):
                import_boundary(value)

    def test_crash_before_punch_commit_rolls_back_punch_and_fetch_success(self):
        script = """import os,sys
from sync_ledger import Ledger
from punch_normalization import normalize
db=Ledger(sys.argv[1])
db.begin_fetch('FP1_DEVICE_01','2026-10-13 09:00:00')
def rows():
    yield normalize({'user_id':'100','timestamp':'2026-10-13 08:00:00','punch':0,'status':1,'uid':1},{'device_id':'FP1_DEVICE_01'})
    os._exit(25)
db.ingest(rows(),'FP1_DEVICE_01',fetched_at='2026-10-13 09:00:00')
"""
        result = subprocess.run([sys.executable, '-c', script, str(self.path)], cwd=fixtures.ROOT, capture_output=True)
        self.assertEqual(result.returncode, 25, result.stderr)
        with Ledger(self.path) as restarted:
            self.assertEqual(restarted.diagnostics()['total'], 0)
            self.assertEqual(restarted.db.execute('SELECT fetch_ok FROM device_state').fetchone()[0], 0)
            self.assertEqual(restarted.verify_integrity()['integrity'], 'ok')

    def test_multiple_earlier_unresolved_punches_cap_all_mapped_devices(self):
        for device_id, hours in (('FP1_DEVICE_01', (7, 8)), ('FP2_DEVICE_08', (6, 9))):
            device = dict(fixtures.DEVICE, device_id=device_id)
            self.ledger.ingest([normalize(fixtures.record(uid=hour, timestamp=dt.datetime(2026, 10, 13, hour)), device)
                               for hour in hours], device_id, fetched_at='2026-10-13 10:00:00')
        self.assertEqual(self.ledger.safe_watermark(['FP1_DEVICE_01', 'FP2_DEVICE_08']), dt.datetime(2026, 10, 13, 5, 59, 59))

    def test_stop_during_rate_wait_does_not_claim_another_punch(self):
        self.ledger.ingest([normalize(fixtures.record(uid=i), fixtures.DEVICE) for i in range(3)], fixtures.DEVICE['device_id'])
        stopped = [False]
        request = mock.Mock(return_value=fixtures.response())
        config = types.SimpleNamespace(ERPNEXT_URL='https://erp.example.test', ERPNEXT_API_KEY='key', ERPNEXT_API_SECRET='secret')
        def wait(seconds):
            stopped[0] = True
        count = deliver_batch(self.ledger, FrappeClient(config, request), clock=lambda: 1000,
                              sleep=wait, stop_requested=lambda: stopped[0], rate=0.1)
        self.assertEqual(count, 1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.ledger.diagnostics()['states']['pending'], 2)

    def test_service_stop_after_fetch_preserves_committed_pending_punch(self):
        sync = load_sync_module(Path(self.temp.name) / 'logs', '20261013')
        sync.config.DURABLE_SYNC_ENABLED = True
        sync.config.DURABLE_DB_PATH = str(self.path)
        fixtures.provision_test_engine(sync)
        sync.config.devices = [fixtures.DEVICE]
        stopped = [False]
        def fetch(*args, **kwargs):
            stopped[0] = True
            return [fixtures.record()]
        sync.get_all_attendance_from_device = fetch
        sync.main(stop_requested=lambda: stopped[0])
        self.assertEqual(self.ledger.diagnostics()['states']['pending'], 1)
        sync.requests.request.assert_not_called()

    def test_string_false_cannot_enable_durable_mode(self):
        sync = load_sync_module(Path(self.temp.name) / 'logs')
        sync.config.DURABLE_SYNC_ENABLED = 'False'
        sync.main()
        sync.requests.request.assert_not_called()
        self.assertFalse(FakeZK.instances)

    def test_corrupt_legacy_status_does_not_block_durable_restart_delivery(self):
        logs = Path(self.temp.name) / 'logs'
        sync = load_sync_module(logs, '20261013')
        sync.config.DURABLE_SYNC_ENABLED = True
        sync.config.DURABLE_DB_PATH = str(self.path)
        fixtures.provision_test_engine(sync)
        sync.config.devices = [fixtures.DEVICE]
        (logs / 'status.json').write_bytes(b'{broken')
        self.ledger.begin_fetch(fixtures.DEVICE['device_id'], str(dt.datetime.now()))
        self.ledger.ingest([normalize(fixtures.record(), fixtures.DEVICE)], fixtures.DEVICE['device_id'])
        with mock.patch.object(sys.modules['pickledb'], 'PickleDB', side_effect=ValueError('broken status')) as old:
            sync = importlib.reload(sync)
            sync.main()
            old.assert_not_called()
        self.assertEqual(self.ledger.diagnostics()['states']['synced'], 1)
        self.assertEqual((logs / 'status.json').read_bytes(), b'{broken')
        self.assertFalse(sync.status.usable)
        sync.config.shift_type_device_mapping = [dict(shift_type_name='Morning', related_device_id=[fixtures.DEVICE['device_id']])]
        sync.send_shift_sync_to_erpnext = mock.Mock()
        update_shifts(self.ledger, sync)
        sync.send_shift_sync_to_erpnext.assert_not_called()

    def test_repeated_engine_cycles_reobserve_history_without_reposting(self):
        sync = load_sync_module(Path(self.temp.name) / 'logs', '20261013')
        sync.config.DURABLE_SYNC_ENABLED = True
        sync.config.DURABLE_DB_PATH = str(self.path)
        fixtures.provision_test_engine(sync)
        sync.config.DURABLE_BATCH_SIZE = 200
        sync.config.DURABLE_REQUESTS_PER_SECOND = 100000
        sync.config.devices = [fixtures.DEVICE]
        FakeZK.attendances = [FakeAttendance(i, str(i), fixtures.PUNCH_TIME) for i in range(200)]
        sync.main()
        self.assertEqual(sync.requests.request.call_count, 200)
        for _ in range(2):
            self.ledger.db.execute("UPDATE device_state SET last_fetch_attempt='2000-01-01 00:00:00'")
            self.ledger.db.commit()
            sync.main()
        self.assertEqual(sync.requests.request.call_count, 200)
        self.assertEqual(self.ledger.diagnostics()['total'], 200)

    def test_pc_inspector_never_executes_config_or_echoes_credential_syntax_error(self):
        script = (fixtures.ROOT / 'scripts' / 'verify_phase1_pc.ps1').read_text()
        body = re.search(r"\$phase1ConfigInspector = @'\n(.*?)\n'@", script, re.S).group(1)
        config = Path(self.temp.name) / 'inspect_config.py'
        config.write_text('ERPNEXT_API_SECRET="private_fixture_secret"\nraise RuntimeError("config executed")\ndevices=' + repr([fixtures.DEVICE]))
        result = subprocess.run([sys.executable, '-c', body, str(config)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('private_fixture_secret', result.stdout + result.stderr)
        config.write_text('ERPNEXT_API_SECRET="private_fixture_secret')
        result = subprocess.run([sys.executable, '-c', body, str(config)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('private_fixture_secret', result.stdout + result.stderr)
