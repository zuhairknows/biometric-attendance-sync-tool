"""Operator-to-existing-writer acceptance. No production site, SCM or device access."""
from contextlib import closing, ExitStack
import datetime as dt
import json
from pathlib import Path
import sqlite3
import time
from unittest import mock

from durable_startup import validate_activation, worker_lock
from durable_sync import delivery_options
from historical_attendance import recovery, recovery_preview
from historical_attendance.service_recovery import service_tick
from historical_attendance.recovery_simulation import SimulationTarget
from manager_app.controller import Controller
from manager_app.web import create_app, ORIGIN
import test_manager as fixtures
from test_service_recovery import Clock


class OperatorRecoveryTests(fixtures.ManagerFixture):
    def setUp(self):
        super().setUp()
        self.live = Path(self.values['recovery_ledger'])
        self.inbox = Path(self.values['recovery_inbox'])
        self.live_original = self.live.read_bytes()
        self.app = create_app(self.controller, self.values, demo=True)
        self.state = self.app.extensions['manager']
        self.addCleanup(self.state.close)
        self.client = self.app.test_client()
        self.client.get('/', base_url=ORIGIN)
        self.state.recovery_preview = self.preview()

    def preview(self):
        return recovery_preview.preview(backup=self.values['backup'], backup_hash=self.values['backup_hash'],
            directory=self.values['output'], start=self.values['start'], end=self.values['end'],
            devices=['FP1_DEVICE_01', 'FP2_DEVICE_08', 'FP2_DEVICE_11'], acl_check=self.controller.acl_check)

    def post(self, path, data=None):
        with self.client.session_transaction(base_url=ORIGIN) as session:
            csrf = session['csrf']
        return self.client.post(path, base_url=ORIGIN, data=dict(csrf=csrf, **(data or {})))

    def finish(self, response, *, success=True):
        self.assertEqual(response.status_code, 303, response.data)
        self.state.thread.join(20)
        self.assertFalse(self.state.thread.is_alive())
        self.assertEqual(self.state.job['status'], 'complete' if success else 'blocked', self.state.job)
        return self.client.get(self.state.job['destination'], base_url=ORIGIN)

    def prepare(self, ids=('1',), *, success=True):
        page = self.finish(self.post('/recovery/service/prepare', dict(ids=list(ids), receipt='SYNTHETIC independent review',
            preview_hash=self.values['backup_hash'])), success=success)
        if success:
            self.identity = self.state.job['destination'].rsplit('/', 1)[1]
        return page

    def approve(self, ids=('1',), held=(), *, success=True):
        return self.finish(self.post(f'/recovery/service/{self.identity}/approve', dict(ids=list(ids), hold_ids=list(held),
            fingerprint=recovery.file_hash(self.inbox / (self.identity+'.draft.json')),
            reference='SYNTHETIC approval', hold_reference='SYNTHETIC separate hold authorization' if held else '',
            confirmed='1')), success=success)

    def dispatch(self):
        return self.finish(self.post(f'/recovery/service/{self.identity}/dispatch', dict(
            fingerprint=recovery.file_hash(self.inbox / (self.identity+'.approved.json')),
            reference='SYNTHETIC dispatch', confirmed='1')))

    def service(self, next_send=None, *, advance=0, rejected=False):
        target = SimulationTarget(self.values['simulation'], max_requests=10000)
        if next_send:
            target.data['next_send'] = next_send
            target._save()
        config = target.config
        config.HISTORICAL_RECOVERY_INBOX = str(self.inbox)
        config.HISTORICAL_RECOVERY_CONTRACT_SHA256 = self.values['contract_hash']
        if rejected:
            config.HISTORICAL_RECOVERY_CONTRACT_SHA256 = '0'*64
        clock = Clock()
        clock.value = time.time() + advance
        from sync_ledger import Ledger
        with Ledger(self.live) as ledger:
            service_tick(ledger, config, validate_activation(self.live, config), dt.datetime(2026, 10, 11),
                delivery_options(config), target=target, storage_check=self.controller.storage_check,
                clock=clock, monotonic=clock, sleep=clock.sleep)
        return target

    def get_details(self):
        return self.client.get(f'/recovery/service/{self.identity}', base_url=ORIGIN)

    def test_preview_selection_approval_dispatch_service_result_end_to_end(self):
        with ExitStack() as stack:
            stack.enter_context(mock.patch('requests.sessions.Session.request', side_effect=AssertionError('manager network forbidden')))
            send = stack.enter_context(mock.patch('durable_delivery.FrappeClient.send', side_effect=AssertionError('manager POST forbidden')))
            page = self.prepare()
            self.assertIn(b'Pending Approval', page.data)
            self.assertNotIn(b'SYNTHETIC_EMP', page.data)
            self.assertNotIn(str(self.live).encode(), page.data)
            self.assertEqual(self.live.read_bytes(), self.live_original)
            self.approve()
            self.assertEqual(list(self.inbox.glob('*.request.json')), [])
            page = self.dispatch()
            self.assertIn(b'Queued for service', page.data)
            self.assertEqual(self.live.read_bytes(), self.live_original)
            send.assert_not_called()
        target = self.service()
        self.assertEqual(target.calls.count('POST'), 1)
        self.assertIn(b'Recovered', self.get_details().data)
        before = self.live.read_bytes()
        self.get_details()
        self.assertEqual(self.live.read_bytes(), before)

    def test_repeated_browser_posts_reuse_draft_approval_and_dispatch(self):
        self.prepare()
        first = self.identity
        self.prepare()
        self.assertEqual(self.identity, first)
        self.approve()
        approval = (self.inbox / (first+'.approved.json')).read_bytes()
        self.approve()
        self.assertEqual((self.inbox / (first+'.approved.json')).read_bytes(), approval)
        self.dispatch()
        dispatched = (self.inbox / (first+'.request.json')).read_bytes()
        self.dispatch()
        self.assertEqual((self.inbox / (first+'.request.json')).read_bytes(), dispatched)
        self.service()
        self.assertNotIn('POST', self.service().calls)

    def test_manager_restart_and_close_do_not_start_or_stop_the_service(self):
        self.prepare()
        self.approve()
        self.dispatch()
        self.state.close()
        self.assertEqual(self.live.read_bytes(), self.live_original)
        self.service()
        fresh = Controller(acl_check=self.controller.acl_check, storage_check=self.controller.storage_check,
            sandbox_root=self.root, service_query=mock.Mock(side_effect=AssertionError('no SCM operation')))
        app = create_app(fresh, self.values, demo=True)
        self.addCleanup(app.extensions['manager'].close)
        page = app.test_client().get(f'/recovery/service/{self.identity}', base_url=ORIGIN)
        self.assertIn(b'Recovered', page.data)
        fresh.service_query.assert_not_called()

    def test_cancellation_is_durable_and_never_sends(self):
        self.prepare()
        self.approve()
        self.dispatch()
        path = f'/recovery/service/{self.identity}/cancel'
        self.finish(self.post(path, dict(reference='SYNTHETIC cancellation', confirmed='1')))
        self.finish(self.post(path, dict(reference='SYNTHETIC cancellation', confirmed='1')))
        self.assertIn(b'Cancellation requested', self.get_details().data)
        self.assertNotIn('POST', self.service().calls)
        self.assertIn(b'Needs Review', self.get_details().data)

    def test_global_service_owner_prevents_another_sender(self):
        self.prepare()
        self.approve()
        self.dispatch()
        errors = []
        import threading
        def attempt():
            try:
                self.service()
            except Exception as error:
                errors.append(type(error).__name__)
        with worker_lock(self.live):
            thread = threading.Thread(target=attempt)
            thread.start()
            thread.join(15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ['StartupError'])
        self.assertEqual(self.live.read_bytes(), self.live_original)

    def test_local_preview_and_offline_or_compatibility_reports_never_authorize(self):
        summary = Path(self.values['recovery_summary'])
        for update in (dict(purpose='local_state_preview'), dict(purpose='offline_comparison'),
                       dict(compatibility_status='reads_complete'), dict(status='unverified')):
            original = summary.read_bytes()
            data = json.loads(original)
            data.update(update)
            summary.write_text(json.dumps(data))
            self.state.values['recovery_summary_hash'] = recovery.file_hash(summary)
            self.prepare(success=False)
            self.assertEqual(list(self.inbox.glob('*.draft.json')), [])
            summary.write_bytes(original)

    def test_production_contract_blocks_all_mutating_actions(self):
        self.prepare()
        contract = Path(self.values['contract'])
        original = contract.read_bytes()
        data = json.loads(original)
        data['destination_environment'] = 'production'
        contract.write_text(json.dumps(data))
        self.state.values['contract_hash'] = recovery.file_hash(contract)
        self.prepare(success=False)
        self.assertIn('recovery_production_disabled', self.state.job['error'])
        for action in ('approve', 'dispatch', 'cancel'):
            self.finish(self.post(f'/recovery/service/{self.identity}/{action}', dict(ids=['1'],
                fingerprint='0'*64, reference='SYNTHETIC', confirmed='1')), success=False)
            self.assertIn('recovery_production_disabled', self.state.job['error'])
        self.assertEqual(list(self.inbox.glob('*.approved.json')), [])
        contract.write_bytes(original)

    def test_missing_profile_or_unapproved_ids_fail_safely(self):
        self.state.values.pop('recovery_summary_hash')
        page = self.client.get('/recovery/service/'+'a'*32, base_url=ORIGIN)
        self.assertEqual(page.status_code, 400)
        self.assertIn(b'approved snapshot/hash', page.data)
        self.prepare(success=False)
        self.assertIn('startup profile', self.state.job['error'])
        self.state.values['recovery_summary_hash'] = self.values['recovery_summary_hash']
        for ids in ([], ['99'], ['1', '1']):
            self.prepare(ids, success=False)
        self.assertEqual(list(self.inbox.glob('*.draft.json')), [])

    def test_security_modes_csrf_host_and_paths_cannot_bypass_workflow(self):
        for mode in ('monitor_only', 'investigation_only'):
            app = create_app(self.controller, self.values, **{mode: True})
            self.addCleanup(app.extensions['manager'].close)
            with mock.patch.object(self.controller, 'service_workflow') as call:
                for path in ('/recovery/service/prepare', '/recovery/service/refresh',
                             '/recovery/service/'+'a'*32, '/recovery/service/'+'a'*32+'/dispatch'):
                    for method in ('GET', 'POST'):
                        self.assertEqual(app.test_client().open(path, base_url=ORIGIN, method=method).status_code, 403)
                call.assert_not_called()
        self.assertEqual(self.client.post('/recovery/service/prepare', base_url=ORIGIN).status_code, 403)
        self.assertEqual(self.client.get('/recovery/service/invalid', base_url=ORIGIN).status_code, 400)
        self.assertEqual(self.client.get('/recovery/service/invalid', base_url=ORIGIN, headers={'Host':'evil.test'}).status_code, 400)
        self.assertEqual(list(self.inbox.glob('*.draft.json')), [])

    def test_safe_private_diagnostics_exclude_raw_errors_and_employee_information(self):
        with mock.patch.object(self.controller, 'storage_check', side_effect=OSError('SECRET_TOKEN PRIVATE_EMPLOYEE C:/sensitive/path')):
            self.prepare(success=False)
        self.assertNotIn('SECRET', self.state.job['error'])
        for path in Path(self.values['output']).glob('recovery-*.jsonl'):
            for line in path.read_text().splitlines():
                value = json.loads(line)
                self.assertEqual(set(value), {'reference','stage','code','event'})
                self.assertNotIn('SECRET', line)
                self.assertNotIn('PRIVATE_EMPLOYEE', line)

    def test_crash_and_restart_status_requires_independent_verification(self):
        self.prepare()
        self.approve()
        self.dispatch()
        with self.assertRaises(KeyboardInterrupt):
            self.service('crash_after')
        self.assertIn(b'Needs Review', self.get_details().data)
        target = self.service()
        self.assertNotIn('POST', target.calls)
        self.assertIn(b'Already Exists', self.get_details().data)

    def test_uncertain_timeout_is_not_displayed_as_recovered_or_resubmitted(self):
        self.prepare()
        self.approve()
        self.dispatch()
        self.service('timeout_after')
        page = self.get_details()
        self.assertIn(b'Needs Review', page.data)
        self.assertNotIn(b'<td>Recovered</td>', page.data)
        # Include the preflight pacing time plus the maximum jittered cooldown.
        self.assertNotIn('POST', self.service(advance=60).calls)
        self.assertIn(b'Already Exists', self.get_details().data)

    def test_normal_delivery_before_dispatch_is_skipped_by_recovery(self):
        from sync_ledger import Ledger
        from durable_delivery import FrappeClient, deliver_batch
        self.prepare()
        self.approve()
        target = SimulationTarget(self.values['simulation'])
        with worker_lock(self.live), Ledger(self.live) as ledger:
            deliver_batch(ledger, FrappeClient(target.config, target.request), sleep=lambda _: None)
        self.dispatch()
        self.assertNotIn('POST', self.service().calls)
        self.assertIn(b'Skipped', self.get_details().data)

    def test_changed_input_after_approval_cannot_be_dispatched(self):
        self.prepare()
        self.approve()
        path = Path(self.values['recovery_summary'])
        path.write_bytes(path.read_bytes()+b' ')
        self.finish(self.post(f'/recovery/service/{self.identity}/dispatch', dict(
            fingerprint=recovery.file_hash(self.inbox / (self.identity+'.approved.json')),
            reference='SYNTHETIC dispatch', confirmed='1')), success=False)
        self.assertEqual(list(self.inbox.glob('*.request.json')), [])

    def test_service_rejection_and_unknown_audit_data_are_safe(self):
        self.prepare()
        self.approve()
        self.dispatch()
        self.service(rejected=True)
        page = self.get_details()
        self.assertIn(b'Needs Review', page.data)
        self.assertIn(b'Request verification failed', page.data)

    def test_demo_fixture_preparation_never_uses_production_mutex(self):
        from manager_app.demo import create_demo
        with mock.patch('durable_startup.worker_lock', side_effect=AssertionError('no startup lock')):
            temporary, controller, values = create_demo()
            try:
                self.assertTrue(Path(values['recovery_ledger']).is_file())
                self.assertNotEqual(values['backup'], values['recovery_ledger'])
            finally:
                controller.close()
                temporary.cleanup()

    def test_logging_failure_after_dispatch_is_reconcilable_without_redispatch(self):
        from historical_attendance.diagnostics import Trace, DiagnosticError
        self.prepare()
        self.approve()
        original = Trace.emit
        def failed(trace, event, code):
            if event == 'complete':
                raise DiagnosticError('diagnostics', 'diagnostics_unavailable', trace.reference)
            return original(trace, event, code)
        with mock.patch.object(Trace, 'emit', failed):
            self.finish(self.post(f'/recovery/service/{self.identity}/dispatch', dict(
                fingerprint=recovery.file_hash(self.inbox / (self.identity+'.approved.json')),
                reference='SYNTHETIC dispatch', confirmed='1')), success=False)
        self.assertIn('may already be published', self.state.job['error'])
        path = self.inbox / (self.identity+'.request.json')
        published = path.read_bytes()
        self.dispatch()
        self.assertEqual(path.read_bytes(), published)

    def test_status_never_associates_a_replaced_source_with_old_approval(self):
        self.prepare()
        with closing(sqlite3.connect(self.live)) as db, db:
            db.execute("UPDATE punches SET user_id='PRIVATE_SOURCE_SWAP' WHERE id=1")
        page = self.get_details()
        self.assertEqual(page.status_code, 400)
        self.assertIn(b'canonical_source_changed', page.data)
        self.assertNotIn(b'PRIVATE_SOURCE_SWAP', page.data)

    def test_record_specific_hold_selection_and_approval(self):
        # Change only synthetic fixtures, then regenerate independently verified evidence.
        from historical_attendance.engine import investigate
        from historical_attendance.client import ReadOnlyFrappeClient
        from historical_attendance.repository import SQLiteRepository
        from historical_attendance.reporting import write_csv
        for path in (self.source, self.live):
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE punches SET state='rejected',error_category='cutover_hold' WHERE id IN (1,2,3)")
        self.original = self.source.read_bytes()
        self.values['backup_hash'] = self.state.values['backup_hash'] = recovery.file_hash(self.source)
        target = SimulationTarget(self.values['simulation'])
        contract = json.loads(Path(self.values['contract']).read_text())
        reader = ReadOnlyFrappeClient(target.url, 'SYNTHETIC', 'SYNTHETIC', target.request)
        rows, summary = investigate(SQLiteRepository(self.source), reader, dt.datetime(2026,10,11),
            dt.datetime(2026,10,11,23,59,59), contract['device_ids'], contract)
        detail = Path(self.values['recovery_summary']).parent/'synthetic-details.csv'
        with detail.open('w', newline='', encoding='utf-8') as stream:
            write_csv(stream, rows)
        summary['contract_file_sha256'] = self.values['contract_hash']
        summary['reports'] = [dict(file=detail.name, sha256=recovery.file_hash(detail))]
        Path(self.values['recovery_summary']).write_text(json.dumps(summary))
        self.state.values['recovery_summary_hash'] = recovery.file_hash(self.values['recovery_summary'])
        self.state.recovery_preview = self.preview()
        self.prepare(ids=('1','2'))
        self.approve(ids=('1','2'), success=False)
        self.assertIn('explicit_selected_hold_authorization_required', self.state.job['error'])
        self.approve(ids=('1','2'), held=('1','2'))
        self.dispatch()
        self.service()
        with closing(sqlite3.connect(self.live)) as db:
            self.assertEqual(db.execute('SELECT state FROM punches WHERE id=1').fetchone()[0], 'synced')
            self.assertEqual(db.execute('SELECT state FROM punches WHERE id=2').fetchone()[0], 'synced')
            self.assertEqual(db.execute('SELECT error_category FROM punches WHERE id=3').fetchone()[0], 'cutover_hold')
