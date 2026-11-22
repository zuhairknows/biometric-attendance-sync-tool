"""Dashboard Shift sync panel: read-only, literal config only, degrades without hiding the ledger."""
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest

from manager_app.controller import ManagerError
from manager_app.profile import shift_sync_profile
from manager_app.web import ORIGIN, create_app
from test_manager import ManagerFixture

MAPPING = ("\nshift_type_device_mapping = [{'shift_type_name': ['Morning', 'Night'], "
           "'related_device_id': ['FP1_DEVICE_01', 'FP2_DEVICE_08']}]\n")


class ShiftSyncDashboardTests(ManagerFixture):
    def config(self, extra=''):
        path = self.root / 'shift-config.py'
        path.write_text(Path(self.values['device_config']).read_text(encoding='utf-8') + MAPPING + extra, encoding='utf-8')
        return str(path)

    def test_dashboard_estimates_next_sync_date_and_lists_saved_dates(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("INSERT INTO shift_state VALUES('Morning','2026-10-11 07:00:00')")
        self.original = self.source.read_bytes()  # Fixture change, never a manager operation.
        data = self.controller.dashboard(str(self.source), values=dict(device_config=self.config()))
        sync = data['shift_sync']
        self.assertIsNone(sync['error'])
        self.assertEqual(sync['saved'], [dict(shift='Morning', watermark='2026-10-11 07:00:00')])
        self.assertEqual(sync['mappings'][0]['shifts'], ['Morning', 'Night'])
        self.assertEqual(sync['mappings'][0]['next_watermark'], '2026-10-11 07:59:59')
        first = sync['mappings'][0]['devices'][0]
        self.assertEqual((first['device'], first['status'], first['blocking'], first['earliest_blocking']),
                         ('FP1_DEVICE_01', 'ok', 1, '2026-10-11 08:00:00'))

    def test_dashboard_applies_configured_ignored_devices(self):
        config = self.config("SHIFT_SYNC_IGNORED_DEVICES = ['FP1_DEVICE_01']\n")
        mapping = self.controller.dashboard(str(self.source), values=dict(device_config=config))['shift_sync']['mappings'][0]
        self.assertEqual(mapping['next_watermark'], '2026-10-11 08:59:59')
        self.assertEqual(mapping['devices'][0], dict(device='FP1_DEVICE_01', status='ignored'))

    def test_bad_or_missing_configuration_degrades_only_the_shift_panel(self):
        bad = self.config("SHIFT_SYNC_IGNORED_DEVICES = __import__('os').getcwd()\n")
        for config, message in ((None, 'Device configuration not set'), (bad, 'Shift sync status unavailable')):
            with self.subTest(config=config):
                data = self.controller.dashboard(str(self.source), values=dict(device_config=config))
                self.assertEqual((data['integrity'], data['total']), ('OK', 3))
                self.assertIn(message, data['shift_sync']['error'])
                self.assertEqual(data['shift_sync']['mappings'], [])

    def test_shift_profile_reads_literals_only(self):
        profile = shift_sync_profile(self.config('SHIFT_SYNC_RETRY_GRACE_HOURS = 6\nSHIFT_SYNC_DEVICE_STALE_HOURS = 1\n'))
        self.assertEqual(profile['policy'], dict(retry_grace_seconds=21600, stale_device_seconds=3600, ignored_devices=()))
        self.assertEqual(profile['mappings'], [dict(shifts=['Morning', 'Night'], devices=['FP1_DEVICE_01', 'FP2_DEVICE_08'])])
        for extra in ('SHIFT_SYNC_RETRY_GRACE_HOURS = -1\n', "SHIFT_SYNC_IGNORED_DEVICES = 'FP1_DEVICE_01'\n",
                      'SHIFT_SYNC_DEVICE_STALE_HOURS = True\n', 'shift_type_device_mapping = []\n'):
            with self.subTest(extra=extra):
                with self.assertRaises(ManagerError):
                    shift_sync_profile(self.config(extra))

    def test_dashboard_page_renders_shift_panel(self):
        self.values['device_config'] = self.config()
        app = create_app(self.controller, self.values, demo=True)
        state = app.extensions['manager']
        self.addCleanup(state.close)
        state.dashboard = self.controller.dashboard(str(self.source), values=self.values)
        page = app.test_client().get('/', base_url=ORIGIN).get_data(as_text=True)
        self.assertIn('Shift sync', page)
        self.assertIn('2026-10-11 07:59:59', page)
        self.assertIn('No Shift Type update has been confirmed yet', page)


class InsightsDashboardTests(ManagerFixture):
    def test_unknown_cards_reasons_clock_errors_and_freshness(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE punches SET state='unmatched_employee',error_category='employee_mapping' WHERE id IN (1,2)")
            db.execute("UPDATE punches SET user_id='777' WHERE id IN (1,2)")
            db.execute("UPDATE punches SET punch_timestamp='2133-02-10 02:08:00' WHERE id=3")
        self.original = self.source.read_bytes()  # Fixture change, never a manager operation.
        data = self.controller.dashboard(str(self.source))
        insights = data['insights']
        self.assertIsNone(insights['error'])
        self.assertEqual(insights['unmatched'], [dict(user_id='777', punches=2, last_seen='2026-10-11 09:00:00',
                                                      devices=sorted(insights['unmatched'][0]['devices']))])
        self.assertEqual(sorted(insights['unmatched'][0]['devices']), ['FP1_DEVICE_01', 'FP2_DEVICE_08'])
        self.assertEqual(insights['reasons'], [dict(state='unmatched_employee', category='employee_mapping', punches=2),
                                               dict(state='pending', category='—', punches=1)])
        rows = {row['device']: row for row in data['devices']}
        self.assertEqual(rows['FP2_DEVICE_11']['future_dated'], 1)
        self.assertEqual(rows['FP1_DEVICE_01']['future_dated'], 0)
        self.assertTrue(rows['FP1_DEVICE_01']['stale'])  # Demo pull is weeks old.
        self.assertGreater(rows['FP1_DEVICE_01']['pull_age_minutes'], 60)
        self.assertEqual(rows['FP1_DEVICE_01']['captured_24h'], 1)  # Demo ledger was captured just now.

    def test_fresh_device_is_not_flagged(self):
        from sync_ledger import Ledger
        now = __import__('datetime').datetime(2026, 10, 11, 11, 30)
        with Ledger(self.source, readonly=True) as ledger:
            states = {r['device_id']: dict(r) for r in ledger.db.execute('SELECT * FROM device_state')}
            result = self.controller._insights(ledger, states, now=now)
        self.assertEqual(result['devices']['FP1_DEVICE_01']['pull_age_minutes'], 30)
        self.assertFalse(result['devices']['FP1_DEVICE_01']['stale'])

    def test_dashboard_page_renders_insight_panels(self):
        app = create_app(self.controller, self.values, demo=True)
        state = app.extensions['manager']
        self.addCleanup(state.close)
        state.dashboard = self.controller.dashboard(str(self.source), values=self.values)
        page = app.test_client().get('/', base_url=ORIGIN).get_data(as_text=True)
        for text in ('Unknown cards', 'No unmatched cards', 'Not delivered, by reason', 'Captured 24 h', 'days ago'):
            self.assertIn(text, page)


if __name__ == '__main__':
    unittest.main()
