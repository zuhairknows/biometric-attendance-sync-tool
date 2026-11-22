"""Uncertain sends: resend only after the destination proves the punch absent. Temporary SQLite only."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from durable_delivery import FrappeClient, deliver_batch
from punch_normalization import normalize
from sync_ledger import Ledger

DEVICE = dict(device_id='FP1_DEVICE_01', ip='192.0.2.10', port=4370, password=0,
              punch_direction=None, clear_from_device_on_fetch=False)
EMPLOYEE = dict(name='SYNTH-EMP', status='Active', attendance_device_id='100')


def record(uid=1, **changes):
    result = dict(user_id='100', timestamp=dt.datetime(2026, 10, 13, 8), punch=0, status=1, uid=uid)
    result.update(changes)
    return result


def reply(status=200, data=None, body=None):
    content = body if body is not None else {'data': data if data is not None else []}
    return types.SimpleNamespace(status_code=status, content=json.dumps(content).encode(), headers={})


def created(name='CHECKIN-NEW'):
    return reply(body={'message': {'name': name}})


class ResendVerifiedMissingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='biometric-resend-tests-')
        self.ledger = Ledger(Path(self.temp.name) / 'test.sqlite3')
        self.config = types.SimpleNamespace(ERPNEXT_URL='https://erp.example.test', ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY='TEST_KEY', ERPNEXT_API_SECRET='TEST_SECRET', ERPNEXT_REQUEST_TIMEOUT=30,
            DURABLE_CONFIRM_READS_VERIFIED=True, DURABLE_SERVER_REVIEW_REFERENCE='SYNTHETIC ONLY',
            DURABLE_RESEND_VERIFIED_MISSING=True)
        self.ledger.ingest([normalize(record(), DEVICE)], DEVICE['device_id'])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def reset(self):
        self.tearDown()
        self.setUp()

    def deliver(self, request, at):
        return deliver_batch(self.ledger, FrappeClient(self.config, request), clock=lambda: at,
                             sleep=lambda _: None, logger=mock.Mock())

    def row(self):
        return self.ledger.db.execute('SELECT * FROM punches').fetchone()

    def first_send_times_out(self):
        self.deliver(mock.Mock(side_effect=TimeoutError('synthetic')), 1000)
        self.assertEqual((self.row()['state'], self.row()['error_category']), ('retry', 'transport'))

    def test_disabled_by_default_keeps_uncertain_punch_waiting_without_reads(self):
        self.config.DURABLE_CONFIRM_READS_VERIFIED = False
        del self.config.DURABLE_RESEND_VERIFIED_MISSING
        self.first_send_times_out()
        request = mock.Mock()
        self.deliver(request, 5000)
        request.assert_not_called()
        self.assertEqual(self.row()['error_category'], 'confirmation_pending')

    def test_flag_needs_verified_confirmation_reads(self):
        self.config.DURABLE_SERVER_REVIEW_REFERENCE = ' '
        self.assertFalse(FrappeClient(self.config, mock.Mock()).resend_verified_missing)
        self.config.DURABLE_SERVER_REVIEW_REFERENCE = 'SYNTHETIC ONLY'
        self.config.DURABLE_RESEND_VERIFIED_MISSING = 'yes'
        self.assertFalse(FrappeClient(self.config, mock.Mock()).resend_verified_missing)

    def test_verified_absence_after_settling_resends_once_and_syncs(self):
        self.first_send_times_out()
        request = mock.Mock(side_effect=[reply(data=[EMPLOYEE]), reply(data=[]), created()])
        logger = mock.Mock()
        deliver_batch(self.ledger, FrappeClient(self.config, request), clock=lambda: 1700, sleep=lambda _: None, logger=logger)
        self.assertEqual([c.args[0] for c in request.call_args_list], ['GET', 'GET', 'POST'])
        window = json.loads(request.call_args_list[1].kwargs['params']['filters'])
        self.assertEqual(window, [['employee', '=', 'SYNTH-EMP'],
                                  ['time', 'between', ['2026-10-13 07:59:58', '2026-10-13 08:00:02']]])
        self.assertEqual((self.row()['state'], self.row()['checkin_name']), ('synced', 'CHECKIN-NEW'))
        self.assertTrue(any('durable_resend_verified_absent' in str(c.args[0]) for c in logger.info.call_args_list))

    def test_absence_is_not_trusted_until_last_attempt_settles(self):
        self.first_send_times_out()
        request = mock.Mock(side_effect=[reply(data=[EMPLOYEE]), reply(data=[])])
        self.deliver(request, 1300)  # 300 s after the timed-out POST
        self.assertEqual([c.args[0] for c in request.call_args_list], ['GET', 'GET'])
        self.assertEqual(self.row()['error_category'], 'confirmation_pending')
        self.assertIn('settle', self.row()['error_message'])
        request = mock.Mock(side_effect=[reply(data=[EMPLOYEE]), reply(data=[]), created()])
        self.deliver(request, 1300 + 601)
        self.assertEqual(self.row()['state'], 'synced')

    def test_present_within_two_seconds_is_confirmed_without_post(self):
        for seen in ('2026-10-13 08:00:00', '2026-10-13 08:00:02', '2026-10-13 07:59:58'):
            with self.subTest(seen=seen):
                self.reset()
                self.first_send_times_out()
                checkin = dict(name='EXISTING', employee='SYNTH-EMP', time=seen)
                request = mock.Mock(side_effect=[reply(data=[EMPLOYEE]), reply(data=[checkin])])
                self.deliver(request, 5000)
                self.assertEqual([c.args[0] for c in request.call_args_list], ['GET', 'GET'])
                self.assertEqual((self.row()['state'], self.row()['checkin_name']), ('duplicate', 'EXISTING'))

    def test_failed_or_ambiguous_reads_never_resend(self):
        cases = {
            'employee read forbidden': [reply(status=403)],
            'employee read server error': [reply(status=500)],
            'two employees share the device ID': [reply(data=[EMPLOYEE, dict(EMPLOYEE, name='OTHER')])],
            'employee inactive': [reply(data=[dict(EMPLOYEE, status='Inactive')])],
            'no employee': [reply(data=[])],
            'checkin read failed': [reply(data=[EMPLOYEE]), reply(status=500)],
            'checkin read malformed': [reply(data=[EMPLOYEE]), reply(body={'data': 'nope'})],
            'checkin for another employee': [reply(data=[EMPLOYEE]),
                                             reply(data=[dict(name='X', employee='OTHER', time='2026-10-13 08:00:00')])],
        }
        for name, replies in cases.items():
            with self.subTest(name):
                self.reset()
                self.first_send_times_out()
                request = mock.Mock(side_effect=replies)
                self.deliver(request, 5000)
                self.assertNotIn('POST', [c.args[0] for c in request.call_args_list])
                self.assertEqual((self.row()['state'], self.row()['error_category']), ('retry', 'confirmation_pending'))

    def test_duplicate_race_on_resend_is_confirmed_or_held(self):
        duplicate = reply(status=417, body={'exception': 'This employee already has a log with the same timestamp',
                                            'exc_type': 'ValidationError'})
        checkin = dict(name='RACED', employee='SYNTH-EMP', time='2026-10-13 08:00:00')
        for later, expected in (([reply(data=[EMPLOYEE]), reply(data=[checkin])], ('duplicate', 'destination_confirmed')),
                                ([reply(data=[EMPLOYEE]), reply(data=[])], ('retry', 'confirmation_pending'))):
            with self.subTest(expected=expected):
                self.reset()
                self.first_send_times_out()
                request = mock.Mock(side_effect=[reply(data=[EMPLOYEE]), reply(data=[]), duplicate] + later)
                self.deliver(request, 5000)
                self.assertEqual([c.args[0] for c in request.call_args_list], ['GET', 'GET', 'POST', 'GET', 'GET'])
                self.assertEqual((self.row()['state'], self.row()['error_category']), expected)

    def test_resend_failure_stays_retryable_and_settles_again(self):
        self.first_send_times_out()
        request = mock.Mock(side_effect=[reply(data=[EMPLOYEE]), reply(data=[]), TimeoutError('again')])
        self.deliver(request, 5000)
        self.assertEqual((self.row()['state'], self.row()['error_category']), ('retry', 'transport'))
        request = mock.Mock(side_effect=[reply(data=[EMPLOYEE]), reply(data=[])])
        self.deliver(request, 5000 + 7200)  # backoff elapsed; settle is measured from the failed resend
        request = mock.Mock(side_effect=[reply(data=[EMPLOYEE]), reply(data=[]), created('CHECKIN-LATE')])
        self.deliver(request, 5000 + 7200 + 4000)
        self.assertEqual(self.row()['state'], 'synced')

    def test_settle_setting_is_validated(self):
        for value in (59, True, '600', float('nan')):
            with self.subTest(value=value):
                self.config.DURABLE_RESEND_SETTLE_SECONDS = value
                with self.assertRaises(ValueError):
                    FrappeClient(self.config, mock.Mock())
        self.config.DURABLE_RESEND_SETTLE_SECONDS = 900
        self.assertEqual(FrappeClient(self.config, mock.Mock()).resend_settle_seconds, 900)


if __name__ == '__main__':
    unittest.main()
