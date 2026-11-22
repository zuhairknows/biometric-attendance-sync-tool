"""Cards enrolled before their Employee existed: re-check and send again. Temporary SQLite only."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from durable_delivery import FrappeClient, deliver_batch, recheck_unmatched
from durable_sync import unmatched_recheck_options
from punch_normalization import normalize
from sync_ledger import Ledger

DEVICE = dict(device_id='FP1_DEVICE_01', ip='192.0.2.10', port=4370, password=0,
              punch_direction=None, clear_from_device_on_fetch=False)
SINCE = '2026-10-01 00:00:00'
HOUR = 3600


def record(uid=1, user_id='100', day=13):
    return dict(user_id=user_id, timestamp=dt.datetime(2026, 10, day, 8), punch=0, status=1, uid=uid)


def reply(status=200, body=None):
    return types.SimpleNamespace(status_code=status, content=json.dumps(body or {}).encode(), headers={})


def employees(*rows):
    return reply(body={'data': list(rows)})


ACTIVE = dict(name='SYNTH-EMP', status='Active', attendance_device_id='100')
NOT_FOUND = reply(417, {'exception': 'No Employee found for attendance_device_id: 100'})


class UnmatchedRecheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='biometric-recheck-tests-')
        self.ledger = Ledger(Path(self.temp.name) / 'test.sqlite3')
        self.config = types.SimpleNamespace(ERPNEXT_URL='https://erp.example.test', ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY='TEST_KEY', ERPNEXT_API_SECRET='TEST_SECRET', ERPNEXT_REQUEST_TIMEOUT=30,
            DURABLE_CONFIRM_READS_VERIFIED=True, DURABLE_SERVER_REVIEW_REFERENCE='SYNTHETIC ONLY',
            DURABLE_RESEND_VERIFIED_MISSING=True)
        self.checked = {}

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def unmatched(self, records):
        self.ledger.ingest([normalize(r, DEVICE) for r in records], DEVICE['device_id'])
        deliver_batch(self.ledger, FrappeClient(self.config, mock.Mock(return_value=NOT_FOUND)),
                      clock=lambda: 1000, sleep=lambda _: None)
        self.assertEqual(self.states(), ['unmatched_employee'] * len(records))

    def states(self):
        return [r[0] for r in self.ledger.db.execute('SELECT state FROM punches ORDER BY id')]

    def recheck(self, request, at, since=SINCE):
        return recheck_unmatched(self.ledger, FrappeClient(self.config, request), since, 6 * HOUR,
                                 self.checked, clock=lambda: at, sleep=lambda _: None)

    def test_card_still_without_employee_is_read_once_per_interval_and_never_resent(self):
        self.unmatched([record(), record(uid=2, day=14)])
        request = mock.Mock(return_value=employees())
        self.assertEqual(self.recheck(request, 2000), 0)
        self.assertEqual(request.call_count, 1)  # One read for the card, not one per punch.
        self.assertEqual(request.call_args[0][0], 'GET')
        self.recheck(request, 2000 + 5 * HOUR)
        self.assertEqual(request.call_count, 1)
        self.recheck(request, 2000 + 6 * HOUR)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(self.states(), ['unmatched_employee'] * 2)

    def test_new_employee_gets_punches_in_window_sent_again_as_fresh_posts(self):
        self.unmatched([record(), record(uid=2, day=14), record(uid=3, day=1)])
        self.assertEqual(self.recheck(mock.Mock(return_value=employees(ACTIVE)), 2000, since='2026-10-10 00:00:00'), 2)
        self.assertEqual(self.states(), ['retry', 'retry', 'unmatched_employee'])
        self.assertEqual(self.ledger.diagnostics()['uncertain_submissions'], 0)
        methods = []
        def request(method, url, **kwargs):
            methods.append(method)
            return reply(body={'message': {'name': 'CHECKIN-NEW'}})
        deliver_batch(self.ledger, FrappeClient(self.config, request), clock=lambda: 3000, sleep=lambda _: None)
        self.assertEqual(methods, ['POST', 'POST'])  # The 417 proved absence: no lookup first.
        self.assertEqual(self.states(), ['synced', 'synced', 'unmatched_employee'])

    def test_inactive_or_ambiguous_employee_is_not_requeued(self):
        self.unmatched([record()])
        inactive = dict(ACTIVE, status='Left')
        self.assertEqual(self.recheck(mock.Mock(return_value=employees(inactive)), 2000), 0)
        self.checked.clear()
        self.assertEqual(self.recheck(mock.Mock(return_value=employees(ACTIVE, dict(ACTIVE, name='OTHER'))), 2000), 0)
        self.assertEqual(self.states(), ['unmatched_employee'])

    def test_failed_read_stops_and_is_retried_next_tick(self):
        self.unmatched([record(), record(uid=2, user_id='200')])
        request = mock.Mock(return_value=reply(500))
        self.recheck(request, 2000)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.checked, {})
        self.assertEqual(self.states(), ['unmatched_employee'] * 2)

    def test_without_verified_reads_nothing_is_requested(self):
        self.unmatched([record()])
        self.config.DURABLE_CONFIRM_READS_VERIFIED = False
        request = mock.Mock()
        self.assertEqual(self.recheck(request, 2000), 0)
        request.assert_not_called()

    def test_reads_per_tick_are_bounded(self):
        self.unmatched([record(uid=i, user_id=str(300 + i)) for i in range(15)])
        request = mock.Mock(return_value=employees())
        self.recheck(request, 2000)
        self.assertEqual(request.call_count, 10)
        self.recheck(request, 2001)
        self.assertEqual(request.call_count, 15)

    def test_settings(self):
        self.assertEqual(unmatched_recheck_options(types.SimpleNamespace()), (30, 6 * HOUR))
        self.assertEqual(unmatched_recheck_options(types.SimpleNamespace(UNMATCHED_RECHECK_DAYS=0))[0], 0)
        for bad in (dict(UNMATCHED_RECHECK_DAYS=-1), dict(UNMATCHED_RECHECK_HOURS=0),
                    dict(UNMATCHED_RECHECK_DAYS=True), dict(UNMATCHED_RECHECK_HOURS='6')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                unmatched_recheck_options(types.SimpleNamespace(**bad))


if __name__ == '__main__':
    unittest.main()
