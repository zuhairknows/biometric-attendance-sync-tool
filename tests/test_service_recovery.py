"""Service-owned coordination: synthetic files/transports and an actual local mutex."""
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from durable_delivery import FrappeClient, deliver_batch
from durable_startup import provision, validate_activation, worker_lock, StartupError
from durable_sync import delivery_options
from historical_attendance import recovery
from historical_attendance.client import ReadOnlyFrappeClient, VerificationError
from historical_attendance.engine import CLAIMS, investigate
from historical_attendance.reporting import write_csv
from historical_attendance.repository import SQLiteRepository
from historical_attendance.recovery_simulation import SimulationTarget, response
from historical_attendance.service_handoff import Inbox, prepare_request, approve_request, dispatch_request, cancel_request
from historical_attendance.service_recovery import Coordinator, install, request_results, service_tick
from punch_normalization import normalize
from sync_ledger import Ledger


class Clock:
    def __init__(self):
        self.value = 1000.

    def __call__(self):
        return self.value

    def sleep(self, value):
        self.value += value


class ServiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='service-recovery-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ('live', 'inputs', 'reports', 'inbox', 'destination'):
            (self.root / name).mkdir()
        self.live = self.root / 'live' / 'sync.sqlite3'
        self.snapshot = self.root / 'inputs' / 'snapshot.sqlite3'
        self.remote = self.root / 'destination' / 'synthetic.json'
        self.remote.write_text(json.dumps(dict(synthetic_only=True, site_url='https://service.example.test',
            employees=[dict(name='SYN_EMP', attendance_device_id='100', status='Active', modified='2026-01-01 00:00:00')], checkins=[])))
        self.target = SimulationTarget(self.remote, max_requests=10000)
        self.config = self.target.config
        self.config.HISTORICAL_RECOVERY_INBOX = str(self.root / 'inbox')
        self.config.HISTORICAL_REQUEST_AUTHOR_SID = 'S-1-5-21-123'
        self.config.DURABLE_CONFIRM_READS_VERIFIED = True
        self.config.DURABLE_SERVER_REVIEW_REFERENCE = 'SYNTHETIC'
        self.clock = Clock()
        self.options = delivery_options(self.config)
        provision(self.live, self.target.url, 15, '2026-10-12 00:00:00', 'SYNTHETIC',
            storage_check=self.storage, now=dt.datetime(2026, 10, 11))
        self.add()
        self.inbox = Inbox(self.root / 'inbox', trusted_sid='S-1-5-21-123', storage_check=self.storage)

    def storage(self, path, service_sid=None):
        if not Path(path).resolve().is_relative_to(self.root):
            raise StartupError('synthetic_private_storage_failed')

    def add(self, *, user='100', hour=8, device='FP1_DEVICE_01', uid=1, held=False):
        row = normalize(dict(user_id=user, timestamp=dt.datetime(2026, 10, 11 if held else 13, hour),
            punch=0, status=1, uid=uid), dict(device_id=device, punch_direction=None))
        if held:
            row.update(state='rejected', error_category='cutover_hold', error_message='Historical held evidence')
        with Ledger(self.live) as ledger:
            ledger.ingest([row], device)

    def evidence(self):
        shutil.copyfile(self.live, self.snapshot)
        self.policy = dict(schema='attendance-investigation-contract-v1', destination_environment='synthetic',
            site_url=self.target.url, site_timezone='Asia/Riyadh', timestamp_precision='seconds',
            duplicate_key='employee_time_log_type', coverage_start='2026-10-10 00:00:00', coverage_end='2026-10-14 23:59:59',
            device_ids=['FP1_DEVICE_01', 'FP2_DEVICE_08'], evidence_reference='SYNTHETIC',
            verification={c: True for c in (*CLAIMS, 'read_only_api_verified')})
        contract = self.root / 'inputs' / 'contract.json'
        contract.write_text(json.dumps(self.policy))
        self.config.HISTORICAL_RECOVERY_CONTRACT_SHA256 = recovery.file_hash(contract)
        reader = ReadOnlyFrappeClient(self.target.url, 'SYNTHETIC', 'SYNTHETIC', self.target.request)
        rows, summary = investigate(SQLiteRepository(self.snapshot), reader,
            dt.datetime(2026, 10, 10), dt.datetime(2026, 10, 14, 23, 59, 59), self.policy['device_ids'], self.policy)
        details = self.root / 'reports' / 'details.csv'
        with details.open('w', encoding='utf-8', newline='') as stream:
            write_csv(stream, rows)
        summary['contract_file_sha256'] = recovery.file_hash(contract)
        summary['reports'] = [dict(file=details.name, sha256=recovery.file_hash(details))]
        summary_path = self.root / 'reports' / 'summary.json'
        summary_path.write_text(json.dumps(summary))
        self.target.calls.clear()
        return dict(ledger_path=self.snapshot, summary_path=summary_path, summary_sha256=recovery.file_hash(summary_path),
            contract_path=contract, contract_sha256=recovery.file_hash(contract), receipt_review='SYNTHETIC supervisor receipt')

    def request(self, ids=(1,), held_ids=(), *, dispatch=True):
        evidence = getattr(self, 'arguments', None)
        if evidence is None:
            self.arguments = evidence = self.evidence()
        request_id, sha = prepare_request(self.inbox, self.live, **evidence, now=self.clock)
        approved = approve_request(self.inbox, request_id, sha, list(ids), approval_reference='SYNTHETIC selected approval',
            held_ids=held_ids, hold_reference='SYNTHETIC specific hold exception' if held_ids else '', now=self.clock)
        if dispatch:
            dispatch_request(self.inbox, request_id, approved, execution_reference='SYNTHETIC execute separately', now=self.clock)
        return request_id

    def tick(self, *, target=True, stopped=lambda: False):
        with Ledger(self.live) as ledger:
            return service_tick(ledger, self.config, validate_activation(self.live, self.config),
                dt.datetime(2026, 10, 12), self.options, stop_requested=stopped,
                target=self.target if target else None, storage_check=self.storage,
                clock=self.clock, monotonic=self.clock, sleep=self.clock.sleep)

    def rows(self, table='punches'):
        with Ledger(self.live, readonly=True) as ledger:
            return [dict(r) for r in ledger.db.execute('SELECT * FROM '+table)]

    def normal(self, guard=None):
        with Ledger(self.live) as ledger:
            if guard:
                # The actual service keeps this one ledger open for both lanes;
                # this fixture reopens it to exercise persisted restart state.
                guard.ledger = ledger
            return deliver_batch(ledger, FrappeClient(self.config, self.target.request), batch_size=10,
                clock=self.clock, sleep=self.clock.sleep, not_before=dt.datetime(2026, 10, 12), recovery_guard=guard)

    def test_production_gate_and_approval_alone_are_inert(self):
        self.request(dispatch=False)
        original = self.live.read_bytes()
        self.assertIsNone(self.tick(target=False))
        self.assertEqual(self.live.read_bytes(), original)
        self.tick()
        self.assertEqual(self.target.calls, [])
        self.assertEqual(self.rows()[0]['state'], 'pending')
        self.assertEqual(self.rows('sr_requests'), [])

    def test_intent_is_durable_before_post_and_success_is_independently_verified(self):
        self.request()
        send = self.target.request
        def transport(method, url, **kwargs):
            self.assertLessEqual(kwargs['timeout'], 10)
            self.assertFalse(kwargs['allow_redirects'])
            if method == 'POST':
                self.assertEqual(self.rows()[0]['state'], 'delivering')
                self.assertEqual(len(self.rows('sr_intents')), 1)
                self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')
            return send(method, url, **kwargs)
        self.target.request = transport
        self.tick()
        self.assertEqual(self.target.calls.count('POST'), 1)
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Recovered')
        self.assertEqual(self.rows()[0]['state'], 'synced')
        self.assertGreater(self.rows('sr_requests')[0]['requests_used'], 16)
        self.tick()
        self.normal()
        self.assertEqual(self.target.calls.count('POST'), 1)

    def test_selected_cutover_authorization_does_not_release_other_holds(self):
        self.add(held=True, hour=7, uid=2)
        self.add(held=True, hour=6, uid=3)
        self.request(ids=(2,), held_ids=(2,))
        self.tick()
        rows = self.rows()
        self.assertEqual(rows[1]['state'], 'synced')
        self.assertEqual(rows[2]['error_category'], 'cutover_hold')
        self.assertEqual(rows[2]['attempt_count'], 0)
        self.assertTrue(any(r['reason'] == 'selected_cutover_exception_authorized' for r in self.rows('sr_events')))

    def test_holds_require_separate_explicit_selected_authorization(self):
        self.add(held=True, uid=2)
        arguments = self.evidence()
        rid, sha = prepare_request(self.inbox, self.live, **arguments, now=self.clock)
        for held_ids, reference in (((), ''), ((2,), ''), ((1, 2), 'SYNTHETIC')):
            with self.assertRaises(VerificationError):
                approve_request(self.inbox, rid, sha, [2], approval_reference='SYNTHETIC',
                    held_ids=held_ids, hold_reference=reference, now=self.clock)
        self.assertEqual(self.rows()[1]['error_category'], 'cutover_hold')

    def test_timeout_before_commit_never_blindly_retries(self):
        self.target.data['next_send'] = 'timeout_before'
        self.target._save()
        self.request()
        self.tick()
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')
        self.clock.sleep(31)
        self.target = SimulationTarget(self.remote, max_requests=10000)
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows()[0]['error_category'], 'confirmation_pending')
        self.normal()  # Default normal writer has no permission to bypass the intent fence.
        self.assertNotIn('POST', self.target.calls)

    def test_timeout_after_commit_is_confirmed_without_repost(self):
        self.target.data['next_send'] = 'timeout_after'
        self.target._save()
        self.request()
        self.tick()
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')
        self.clock.sleep(40)  # Respect the shared Phase 1 destination cooldown first.
        self.tick()
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Already Exists')
        self.assertEqual(self.rows()[0]['state'], 'duplicate')
        self.assertEqual(self.target.calls.count('POST'), 1)

    def test_crash_after_commit_restarts_with_reads_only(self):
        self.target.data['next_send'] = 'crash_after'
        self.target._save()
        self.request()
        with self.assertRaises(KeyboardInterrupt):
            self.tick()
        self.assertEqual(self.rows()[0]['state'], 'delivering')
        self.target = SimulationTarget(self.remote, max_requests=10000)
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows()[0]['state'], 'duplicate')
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Already Exists')

    def test_crash_after_intent_before_post_burns_permission(self):
        self.request()
        send = self.target.request
        def crash(method, url, **kwargs):
            if method == 'POST':
                raise KeyboardInterrupt
            return send(method, url, **kwargs)
        self.target.request = crash
        with self.assertRaises(KeyboardInterrupt):
            self.tick()
        self.target = SimulationTarget(self.remote, max_requests=10000)
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')

    def test_prior_intent_confirmation_cannot_overwrite_new_quarantine(self):
        self.target.data['next_send'] = 'crash_after'
        self.target._save()
        self.request()
        with self.assertRaises(KeyboardInterrupt):
            self.tick()
        self.target = SimulationTarget(self.remote, max_requests=10000)
        compare = Coordinator.compare

        def quarantine_after_read(coordinator, *args, **kwargs):
            result = compare(coordinator, *args, **kwargs)
            # Simulate a state change while the independent remote read is running.
            self.update("UPDATE punches SET state='rejected',error_category='unmatched_employee' WHERE id=1")
            return result

        with mock.patch.object(Coordinator, 'compare', quarantine_after_read):
            self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows()[0]['state'], 'rejected')
        self.assertEqual(self.rows()[0]['error_category'], 'unmatched_employee')
        self.assertEqual(self.rows()[0]['attempt_count'], 1)
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')

    def test_duplicate_requests_and_duplicate_normal_source_do_not_repost(self):
        self.request()
        self.request()
        self.tick()
        self.tick()
        self.add(device='FP2_DEVICE_08', uid=20)
        guard = self.tick()
        self.normal(guard)
        self.assertEqual(self.target.calls.count('POST'), 1)
        self.assertEqual(self.rows()[1]['state'], 'duplicate')

    def test_equivalent_mapping_duplicate_normal_identity_uses_database_lookup(self):
        self.request()
        guard = self.tick()
        self.add(user='100 ', device='FP2_DEVICE_08', uid=20)
        self.normal(guard)
        self.assertEqual(self.target.calls.count('POST'), 1)
        self.assertEqual(self.rows()[1]['state'], 'duplicate')

    def test_unrelated_normal_attendance_at_same_second_still_delivers(self):
        self.request()
        self.tick()
        self.target.data['employees'].append(dict(name='SYN_OTHER', attendance_device_id='200', status='Active', modified='2026-01-01 00:00:00'))
        self.target._save()
        self.add(user='200', device='FP2_DEVICE_08', uid=20)
        guard = self.tick()
        self.normal(guard)
        self.assertEqual(self.target.calls.count('POST'), 2)
        self.assertEqual(self.rows()[1]['state'], 'synced')

    def test_changing_live_state_before_or_during_verification_cannot_send(self):
        self.request()
        self.update("UPDATE punches SET state='retry',attempt_count=1 WHERE id=1")
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Skipped')

    def update(self, sql):
        with closing(sqlite3.connect(self.live)) as db, db:
            db.execute(sql)

    def test_normal_claim_racing_preflight_wins_without_recovery_post(self):
        self.request()
        send = self.target.request
        claimed = []
        def transport(method, url, **kwargs):
            result = send(method, url, **kwargs)
            if not claimed:
                with Ledger(self.live) as other:
                    claimed.append(other.claim(self.clock(), 180))
            return result
        self.target.request = transport
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(len(self.rows('sr_intents')), 0)
        self.assertEqual(self.rows()[0]['state'], 'delivering')

    def test_global_mutex_excludes_second_service_writer(self):
        self.request()
        errors = []
        with worker_lock(self.live):
            thread = threading.Thread(target=lambda: self.concurrent_tick(errors))
            thread.start()
            thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [StartupError])
        self.assertNotIn('POST', self.target.calls)

    def concurrent_tick(self, errors):
        try:
            self.tick()
        except Exception as error:
            errors.append(type(error))

    def test_changed_inputs_and_unapproved_contract_are_blocked(self):
        self.request()
        self.config.HISTORICAL_RECOVERY_CONTRACT_SHA256 = '0'*64
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_events')[0]['reason'], 'request_validation_failed')
        self.config.HISTORICAL_RECOVERY_CONTRACT_SHA256 = self.arguments['contract_sha256']
        self.snapshot.write_bytes(self.snapshot.read_bytes()+b'changed')
        self.tick()
        self.assertNotIn('POST', self.target.calls)

    def test_cancellation_expiration_and_request_budget(self):
        rid = self.request()
        cancel_request(self.inbox, rid, cancellation_reference='SYNTHETIC stop')
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')
        self.clock.sleep(121)
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_requests')[0]['requests_used'], 0)

    def test_pacing_timeout_and_mid_request_deadline(self):
        self.request()
        send = self.target.request
        times = []
        def transport(method, url, **kwargs):
            times.append(self.clock())
            self.assertLessEqual(kwargs['timeout'], 10)
            result = send(method, url, **kwargs)
            if method == 'POST':
                self.clock.sleep(121)
            return result
        self.target.request = transport
        self.tick()
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')
        self.assertTrue(all(b-a >= .5 for a,b in zip(times, times[1:])))
        self.assertEqual(self.target.calls[-1], 'POST')

    def test_unverified_mapping_and_200_without_destination_record_cannot_recover(self):
        self.request()
        send = self.target.request
        def transport(method, url, **kwargs):
            if method == 'POST':
                self.target.calls.append(method)
                return response(200, {'message': {'name': 'SYNTHETIC_LIE'}})
            return send(method, url, **kwargs)
        self.target.request = transport
        self.tick()
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')
        self.assertEqual(self.rows()[0]['state'], 'retry')
        self.tick()
        self.assertEqual(self.target.calls.count('POST'), 1)

    def test_ambiguous_mapping_is_blocked_before_post(self):
        self.request()
        self.target.data['employees'].append(dict(name='SYN_EQUIVALENT', attendance_device_id='100 ', status='Inactive', modified='2026-01-01 00:00:00'))
        self.target._save()
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')

    def test_private_acl_request_hash_and_schema_checks_are_preserved(self):
        self.request()
        with mock.patch.object(self.inbox, 'storage_check', side_effect=StartupError('private_storage_unverified')):
            with self.assertRaises(StartupError):
                self.inbox.read(next(self.inbox.directory.glob('*.request.json')).name.split('.')[0], 'request')
        with Ledger(self.live) as ledger:
            install(ledger)
            with ledger.db:
                ledger.db.execute('DROP INDEX sr_seconds')
            with self.assertRaises(VerificationError):
                install(ledger)

    def test_missing_employee_and_read_error_never_post(self):
        self.request()
        self.target.data['employees'] = []
        self.target._save()
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')

    def test_unknown_endpoint_safety_never_performs_even_get(self):
        arguments = self.evidence()
        path = arguments['contract_path']
        policy = json.loads(path.read_text())
        policy['verification']['read_only_api_verified'] = False
        path.write_text(json.dumps(policy))
        arguments['contract_sha256'] = recovery.file_hash(path)
        # Existing summary is now bound to another contract and cannot be reused.
        with self.assertRaises(VerificationError):
            prepare_request(self.inbox, self.live, **arguments, now=self.clock)
        self.assertEqual(self.target.calls, [])

    def test_durable_request_limit_survives_restart(self):
        rid = self.request()
        self.target.data['next_send'] = 'timeout_before'
        self.target._save()
        self.tick()
        self.update("UPDATE sr_requests SET requests_used=128")
        self.target.calls.clear()
        self.tick()
        self.assertEqual(self.target.calls, [])
        self.assertEqual(self.rows('sr_requests')[0]['requests_used'], 128)
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')

    def test_duplicate_wording_without_identity_proof_is_not_success(self):
        self.request()
        send = self.target.request
        def transport(method, url, **kwargs):
            if method == 'POST':
                self.target.calls.append(method)
                return response(417, {'exc_type':'ValidationError',
                    'exception':'This employee already has a log with the same timestamp'})
            return send(method, url, **kwargs)
        self.target.request = transport
        self.tick()
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')
        self.assertEqual(self.rows()[0]['state'], 'retry')
        self.tick()
        self.assertEqual(self.target.calls.count('POST'), 1)

    def test_source_cookie_and_attempt_history_cannot_be_bypassed(self):
        arguments = self.evidence()
        self.update('PRAGMA application_id=123')
        with self.assertRaises(VerificationError):
            prepare_request(self.inbox, self.live, **arguments, now=self.clock)

    def test_changed_report_during_get_burns_no_submission_permission(self):
        self.request()
        original = self.target.request
        changed = []
        def transport(method, url, **kwargs):
            result = original(method, url, **kwargs)
            if not changed:
                changed.append(True)
                self.arguments['summary_path'].write_text('{}')
            return result
        self.target.request = transport
        self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_intents'), [])
        self.assertEqual(self.rows()[0]['attempt_count'], 0)

    def test_preexisting_checkin_is_confirmed_and_normal_peer_does_not_post(self):
        self.request()
        self.target.data['checkins'] = [dict(name='SYN_EXISTING', employee='SYN_EMP',
            time='2026-10-13 08:00:00', log_type='', device_id='FP1_DEVICE_01', modified='2026-01-01 00:00:00')]
        self.target._save()
        guard = self.tick()
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Already Exists')
        self.assertEqual(self.rows()[0]['state'], 'duplicate')
        self.add(device='FP2_DEVICE_08', uid=20)
        self.normal(guard)
        self.assertNotIn('POST', self.target.calls)

    def test_read_timeout_requires_fresh_request_before_any_send(self):
        self.request()
        send = self.target.request
        self.target.request = mock.Mock(side_effect=TimeoutError('PRIVATE TRANSPORT'))
        self.tick()
        self.assertEqual(self.rows('sr_items')[0]['status'], 'Needs Review')
        self.target.request = send
        self.tick()
        self.assertNotIn('POST', self.target.calls)

    def test_request_tampering_is_audited_without_private_error_text(self):
        rid = self.request()
        path = self.inbox.path(rid, 'request')
        request = json.loads(path.read_text())
        request['approved']['approval_reference'] = 'PRIVATE EMPLOYEE SECRET'
        path.write_text(json.dumps(request))
        self.tick()
        self.assertEqual(self.target.calls, [])
        self.assertNotIn('PRIVATE', json.dumps(self.rows('sr_events')))
        self.assertEqual(self.rows('sr_events')[0]['reason'], 'request_validation_failed')

    def test_real_time_dispatch_budget_uses_one_publication_timestamp(self):
        rid = self.request(dispatch=False)
        approved = self.inbox.path(rid, 'approved')
        calls = []
        def advancing_clock():
            value = self.clock() + len(calls) * .001
            calls.append(value)
            return value
        dispatch_request(self.inbox, rid, recovery.file_hash(approved), execution_reference='SYNTHETIC', now=advancing_clock)
        request = self.inbox.read(rid, 'request')
        self.assertEqual(request['deadline'], request['dispatched_at'] + 120)
        self.assertEqual(len(calls), 2)  # Expiry check, then one publication time.

    def test_mapping_change_cannot_create_a_new_normal_submission_identity(self):
        self.request()
        self.tick()
        self.target.data['employees'][0]['name'] = 'SYN_REMAPPED'
        self.target._save()
        self.add(user='100 ', device='FP2_DEVICE_08', uid=20)
        guard = self.tick()
        self.normal(guard)
        self.assertEqual(self.target.calls.count('POST'), 1)
        self.assertEqual(self.rows()[1]['state'], 'retry')

    def test_audit_reader_is_read_only_and_preserves_events(self):
        rid = self.request()
        self.tick()
        with Ledger(self.live, readonly=True) as ledger:
            data = request_results(ledger, rid)
        self.assertEqual(data['counts'], {'Recovered': 1})
        self.assertFalse(data['production_execution_supported'])
        self.assertTrue(any(e['reason'] == 'submission_intent_recorded_before_post' for e in data['events']))

    def test_pacing_is_preserved_between_recovery_and_unrelated_normal_post(self):
        self.request()
        send = self.target.request
        times = []
        def transport(method, url, **kwargs):
            times.append(self.clock())
            return send(method, url, **kwargs)
        self.target.request = transport
        guard = self.tick()
        self.target.data['employees'].append(dict(name='SYN_OTHER', attendance_device_id='200', status='Active', modified='2026-01-01 00:00:00'))
        self.target._save()
        self.add(user='200', hour=9, uid=2)
        self.normal(guard)
        self.assertTrue(all(b-a >= .5 for a,b in zip(times, times[1:])))
        self.assertEqual(self.target.calls.count('POST'), 2)

    def test_equivalent_peer_claim_racing_intent_transaction_cannot_send(self):
        self.request()
        self.add(user='100 ', device='FP2_DEVICE_08', uid=2)
        verify = Coordinator.evidence
        count = []
        def changed(coordinator, envelope):
            result = verify(coordinator, envelope)
            count.append(True)
            if len(count) == 3:
                self.update("UPDATE punches SET state='delivering',attempt_count=1,last_attempt=1000,lease_until=1180 WHERE id=2")
            return result
        with mock.patch.object(Coordinator, 'evidence', changed):
            self.tick()
        self.assertNotIn('POST', self.target.calls)
        self.assertEqual(self.rows('sr_intents'), [])
        self.assertEqual(self.rows()[0]['attempt_count'], 0)


if __name__ == '__main__':
    unittest.main()
