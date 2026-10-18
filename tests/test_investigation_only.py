"""Read-only mode acceptance: synthetic source, mocked authenticated/paginated API."""
from contextlib import ExitStack, closing, redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest import mock

from historical_attendance.engine import CLAIMS
from historical_attendance.recovery import file_hash
from historical_attendance.recovery_simulation import SimulationTarget
from manager_app.demo import create_demo
from manager_app.web import ORIGIN, create_app
from scripts import biometric_manager as launcher, investigate_attendance as cli
from test_manager import ManagerFixture


class InvestigationOnlyTests(ManagerFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(self.controller, self.values, demo=True, investigation_only=True)
        self.state = self.app.extensions['manager']
        self.addCleanup(self.state.close)
        self.client = self.app.test_client()
        self.client.get('/', base_url=ORIGIN)
        self.calls = []

    def get(self, path):
        return self.client.get(path, base_url=ORIGIN)

    def post(self, path, data=None, **options):
        with self.client.session_transaction(base_url=ORIGIN) as session:
            token = session['csrf']
        return self.client.post(path, base_url=ORIGIN, data=dict(csrf=token, **(data or {})), **options)

    def finish(self, response, status='complete'):
        self.assertEqual(response.status_code, 303)
        self.state.thread.join(15)
        self.assertFalse(self.state.thread.is_alive())
        self.assertEqual(self.state.job['status'], status, self.state.job)
        return self.get(self.state.job['destination'])

    def investigate(self, **changes):
        data = dict(start=self.values['start'], end=self.values['end'], devices=['FP1_DEVICE_01'],
                    api_key='SYNTHETIC_KEY', api_secret='SYNTHETIC_SECRET', backup_ack='1')
        data.update(changes)
        return self.post('/investigation/run', data)

    def contract(self, *, production=True, verified=False, read_only=True):
        path = Path(self.values['contract'])
        data = json.loads(path.read_text())
        if production:
            data.update(site_url='https://erp.myfpf.com', destination_environment='production')
        data['verification'] = {claim: verified for claim in CLAIMS}
        data['verification']['read_only_api_verified'] = read_only
        path.write_text(json.dumps(data), encoding='utf-8')
        self.state.values.update(site=data['site_url'], contract_hash=file_hash(path))
        return data

    def mock_api(self, *, many_pages=False):
        target = SimulationTarget(self.values['simulation'])
        site = self.state.values['site']
        def request(method, url, **options):
            # The production URL is asserted, but NEVER requested over the network.
            self.assertEqual(method, 'GET')
            self.assertTrue(url.startswith(site + '/api/'))
            self.assertEqual(options['timeout'], 10)
            self.assertFalse(options['allow_redirects'])
            self.assertEqual(options['headers']['Authorization'], 'token SYNTHETIC_KEY:SYNTHETIC_SECRET')
            self.calls.append((method, url))
            params = options['params']
            if many_pages and params.get('doctype') == 'Employee Checkin':
                return mock.Mock(status_code=200, json=lambda: {'message': 1000})
            if many_pages and url.endswith('/Employee%20Checkin'):
                row = dict(name='SYNTHETIC_' + str(params['limit_start']), employee='SYNTHETIC_EMP',
                    time='2026-10-11 08:00:00', log_type='', device_id='FP1_DEVICE_01', modified='2026-10-11 00:00:00')
                return mock.Mock(status_code=200, json=lambda: {'data': [row]})
            return target.request(method, target.url + url[len(site):], **options)
        def mocked_cli(args, **options):
            # Inline only for call assertions; real bounded spawning is exercised below
            # and in the unchanged deadline tests. The real GET budget/client is used.
            options.pop('runner')
            return cli.main(args, request=request, runner=lambda task, kwargs, **limits: task(**kwargs), **options)
        self.controller.cli = mocked_cli

    def test_navigation_dashboard_devices_and_investigation_are_available(self):
        for path in ('/', '/devices', '/investigation'):
            page = self.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'Investigation only', page.data)
            self.assertIn(b'href="/investigation"', page.data)
            self.assertNotIn(b'href="/recovery"', page.data)
        for destination in ('dashboard_page', 'devices_page'):
            self.finish(self.post('/refresh', {'destination': destination}))
            self.assertEqual(self.state.dashboard['integrity'], 'OK')
        self.assertEqual(self.client.post('/investigation/run', base_url=ORIGIN).status_code, 403)
        self.assertEqual(self.client.get('/', base_url=ORIGIN, headers={'Host': 'evil.test'}).status_code, 400)

    def test_all_recovery_routes_block_direct_get_head_and_post_without_side_effects(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        routes = ['/recovery', '/recovery/refresh', '/recovery/' + self.batch['id']]
        routes += [f"/recovery/{self.batch['id']}/{action}" for action in ('approve', 'simulate', 'export', 'execute')]
        with ExitStack() as patches:
            operations = [patches.enter_context(mock.patch.object(self.controller, name)) for name in
                          ('batches', 'batch', 'approve', 'simulate', 'export_recovery', 'investigate')]
            operations.append(patches.enter_context(mock.patch.object(self.state, 'start')))
            for route in routes:
                with self.subTest(route=route):
                    self.assertEqual(self.get(route).status_code, 403)
                    self.assertEqual(self.client.head(route, base_url=ORIGIN).status_code, 403)
                    self.assertEqual(self.post(route, dict(confirmed='1', ids=['1'], reference='SYNTHETIC',
                        fingerprint=self.batch['fingerprint']), headers={'Origin': ORIGIN}).status_code, 403)
            for operation in operations:
                operation.assert_not_called()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_verified_comparison_and_protected_exports_use_existing_bounded_cli(self):
        page = self.finish(self.investigate())
        summary = self.state.investigation['summary']
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual(summary['totals']['potentially_missing'], 1)
        self.assertFalse(summary['recovery_authorized'])
        self.assertFalse(summary['execution_supported'])
        self.assertEqual(summary['execution_limits'], dict(max_get_requests=32, deadline_seconds=120, request_timeout_seconds=10))
        self.assertTrue(all(row['eligible_for_recovery'] == 'False' for row in self.state.investigation['rows']))
        self.assertIn(b'Complete', page.data)
        self.finish(self.post('/investigation/export'))
        result = self.state.investigation
        self.assertEqual(file_hash(Path(result['csv_path'])), summary['reports'][0]['sha256'])
        self.assertEqual(json.loads(Path(result['json_path']).read_text()), summary)

    def test_production_reads_complete_stays_unverified_with_unknown_claims_false(self):
        contract = self.contract()
        self.mock_api()
        scenario = Path(self.values['simulation']).read_bytes()
        page = self.finish(self.investigate(production_ack='1'))
        summary = self.state.investigation['summary']
        self.assertEqual(summary['compatibility_status'], 'reads_complete')
        self.assertEqual(summary['status'], 'unverified')
        self.assertEqual(summary['totals']['potentially_missing'], 0)
        self.assertEqual(summary['totals']['unable_to_verify'], 1)
        self.assertEqual(set(summary['unverified_claims']), set(CLAIMS))
        self.assertFalse(summary['recovery_authorized'])
        self.assertTrue(0 < len(self.calls) <= 32)
        self.assertEqual(Path(self.values['simulation']).read_bytes(), scenario)
        self.assertEqual(json.loads(Path(self.values['contract']).read_text()), contract)
        self.assertIn(b'Unverified deployment claims', page.data)
        self.assertIn(b'employee lookup database equality', page.data)
        self.assertIn(b'Completed reads do not establish', page.data)
        self.assertNotIn(b'SYNTHETIC_SECRET', page.data)

    def test_production_cannot_become_verified_even_when_all_claims_are_true(self):
        self.contract(verified=True)
        self.mock_api()
        self.finish(self.investigate(production_ack='1'))
        summary = self.state.investigation['summary']
        self.assertEqual(summary['status'], 'unverified')
        self.assertIn('production_compatibility_only_no_reconciliation', summary['blockers'])
        self.assertEqual(summary['totals']['potentially_missing'], 0)

    def test_missing_production_ack_or_endpoint_safety_blocks_before_api(self):
        for ack, read_only in (('0', True), ('1', False)):
            with self.subTest(ack=ack, read_only=read_only):
                self.contract(read_only=read_only)
                self.mock_api()
                self.finish(self.investigate(production_ack=ack), status='blocked')
                self.assertEqual(self.calls, [])
                self.assertIsNone(self.state.investigation)

    def test_incomplete_nonproduction_evidence_blocks_comparison_and_explains_why(self):
        self.contract(production=False)
        self.mock_api()
        page = self.finish(self.investigate())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state.investigation['summary']['status'], 'unverified')
        self.assertIn(b'Comparison evidence is incomplete', page.data)
        self.assertEqual(self.state.investigation['summary']['totals']['potentially_missing'], 0)

    def test_strict_date_device_validation_cannot_change_paths_or_limits(self):
        with mock.patch.object(self.controller, 'investigate') as investigate:
            for changes in (dict(devices=[]), dict(devices=['FP1_DEVICE_01']*2), dict(devices=['UNKNOWN']),
                            dict(floor='FP2'), dict(start='bad'), dict(end='2000-01-01 00:00:00'),
                            dict(start='2026-10-11T00:00:00+03:00'), dict(backup_ack='0'),
                            dict(backup=str(self.source)), dict(max_get_requests='999')):
                with self.subTest(changes=changes):
                    self.assertEqual(self.investigate(**changes).status_code, 400)
            investigate.assert_not_called()

    def test_historical_hold_and_source_bytes_are_preserved(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE punches SET state='rejected',error_category='cutover_hold' WHERE id=1")
        self.original = self.source.read_bytes()  # Synthetic fixture setup only.
        self.finish(self.investigate())
        self.assertEqual(self.state.investigation['summary']['historical_holds'], 1)
        self.assertEqual(self.state.investigation['rows'][0]['historical_hold'], 'True')
        self.assertEqual(self.state.investigation['rows'][0]['eligible_for_recovery'], 'False')

    def test_source_directory_cannot_receive_reports(self):
        self.state.values['output'] = str(self.source.parent)
        self.mock_api()
        self.finish(self.investigate(), status='blocked')
        self.assertEqual(self.calls, [])
        self.assertFalse(list(self.source.parent.glob('investigation_*.json')))

    def test_hard_get_budget_is_retained_on_incomplete_pagination(self):
        self.contract()
        self.mock_api(many_pages=True)
        self.finish(self.investigate(production_ack='1'))
        summary = self.state.investigation['summary']
        self.assertEqual(len(self.calls), 32)
        self.assertEqual(summary['api_requests_made'], 32)
        self.assertEqual(summary['compatibility_status'], 'not_verified')
        self.assertEqual(summary['totals']['potentially_missing'], 0)
        self.assertIn('investigation_request_limit_exceeded', summary['blockers'])

    def test_startup_modes_are_exclusive_and_cli_forwards_investigation_flag(self):
        with self.assertRaises(ValueError):
            create_app(self.controller, self.values, monitor_only=True, investigation_only=True)
        with mock.patch('manager_app.runtime.serve') as serve, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                launcher.main(['--monitor-only', '--investigation-only'])
            serve.assert_not_called()
        for args in (['--demo'], ['--profile', 'C:/SYNTHETIC/profile.json']):
            with mock.patch('manager_app.runtime.serve') as serve, \
                    mock.patch('manager_app.profile.load_profile', return_value=self.values), \
                    mock.patch('manager_app.demo.create_demo', return_value=(mock.Mock(), self.controller, self.values)):
                self.assertEqual(launcher.main(args + ['--investigation-only', '--no-browser']), 0)
                client = serve.call_args.args[0].test_client()
                self.assertEqual(client.get('/investigation', base_url=ORIGIN).status_code, 200)
                self.assertEqual(client.get('/recovery', base_url=ORIGIN).status_code, 403)

    def test_investigation_demo_does_not_prepare_recovery_or_preset_results(self):
        with mock.patch('manager_app.demo.recovery.prepare') as prepare, \
                mock.patch('manager_app.demo.investigate') as investigate:
            temporary, controller, values = create_demo(investigation_only=True)
            self.addCleanup(temporary.cleanup)
            self.addCleanup(controller.close)
            self.assertFalse((Path(values['workspace']) / 'recovery-state.recovery-audit').exists())
            prepare.assert_not_called()
            investigate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
