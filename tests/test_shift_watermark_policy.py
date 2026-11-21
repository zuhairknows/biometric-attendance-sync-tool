"""Shift Type watermark policy: wait only for punches that can still arrive. Temporary SQLite only."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from durable_sync import shift_sync_options, update_shifts
from punch_normalization import normalize
from sync_ledger import Ledger, WATERMARK_POLICY

FETCHED = '2026-11-21 10:45:00'
NOW = dt.datetime(2026, 11, 21, 10, 50)
PUNCH = dt.datetime(2026, 11, 21, 8, 0)


def device(device_id='FP1_DEVICE_01'):
    return dict(device_id=device_id, ip='192.0.2.10', port=4370, password=0,
                punch_direction=None, clear_from_device_on_fetch=False)


def record(uid, timestamp=PUNCH, user_id='100'):
    return dict(user_id=user_id, timestamp=timestamp, punch=0, status=1, uid=uid)


class WatermarkPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='biometric-watermark-tests-')
        self.ledger = Ledger(Path(self.temp.name) / 'test.sqlite3')

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def ingest(self, records, device_id='FP1_DEVICE_01', fetched_at=FETCHED):
        return self.ledger.ingest((normalize(r, device(device_id)) for r in records), device_id, fetched_at=fetched_at)

    def set_rows(self, sql, *params):
        self.ledger.db.execute('UPDATE punches SET ' + sql, params)
        self.ledger.db.commit()

    def watermark(self, devices=('FP1_DEVICE_01',), **kwargs):
        kwargs.setdefault('now', NOW)
        return self.ledger.safe_watermark(list(devices), **kwargs)

    def test_states_that_can_still_arrive_block_and_terminal_states_do_not(self):
        self.ingest([record(1)])
        fresh = NOW.timestamp() - 3600
        blocking = [('pending', None), ('delivering', None), ('retry', 'transient'), ('retry', 'uncertain_submission'),
                    ('retry', 'peer_uncertain')]
        for state, category in blocking:
            with self.subTest(state=state, category=category):
                self.set_rows('state=?, error_category=?, first_captured=?', state, category, fresh)
                self.assertEqual(self.watermark(), PUNCH - dt.timedelta(seconds=1))
        terminal = [('unmatched_employee', 'employee_mapping'), ('rejected', 'cutover_hold'),
                    ('rejected', 'inactive_employee'), ('rejected', 'business_validation'),
                    ('invalid_record', 'source_validation'), ('synced', None), ('duplicate', 'already_exists')]
        for state, category in terminal:
            with self.subTest(state=state, category=category):
                self.set_rows('state=?, error_category=?', state, category)
                self.assertEqual(self.watermark(), dt.datetime.fromisoformat(FETCHED))

    def test_ordinary_retry_stops_blocking_after_grace_period(self):
        self.ingest([record(1)])
        self.set_rows("state='retry', error_category='transient', first_captured=?", NOW.timestamp() - 23 * 3600)
        self.assertEqual(self.watermark(), PUNCH - dt.timedelta(seconds=1))
        self.set_rows('first_captured=?', NOW.timestamp() - 25 * 3600)
        self.assertEqual(self.watermark(), dt.datetime.fromisoformat(FETCHED))
        self.assertEqual(self.watermark(policy=dict(retry_grace_seconds=48 * 3600)), PUNCH - dt.timedelta(seconds=1))

    def test_destination_wide_retries_always_block(self):
        self.ingest([record(1)])
        for category in ('authentication', 'rate_limited'):
            with self.subTest(category=category):
                self.set_rows("state='retry', error_category=?, first_captured=?", category, NOW.timestamp() - 30 * 24 * 3600)
                self.assertEqual(self.watermark(), PUNCH - dt.timedelta(seconds=1))

    def test_punches_before_live_boundary_are_ignored_only_when_boundary_is_known(self):
        early = dt.datetime(2026, 9, 1, 8)
        self.ingest([record(1, early)])
        self.assertEqual(self.watermark(), early - dt.timedelta(seconds=1))
        self.assertEqual(self.watermark(boundary=dt.datetime(2026, 9, 30)), dt.datetime.fromisoformat(FETCHED))
        on_boundary = dt.datetime(2026, 9, 30)
        self.ingest([record(2, on_boundary)])
        self.assertEqual(self.watermark(boundary=on_boundary), on_boundary - dt.timedelta(seconds=1))

    def test_future_dated_punches_are_device_clock_errors(self):
        # Scan started 10:45:00; with 10 minutes tolerance anything after 10:55:00 is a clock error.
        self.ingest([record(1, dt.datetime(2133, 2, 10, 2, 8)), record(2, dt.datetime(2026, 11, 21, 10, 55, 1))])
        self.assertEqual(self.watermark(), dt.datetime.fromisoformat(FETCHED))
        report = self.ledger.watermark_report(['FP1_DEVICE_01'], now=NOW)['devices']['FP1_DEVICE_01']
        self.assertEqual((report['blocking'], report['not_waited']), (0, 2))
        self.assertEqual(self.watermark(policy=dict(future_tolerance_seconds=3600)), dt.datetime.fromisoformat(FETCHED))
        self.assertEqual(self.ledger.watermark_report(['FP1_DEVICE_01'], now=NOW, policy=dict(future_tolerance_seconds=3600))
                         ['devices']['FP1_DEVICE_01']['blocking'], 1)
        self.ingest([record(3, dt.datetime(2026, 11, 21, 10, 40))])
        self.assertEqual(self.watermark(), dt.datetime(2026, 11, 21, 10, 39, 59))

    def test_unknown_timestamp_blocks_only_when_it_could_still_be_delivered(self):
        self.ingest([record(1)])
        self.set_rows("state='synced'")
        self.ledger.db.execute("UPDATE punches SET punch_timestamp=NULL, state='invalid_record'")
        self.ledger.db.commit()
        self.assertEqual(self.watermark(), dt.datetime.fromisoformat(FETCHED))
        self.set_rows("state='pending'")
        self.assertIsNone(self.watermark())
        self.assertEqual(self.ledger.watermark_report(['FP1_DEVICE_01'], now=NOW)['devices']['FP1_DEVICE_01']['status'],
                         'unknown_timestamp')

    def test_failed_scan_blocks_until_stale_then_device_is_excluded(self):
        self.ingest([], 'FP1_DEVICE_01')
        self.ingest([], 'FP1_DEVICE_06', fetched_at='2026-11-21 08:00:00')
        self.ledger.begin_fetch('FP1_DEVICE_06', '2026-11-21 10:46:00')
        devices = ('FP1_DEVICE_01', 'FP1_DEVICE_06')
        self.assertIsNone(self.watermark(devices))
        self.assertEqual(self.ledger.watermark_report(list(devices), now=NOW)['devices']['FP1_DEVICE_06']['status'], 'scan_failed')
        later = dt.datetime(2026, 11, 21, 20, 1)
        report = self.ledger.watermark_report(list(devices), now=later)
        self.assertEqual(report['devices']['FP1_DEVICE_06']['status'], 'stale')
        self.assertEqual(report['watermark'], dt.datetime.fromisoformat(FETCHED))

    def test_device_never_scanned_blocks_unless_ignored(self):
        self.ingest([], 'FP1_DEVICE_01')
        devices = ('FP1_DEVICE_01', 'FP1_DEVICE_06')
        self.assertIsNone(self.watermark(devices))
        ignored = dict(ignored_devices=('FP1_DEVICE_06',))
        self.assertEqual(self.watermark(devices, policy=ignored), dt.datetime.fromisoformat(FETCHED))
        self.assertIsNone(self.watermark(('FP1_DEVICE_06',), policy=ignored))

    def test_production_shape_advances_past_held_history_junk_and_door_cards(self):
        # Mirrors the 2026-11-21 sync PC ledger: history held at cutover back to 2000-01-01,
        # empty-user junk, clock errors in 2133, unmatched door cards, a long-failed reader.
        self.ingest([record(1, dt.datetime(2000, 1, 1)), record(2, dt.datetime(2025, 5, 26, 14, 56, 57))], 'FP1_DEVICE_01')
        self.set_rows("state='rejected', error_category='cutover_hold'")
        self.ingest([record(3, dt.datetime(2000, 1, 1), user_id='5'), record(4, dt.datetime(2133, 2, 10, 2, 8)),
                     record(5, dt.datetime(2026, 11, 21, 9, 8), user_id='99999'),
                     record(6, dt.datetime(2026, 11, 21, 10, 40))], 'FP2_DEVICE_10', fetched_at='2026-11-21 10:46:50')
        self.set_rows("state='invalid_record', error_category='source_validation' WHERE device_uid='3'")
        self.set_rows("state='unmatched_employee', error_category='employee_mapping' WHERE device_uid='5'")
        self.set_rows("state='synced' WHERE device_uid='6'")
        self.ingest([record(7, dt.datetime(2026, 11, 5, 13, 17))], 'FP1_DEVICE_06', fetched_at='2026-11-05 13:26:38')
        self.set_rows("state='retry', error_category='transient', first_captured=? WHERE device_uid='7'", NOW.timestamp() - 16 * 24 * 3600)
        self.ledger.begin_fetch('FP1_DEVICE_06', '2026-11-21 10:45:10')
        devices = ('FP1_DEVICE_01', 'FP2_DEVICE_10', 'FP1_DEVICE_06')
        report = self.ledger.watermark_report(list(devices), boundary=dt.datetime(2026, 10, 17, 9), now=NOW)
        self.assertEqual(report['watermark'], dt.datetime.fromisoformat(FETCHED))
        self.assertEqual(report['devices']['FP1_DEVICE_06']['status'], 'stale')
        self.assertEqual(report['devices']['FP2_DEVICE_10']['not_waited'], 3)
        self.assertEqual(report['devices']['FP2_DEVICE_10']['blocking'], 0)

    def test_watermark_is_rounded_down_and_takes_the_earliest_device(self):
        self.ingest([], 'FP1_DEVICE_01', fetched_at='2026-11-21 10:45:00.999999')
        self.ingest([record(1, dt.datetime(2026, 11, 21, 10, 30))], 'FP2_DEVICE_08')
        self.assertEqual(self.watermark(('FP1_DEVICE_01',)), dt.datetime(2026, 11, 21, 10, 45))
        self.assertEqual(self.watermark(('FP1_DEVICE_01', 'FP2_DEVICE_08')), dt.datetime(2026, 11, 21, 10, 29, 59))
        self.assertIsNone(self.watermark(()))

    def test_default_policy_matches_documented_settings(self):
        self.assertEqual(shift_sync_options(types.SimpleNamespace()), dict(WATERMARK_POLICY, ignored_devices=()))
        self.assertEqual(WATERMARK_POLICY['retry_grace_seconds'], 24 * 3600)
        self.assertEqual(WATERMARK_POLICY['stale_device_seconds'], 12 * 3600)
        self.assertEqual(WATERMARK_POLICY['future_tolerance_seconds'], 600)
        config = types.SimpleNamespace(SHIFT_SYNC_RETRY_GRACE_HOURS=6, SHIFT_SYNC_DEVICE_STALE_HOURS=0.5,
                                       SHIFT_SYNC_FUTURE_TOLERANCE_MINUTES=0, SHIFT_SYNC_IGNORED_DEVICES=['FP1_DEVICE_06'])
        self.assertEqual(shift_sync_options(config), dict(retry_grace_seconds=21600, stale_device_seconds=1800.0,
                                                          future_tolerance_seconds=0, ignored_devices=('FP1_DEVICE_06',)))

    def test_invalid_settings_are_rejected(self):
        for name, value in (('SHIFT_SYNC_RETRY_GRACE_HOURS', -1), ('SHIFT_SYNC_DEVICE_STALE_HOURS', True),
                            ('SHIFT_SYNC_FUTURE_TOLERANCE_MINUTES', float('inf')), ('SHIFT_SYNC_RETRY_GRACE_HOURS', '24'),
                            ('SHIFT_SYNC_IGNORED_DEVICES', 'FP1_DEVICE_06'), ('SHIFT_SYNC_IGNORED_DEVICES', [6])):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ValueError):
                    shift_sync_options(types.SimpleNamespace(**{name: value}))


class UpdateShiftsPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='biometric-watermark-tests-')
        self.ledger = Ledger(Path(self.temp.name) / 'test.sqlite3')
        self.config = types.SimpleNamespace(ERPNEXT_REQUEST_TIMEOUT=30, shift_type_device_mapping=[
            dict(shift_type_name=['صباحي 1', 'ليلي'], related_device_id=['FP1_DEVICE_01', 'FP1_DEVICE_06'])])
        self.engine = types.SimpleNamespace(config=self.config, status=types.SimpleNamespace(usable=True, get=lambda _: None),
                                            _safe_convert_date=lambda value, _: None, info_logger=mock.Mock(),
                                            error_logger=mock.Mock(), send_shift_sync_to_erpnext=mock.Mock(return_value=200))

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def ingest(self, records, device_id, fetched_at=FETCHED):
        return self.ledger.ingest((normalize(r, device(device_id)) for r in records), device_id, fetched_at=fetched_at)

    def logged(self, logger, event):
        return [json.loads(c.args[0]) for c in logger.call_args_list if c.args and event in str(c.args[0])]

    def test_ignored_device_unblocks_all_mapped_shifts_and_is_reported(self):
        self.ingest([record(1, dt.datetime(2000, 1, 1))], 'FP1_DEVICE_01')
        self.ledger.db.execute("UPDATE punches SET state='rejected', error_category='cutover_hold'")
        self.ledger.db.commit()
        update_shifts(self.ledger, self.engine, now=NOW)
        self.engine.send_shift_sync_to_erpnext.assert_not_called()
        self.config.SHIFT_SYNC_IGNORED_DEVICES = ['FP1_DEVICE_06']
        update_shifts(self.ledger, self.engine, now=NOW)
        expected = dt.datetime.fromisoformat(FETCHED)
        self.assertEqual(self.engine.send_shift_sync_to_erpnext.call_args_list,
                         [mock.call('صباحي 1', expected), mock.call('ليلي', expected)])
        self.assertEqual(self.ledger.shift_watermark('ليلي'), expected)
        self.assertEqual(self.logged(self.engine.error_logger.warning, 'shift_watermark_devices_excluded')[-1]['devices'],
                         ['FP1_DEVICE_06'])
        events = self.logged(self.engine.info_logger.info, '"event": "shift_watermark"')
        self.assertEqual(events[-1]['watermark'], FETCHED)
        self.assertEqual(events[-1]['devices']['FP1_DEVICE_06'], dict(status='ignored'))

    def test_watermark_never_moves_backwards(self):
        self.config.SHIFT_SYNC_IGNORED_DEVICES = ['FP1_DEVICE_06']
        self.ingest([], 'FP1_DEVICE_01')
        update_shifts(self.ledger, self.engine, now=NOW)
        self.assertEqual(self.engine.send_shift_sync_to_erpnext.call_count, 2)
        self.ingest([record(1, dt.datetime(2026, 11, 21, 9))], 'FP1_DEVICE_01', fetched_at='2026-11-21 10:55:00')
        update_shifts(self.ledger, self.engine, now=NOW)
        self.assertEqual(self.engine.send_shift_sync_to_erpnext.call_count, 2)
        self.assertEqual(self.ledger.shift_watermark('صباحي 1'), dt.datetime.fromisoformat(FETCHED))

    def test_boundary_from_service_excludes_pre_cutover_pending_punches(self):
        self.config.SHIFT_SYNC_IGNORED_DEVICES = ['FP1_DEVICE_06']
        self.ingest([record(1, dt.datetime(2026, 9, 1))], 'FP1_DEVICE_01')
        update_shifts(self.ledger, self.engine, now=NOW, boundary=dt.datetime(2026, 9, 30))
        self.engine.send_shift_sync_to_erpnext.assert_any_call('ليلي', dt.datetime.fromisoformat(FETCHED))

    def test_invalid_setting_stops_shift_updates(self):
        self.config.SHIFT_SYNC_IGNORED_DEVICES = 'FP1_DEVICE_06'
        self.ingest([], 'FP1_DEVICE_01')
        with self.assertRaises(ValueError):
            update_shifts(self.ledger, self.engine, now=NOW)
        self.engine.send_shift_sync_to_erpnext.assert_not_called()


if __name__ == '__main__':
    unittest.main()
