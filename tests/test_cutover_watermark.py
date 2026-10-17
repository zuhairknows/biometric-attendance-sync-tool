"""Focused watermark scope tests; synthetic SQLite only, no device/HTTP calls."""
import datetime as dt
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from durable_startup import StartupError, activation_path
from durable_sync import update_shifts
from punch_normalization import normalize
from sync_ledger import Ledger


BOUNDARY = dt.datetime(2026, 10, 17, 9)
FETCHED = BOUNDARY + dt.timedelta(minutes=10)
DEVICE = dict(device_id='SYNTHETIC_01', punch_direction=None)


class CutoverWatermarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-watermark-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite3'
        self.ledger = Ledger(self.path)
        self.addCleanup(self.ledger.close)
        self.sequence = 0
        self.ledger.ingest([], DEVICE['device_id'], fetched_at=str(FETCHED))

    def add(self, timestamp, state='rejected', category='cutover_hold'):
        self.sequence += 1
        row = normalize(dict(user_id='0001', timestamp=timestamp, punch=0,
                             status=1, uid=self.sequence), DEVICE)
        row.update(state=state, error_category=category)
        self.ledger.ingest([row], DEVICE['device_id'], fetched_at=str(FETCHED))

    def watermark(self):
        return self.ledger.safe_watermark([DEVICE['device_id']], cutover=BOUNDARY)

    def test_only_preboundary_verified_hold_is_excluded_without_mutation(self):
        self.add(BOUNDARY - dt.timedelta(days=30))
        before = [tuple(row) for row in self.ledger.db.execute('SELECT * FROM punches')]
        self.assertEqual(self.watermark(), FETCHED)
        self.assertLess(self.ledger.safe_watermark([DEVICE['device_id']]), BOUNDARY)
        self.assertEqual(before, [tuple(row) for row in self.ledger.db.execute('SELECT * FROM punches')])

    def test_boundary_inclusive_and_microseconds(self):
        for offset, expected in ((dt.timedelta(microseconds=-1), FETCHED),
                                 (dt.timedelta(0), BOUNDARY - dt.timedelta(seconds=1)),
                                 (dt.timedelta(microseconds=1), BOUNDARY - dt.timedelta(seconds=1))):
            with self.subTest(offset=offset):
                with self.ledger.db:
                    self.ledger.db.execute('DELETE FROM punches')
                self.add(BOUNDARY + offset)
                self.assertEqual(self.watermark(), expected)

    def test_wrong_hold_category_or_state_still_blocks(self):
        old = BOUNDARY - dt.timedelta(days=1)
        for state, category in (('rejected', 'business_validation'), ('pending', 'cutover_hold'),
                                ('unmatched_employee', 'employee_mapping')):
            with self.subTest(state=state):
                with self.ledger.db:
                    self.ledger.db.execute('DELETE FROM punches')
                self.add(old, state, category)
                self.assertEqual(self.watermark(), old - dt.timedelta(seconds=1))

    def test_every_postboundary_unresolved_state_remains_a_blocker(self):
        for state in ('pending', 'retry', 'delivering', 'unmatched_employee', 'rejected', 'invalid_record'):
            with self.subTest(state=state):
                with self.ledger.db:
                    self.ledger.db.execute('DELETE FROM punches')
                self.add(BOUNDARY + dt.timedelta(minutes=1), state, None)
                self.assertEqual(self.watermark(), BOUNDARY + dt.timedelta(seconds=59))

    def test_unknown_malformed_aware_or_noncanonical_hold_time_blocks(self):
        self.add(BOUNDARY - dt.timedelta(days=1))
        for stamp in (None, 'invalid', '2026-02-30 08:00:00', '2026-10-16 08:00:00+03:00', '2026-10-16'):
            with self.subTest(stamp=stamp):
                with self.ledger.db:
                    self.ledger.db.execute('UPDATE punches SET punch_timestamp=?', (stamp,))
                self.assertIsNone(self.watermark())

    def test_failed_or_missing_device_still_blocks_mapped_shift(self):
        self.add(BOUNDARY - dt.timedelta(days=1))
        self.ledger.begin_fetch(DEVICE['device_id'], str(FETCHED))
        self.assertIsNone(self.watermark())
        self.assertIsNone(self.ledger.safe_watermark(['MISSING'], cutover=BOUNDARY))

    def test_multi_device_minimum_is_preserved(self):
        self.add(BOUNDARY - dt.timedelta(days=1))
        second = 'SYNTHETIC_02'
        self.ledger.ingest([], second, fetched_at=str(BOUNDARY + dt.timedelta(minutes=2)))
        self.assertEqual(self.ledger.safe_watermark([DEVICE['device_id'], second], cutover=BOUNDARY),
                         BOUNDARY + dt.timedelta(minutes=2))

    def test_invalid_boundary_is_refused(self):
        for boundary in ('2026-10-17', BOUNDARY.replace(tzinfo=dt.timezone.utc),
                         BOUNDARY.replace(microsecond=1)):
            with self.subTest(boundary=boundary), self.assertRaises(ValueError):
                self.ledger.safe_watermark([DEVICE['device_id']], cutover=boundary)

    def test_update_uses_effective_boundary_and_retains_both_floors(self):
        config = types.SimpleNamespace(IMPORT_START_DATE='20261018',
            shift_type_device_mapping=[dict(shift_type_name='SYNTHETIC_SHIFT',
                                           related_device_id=[DEVICE['device_id']])])
        engine = types.SimpleNamespace(config=config,
            status=types.SimpleNamespace(usable=True, get=lambda key: None),
            _safe_convert_date=lambda value, fmt: dt.datetime.fromisoformat(value) if value else None,
            send_shift_sync_to_erpnext=mock.Mock(return_value=200))
        effective = dt.datetime(2026, 10, 18)
        with mock.patch('durable_sync.validate_activation', return_value={'cutover_time': str(BOUNDARY)}), \
                mock.patch.object(self.ledger, 'safe_watermark', return_value=effective) as calculate:
            update_shifts(self.ledger, engine)
            calculate.assert_called_once_with([DEVICE['device_id']], cutover=effective)
            self.assertEqual(self.ledger.shift_watermark('SYNTHETIC_SHIFT'), effective)
            engine.send_shift_sync_to_erpnext.reset_mock()
            update_shifts(self.ledger, engine)
            engine.send_shift_sync_to_erpnext.assert_not_called()
            engine.status.get = lambda key: str(effective + dt.timedelta(hours=1))
            with mock.patch.object(self.ledger, 'safe_watermark', return_value=effective + dt.timedelta(minutes=5)):
                update_shifts(self.ledger, engine)
            engine.send_shift_sync_to_erpnext.assert_not_called()
            self.assertEqual(self.ledger.shift_watermark('SYNTHETIC_SHIFT'), effective)

    def test_missing_activation_prevents_any_shift_update(self):
        config = types.SimpleNamespace(IMPORT_START_DATE='20261017')
        engine = types.SimpleNamespace(config=config, status=types.SimpleNamespace(usable=True),
                                       send_shift_sync_to_erpnext=mock.Mock())
        self.assertFalse(activation_path(self.path).exists())
        with self.assertRaises(StartupError):
            update_shifts(self.ledger, engine)
        engine.send_shift_sync_to_erpnext.assert_not_called()


if __name__ == '__main__':
    unittest.main()
