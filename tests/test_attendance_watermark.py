"""Attendance progress tests using temporary SQLite and mocked Frappe only."""
import datetime as dt
import types
import unittest
from unittest import mock

from durable_sync import _run_locked, update_shifts
from sync_ledger import STATES
import test_durable_sync as fixtures

DEVICE = fixtures.DEVICE
record = fixtures.record


class AttendanceWatermarkTests(unittest.TestCase):
    # Reuse only the isolated fixture helpers, not the unrelated test cases.
    setUp = fixtures.DurableSyncTests.setUp
    tearDown = fixtures.DurableSyncTests.tearDown
    ingest = fixtures.DurableSyncTests.ingest
    punch = fixtures.DurableSyncTests.punch
    boundary = dt.datetime(2026, 10, 13)

    def test_pre_boundary_records_do_not_block_or_change(self):
        self.ingest([record(timestamp=self.boundary - dt.timedelta(seconds=1))])
        for state in STATES:
            with self.subTest(state=state):
                self.ledger.db.execute(
                    'UPDATE punches SET state=?,error_category=?', (state, 'cutover_hold'))
                before = tuple(self.punch())
                self.assertEqual(self.ledger.safe_watermark(
                    [DEVICE['device_id']], not_before=self.boundary), dt.datetime(2026, 10, 13, 9))
                self.assertEqual(tuple(self.punch()), before)

    def test_all_unresolved_live_states_still_block_at_boundary(self):
        self.ingest([record(timestamp=self.boundary)])
        for state in STATES:
            if state in ('synced', 'duplicate'):
                continue
            with self.subTest(state=state):
                self.ledger.db.execute('UPDATE punches SET state=?,error_category=?', (state, 'cutover_hold'))
                self.assertEqual(self.ledger.safe_watermark(
                    [DEVICE['device_id']], not_before=self.boundary),
                    self.boundary - dt.timedelta(seconds=1))

    def test_unknown_timestamp_still_blocks(self):
        self.ingest()
        self.ledger.db.execute('UPDATE punches SET punch_timestamp=NULL')
        self.assertIsNone(self.ledger.safe_watermark([DEVICE['device_id']], not_before=self.boundary))

    def test_old_history_does_not_hide_unresolved_live_punch_on_another_device(self):
        other = dict(DEVICE, device_id='SECOND')
        self.ingest([record(timestamp=self.boundary - dt.timedelta(days=30))])
        self.ledger.db.execute("UPDATE punches SET state='rejected',error_category='cutover_hold'")
        live_time = self.boundary + dt.timedelta(hours=2)
        self.ingest([record(timestamp=live_time)], device=other)
        self.assertEqual(self.ledger.safe_watermark(
            [DEVICE['device_id'], other['device_id']], not_before=self.boundary),
            live_time - dt.timedelta(seconds=1))

    def test_failed_or_missing_device_still_blocks(self):
        self.ingest([])
        self.assertIsNone(self.ledger.safe_watermark(
            [DEVICE['device_id'], 'MISSING'], not_before=self.boundary))
        self.ledger.begin_fetch(DEVICE['device_id'], '2026-10-13 10:00:00')
        self.assertIsNone(self.ledger.safe_watermark([DEVICE['device_id']], not_before=self.boundary))

    def test_no_boundary_preserves_conservative_behavior(self):
        self.ingest([record(timestamp=self.boundary - dt.timedelta(days=1))])
        self.assertLess(self.ledger.safe_watermark([DEVICE['device_id']]), self.boundary)

    def test_invalid_boundary_is_rejected(self):
        for value in ('2026-10-13', self.boundary.replace(tzinfo=dt.timezone.utc), False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ledger.safe_watermark([DEVICE['device_id']], not_before=value)

    def test_shift_update_uses_live_boundary_and_retains_historical_evidence(self):
        self.ingest([record(timestamp=self.boundary - dt.timedelta(days=1))])
        self.ledger.db.execute("UPDATE punches SET state='rejected',error_category='cutover_hold'")
        engine = types.SimpleNamespace(
            config=types.SimpleNamespace(shift_type_device_mapping=[dict(
                shift_type_name=['Morning', 'Night'], related_device_id=[DEVICE['device_id']])]),
            status=types.SimpleNamespace(get=lambda _: None),
            _safe_convert_date=lambda *_: None,
            send_shift_sync_to_erpnext=mock.Mock(return_value=200))
        update_shifts(self.ledger, engine, not_before=self.boundary)
        expected = dt.datetime(2026, 10, 13, 9)
        self.assertEqual(engine.send_shift_sync_to_erpnext.call_args_list,
                         [mock.call('Morning', expected), mock.call('Night', expected)])
        self.assertEqual(self.punch()['state'], 'rejected')
        self.assertEqual(self.punch()['error_category'], 'cutover_hold')

    def test_worker_passes_later_import_or_activation_boundary_to_delivery_and_shifts(self):
        # Exercise the real worker dispatch; a helper-only test would miss a lost boundary.
        engine = mock.Mock()
        engine.config = types.SimpleNamespace(
            IMPORT_START_DATE='20261013', DURABLE_DB_PATH=str(self.path),
            devices=[DEVICE], PULL_FREQUENCY=5)
        with mock.patch('durable_sync.Ledger') as factory, \
                mock.patch('durable_sync.FrappeClient'), \
                mock.patch('durable_sync.validate_activation') as activation, \
                mock.patch('durable_sync.deliver_batch') as deliver, \
                mock.patch('durable_sync.update_shifts') as shifts:
            ledger = factory.return_value.__enter__.return_value
            ledger.polling_due.return_value = False
            ledger.diagnostics.return_value = {}
            for cutover, expected in (
                ('2026-10-12 00:00:00', self.boundary),
                ('2026-10-14 06:30:00', dt.datetime(2026, 10, 14, 6, 30)),
            ):
                with self.subTest(cutover=cutover):
                    activation.return_value = {'cutover_time': cutover}
                    _run_locked(engine, lambda: False)
                    self.assertEqual(deliver.call_args.kwargs['not_before'], expected)
                    self.assertEqual(shifts.call_args.kwargs['not_before'], expected)
