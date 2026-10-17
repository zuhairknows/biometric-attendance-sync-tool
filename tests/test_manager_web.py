"""Flask acceptance uses only synthetic ledgers, fake SCM and in-process ERP fixtures."""
import json
from pathlib import Path
import threading
import unittest
from unittest import mock

from manager_app import runtime
from manager_app.controller import ManagerError
from manager_app.profile import device_catalog, load_profile
from manager_app.web import ORIGIN, create_app
from historical_attendance.recovery import file_hash
from test_manager import ManagerFixture


class WebTests(ManagerFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(self.controller, self.values, demo=True)
        self.state = self.app.extensions['manager']
        self.addCleanup(self.state.close)
        self.client = self.app.test_client()
        self.client.get('/', base_url=ORIGIN)

    def get(self, path, **options):
        return self.client.get(path, base_url=ORIGIN, **options)

    def post(self, path, data=None, **options):
        with self.client.session_transaction(base_url=ORIGIN) as session:
            token = session['csrf']
        return self.client.post(path, base_url=ORIGIN, data=dict(csrf=token, **(data or {})), **options)

    def complete(self, response):
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        self.state.thread.join(15)
        self.assertFalse(self.state.thread.is_alive())
        self.assertEqual(self.state.job['status'], 'complete', self.state.job)
        return self.get(self.state.job['destination'])

    def investigate(self, **changes):
        args = dict(start=self.values['start'], end=self.values['end'], devices=['FP1_DEVICE_01'],
                    api_key='DEMO', api_secret='DEMO', backup_ack='1')
        args.update(changes)
        return self.post('/investigation/run', args)

    def test_startup_is_idle_and_all_sections_render(self):
        self.controller.service_query = mock.Mock(side_effect=AssertionError('no startup calls'))
        for route in ('/', '/devices', '/investigation', '/recovery'):
            response = self.get(route)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b'DEMO_DEVICE_SECRET', response.data)
        self.assertFalse(self.app.debug)
        self.assertIsNone(self.state.thread)
        self.controller.service_query.assert_not_called()

    def test_host_remote_and_proxy_headers_are_rejected(self):
        for host in ('evil.test', 'localhost:8765', '127.0.0.1', '127.0.0.1:9999', '127.0.0.1.evil.test:8765'):
            self.assertEqual(self.get('/', headers={'Host': host}).status_code, 400)
        self.assertEqual(self.get('/', environ_overrides={'REMOTE_ADDR': '192.0.2.1'}).status_code, 400)
        for header in ('Forwarded', 'X-Forwarded-Host', 'X-Forwarded-For'):
            self.assertEqual(self.get('/', headers={header: 'evil.test'}).status_code, 400)

    def test_every_action_requires_csrf(self):
        for route in ('/refresh', '/investigation/run', '/investigation/export', '/recovery/refresh',
                      f"/recovery/{self.batch['id']}/approve", f"/recovery/{self.batch['id']}/simulate"):
            self.assertEqual(self.client.post(route, base_url=ORIGIN).status_code, 403)
        with self.client.session_transaction(base_url=ORIGIN) as session:
            token = session['csrf']
        for headers in ({'Origin': 'https://evil.test'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.post('/refresh', headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/refresh', base_url=ORIGIN, data={'csrf': 'wrong'}).status_code, 403)
        second = self.app.test_client()
        self.assertEqual(second.post('/refresh', base_url=ORIGIN, data={'csrf': token}).status_code, 403)

    def test_security_headers_and_payload_limit(self):
        response = self.get('/')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertIn("form-action 'self'", response.headers['Content-Security-Policy'])
        self.assertIn('HttpOnly', response.headers.get('Set-Cookie', '') or self.client.get('/', base_url=ORIGIN).headers.get('Set-Cookie', ''))
        response = self.client.post('/refresh', base_url=ORIGIN, data={'large': 'a'*40000})
        self.assertEqual(response.status_code, 413)

    def test_forbidden_browser_methods_and_arbitrary_downloads(self):
        for method in ('PUT', 'PATCH', 'DELETE'):
            response = self.client.open('/', base_url=ORIGIN, method=method)
            self.assertEqual(response.status_code, 405)
        for route in ('/download?file=C:/Windows/system.ini', '/files/../profile.json', '/recovery/../../config', '/static/../profile.py'):
            self.assertIn(self.get(route).status_code, (400, 404))

    def test_dashboard_refresh_and_degraded_states(self):
        self.controller.service_query = mock.Mock(return_value='STOPPED')
        page = self.complete(self.post('/refresh'))
        self.assertIn(b'STOPPED', page.data)
        self.assertIn(b'FP1_DEVICE_01', page.data)
        self.assertEqual(self.state.dashboard['total'], 3)
        self.assertTrue(self.state.dashboard['refreshed'])
        self.state.values['monitor_ledger'] = str(self.root / 'missing.sqlite3')
        self.controller.service_query.side_effect = RuntimeError('PRIVATE SERVICE SECRET')
        page = self.complete(self.post('/refresh'))
        self.assertIn(b'Unavailable', page.data)
        self.assertNotIn(b'PRIVATE SERVICE', page.data)
        self.assertEqual(page.data.count('<strong>—</strong>'.encode()), 7)
        self.assertFalse((self.root / 'missing.sqlite3').exists())

    def test_twelve_devices_grouped_without_inventing_diagnostics(self):
        page = self.get('/devices')
        for number in range(1, 13):
            self.assertIn(f'DEVICE_{number:02}'.encode(), page.data)
        self.assertIn(b'7 configured', page.data)
        self.assertIn(b'5 configured', page.data)
        self.assertIn(b'Not observed', page.data)
        self.assertIn(b'192.0.2.1', page.data)
        self.assertNotIn(b'DEMO_DEVICE_SECRET', page.data)
        self.complete(self.post('/refresh'))
        self.assertIn(b'2026-10-11', self.get('/devices').data)

    def test_config_is_parsed_without_execution_or_credential_display(self):
        config = self.root / 'config.py'
        marker = self.root / 'should-not-exist'
        config.write_text(f"raise RuntimeError('MUST NOT EXECUTE')\ndevices=[{{'device_id':'FP1_DEVICE_01','ip':'192.0.2.1','port':4371,'password':open({str(marker)!r},'w')}}]")
        self.assertEqual(device_catalog(str(config))[0]['port'], 4371)
        self.assertFalse(marker.exists())
        config.write_text('devices = generate_devices()')
        app = create_app(self.controller, dict(self.values, device_config=str(config)))
        response = app.test_client().get('/devices', base_url=ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'could not be verified', response.data)
        self.assertNotIn(b'192.0.2.1', response.data)

    def test_investigation_keeps_limits_pins_and_private_exports(self):
        page = self.complete(self.investigate())
        summary = self.state.investigation['summary']
        self.assertEqual(summary['execution_limits'], dict(max_get_requests=32, deadline_seconds=120, request_timeout_seconds=10))
        self.assertEqual(summary['status'], 'complete')
        self.assertFalse(summary['recovery_authorized'])
        self.assertNotIn(b'SYNTHETIC_EMP', page.data)
        self.assertIn(b'SYNTHETIC_EMP', self.get('/investigation?identifiers=1').data)
        self.assertTrue(list(Path(self.values['output']).glob('*.csv')))
        self.complete(self.post('/investigation/export'))

    def test_filtering_pagination_and_validation(self):
        self.complete(self.investigate(devices=['FP1_DEVICE_01', 'FP2_DEVICE_08']))
        rows = self.state.investigation['rows']
        self.state.investigation['rows'] = rows*70
        page = self.get('/investigation?floor=FP2&size=25&page=2&q=FP2_DEVICE_08')
        self.assertIn(b'70 results', page.data)
        self.assertIn(b'Page 2 of 3', page.data)
        self.assertEqual(page.data.count(b'<td class="mono">FP2_DEVICE_08'), 25)
        for query in ('size=10000', 'page=-1', 'classification=bogus', 'floor=FP3'):
            self.assertEqual(self.get('/investigation?' + query).status_code, 400)

    def test_invalid_scope_and_path_injection_never_reaches_api(self):
        self.controller.cli = mock.Mock(side_effect=AssertionError('no API'))
        for changes in (dict(devices=['UNKNOWN']), dict(devices=['FP1_DEVICE_01']*2), dict(floor='FP2'),
                        dict(start='bad'), dict(end='2020-01-01 00:00:00'), dict(backup_ack='0'),
                        dict(output='C:/Windows'), dict(backup='../source.sqlite3'), dict(site='https://evil.test')):
            self.assertEqual(self.investigate(**changes).status_code, 400)
        self.controller.cli.assert_not_called()

    def test_production_compatibility_cannot_authorize_recovery(self):
        contract = Path(self.values['contract'])
        data = json.loads(contract.read_text())
        data['destination_environment'] = 'production'
        data['verification'] = {'read_only_api_verified': True}
        contract.write_text(json.dumps(data))
        self.state.values['contract_hash'] = file_hash(contract)
        page = self.complete(self.investigate(production_ack='1'))
        self.assertIn(b'Unverified', page.data)
        self.assertIn(b'not confirmed missing', page.data)
        self.assertFalse(self.state.investigation['summary']['recovery_authorized'])
        self.assertEqual(self.state.investigation['summary']['totals']['potentially_missing'], 0)

    def test_missing_production_acknowledgement_fails_closed(self):
        contract = Path(self.values['contract'])
        data = json.loads(contract.read_text())
        data.update(destination_environment='production')
        contract.write_text(json.dumps(data))
        self.state.values['contract_hash'] = file_hash(contract)
        response = self.investigate()
        self.state.thread.join(10)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.state.job['status'], 'blocked')
        self.assertIsNone(self.state.investigation)

    def test_approval_requires_selected_pending_ids_and_explicit_confirmation(self):
        identity = self.batch['id']
        page = self.get('/recovery/' + identity)
        self.assertIn(b'Simulation Only', page.data)
        for changes in (dict(confirmed='0'), dict(ids=[]), dict(ids=['999']), dict(fingerprint='changed')):
            args = dict(fingerprint=self.batch['fingerprint'], ids=['1'], confirmed='1', reference='SYNTHETIC review')
            args.update(changes)
            self.assertEqual(self.post(f'/recovery/{identity}/approve', args).status_code, 400)
        self.assertEqual(self.controller.batch(self.values['workspace'], identity)['counts'], {'Pending Approval': 3})

    def test_approval_simulation_repeated_execution_and_audit_export(self):
        self.complete(self.post('/recovery/refresh'))
        identity = self.batch['id']
        self.assertIn(identity[:12].encode(), self.get('/recovery').data)
        args = dict(fingerprint=self.batch['fingerprint'], ids=['1'], confirmed='1', reference='SYNTHETIC browser review')
        self.complete(self.post(f'/recovery/{identity}/approve', args))
        with mock.patch('socket.socket', side_effect=AssertionError('no network')):
            for _ in range(2):
                self.complete(self.post(f'/recovery/{identity}/simulate', dict(fingerprint=self.batch['fingerprint'], confirmed='1')))
        data = self.controller.batch(self.values['workspace'], identity)
        self.assertEqual(data['counts'], {'Recovered': 1, 'Pending Approval': 2})
        self.assertEqual(len(json.loads(Path(self.values['simulation']).read_text())['checkins']), 1)
        self.complete(self.post(f'/recovery/{identity}/export'))
        self.assertTrue(list(Path(self.values['output']).glob('recovery_*.json')))

    def test_non_synthetic_batch_cannot_be_approved_or_simulated(self):
        data = self.controller.batch(self.values['workspace'], self.batch['id'])
        data['metadata']['contract']['destination_environment'] = 'production'
        with mock.patch.object(self.controller, 'batch', return_value=data), mock.patch.object(self.controller, 'approve') as approve:
            page = self.get('/recovery/' + self.batch['id'])
            self.assertNotIn(b'Approve selected records', page.data)
            for action in ('approve', 'simulate'):
                self.assertEqual(self.post(f"/recovery/{self.batch['id']}/{action}").status_code, 400)
            approve.assert_not_called()
        self.assertEqual(self.post(f"/recovery/{self.batch['id']}/execute").status_code, 404)

    def test_output_protection_rejects_source_directory(self):
        self.state.values['output'] = str(self.source.parent)
        response = self.post(f"/recovery/{self.batch['id']}/export")
        self.assertEqual(response.status_code, 303)
        self.state.thread.join(10)
        self.assertEqual(self.state.job['status'], 'blocked')
        self.assertFalse(list(self.source.parent.glob('recovery_*.json')))

    def test_one_action_at_a_time_and_closing_browser_does_not_cancel(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.state.start('synthetic blocked work', lambda: release.wait(10), lambda _: None, '/')
        self.assertEqual(self.post('/refresh').status_code, 400)
        self.client = self.app.test_client()  # Simulates closing the original browser/session.
        self.assertFalse(self.controller.cancelled.is_set())
        release.set()
        self.state.thread.join(5)
        self.assertEqual(self.state.job['status'], 'complete')

    def test_worker_errors_never_expose_private_values(self):
        self.controller.dashboard = mock.Mock(side_effect=RuntimeError('PRIVATE EMPLOYEE TEST_SECRET'))
        response = self.post('/refresh')
        self.state.thread.join(5)
        page = self.get(response.location)
        self.assertNotIn(b'TEST_SECRET', page.data)
        self.assertNotIn(b'PRIVATE EMPLOYEE', page.data)
        self.assertEqual(self.state.job['status'], 'blocked')

    def test_server_binds_only_loopback_and_shutdown_is_independent(self):
        self.controller.service_query = mock.Mock()
        server = mock.Mock()
        server.run.side_effect = KeyboardInterrupt
        with mock.patch.object(runtime, 'create_server', return_value=server) as make, mock.patch.object(runtime.webbrowser, 'open') as browser:
            with self.assertRaises(KeyboardInterrupt):
                runtime.serve(self.app)
            self.assertEqual(make.call_args.kwargs['host'], '127.0.0.1')
            self.assertEqual(make.call_args.kwargs['port'], 8765)
            browser.assert_called_once_with(ORIGIN)
        server.close.assert_called_once()
        self.controller.service_query.assert_not_called()
        self.assertTrue(self.controller.cancelled.is_set())

    def test_startup_failure_does_not_open_browser(self):
        with mock.patch.object(runtime, 'create_server', side_effect=OSError('port busy')), mock.patch.object(runtime.webbrowser, 'open') as browser:
            with self.assertRaises(OSError):
                runtime.serve(self.app)
            browser.assert_not_called()
        self.assertTrue(self.controller.cancelled.is_set())

    def test_profile_rejects_credentials_relative_paths_and_unsafe_origin(self):
        profile = self.root / 'profile.json'
        for data in ({'api_key': 'PRIVATE'}, {'backup': '../file'}, {'site': 'https://key:SECRET@example.test'}, {'site': 'http://example.test'}):
            profile.write_text(json.dumps(data))
            with self.assertRaises(ManagerError):
                load_profile(str(profile), acl_check=lambda _: None)
        profile.write_text(json.dumps({'monitor_ledger': str(self.source)}))
        self.assertEqual(load_profile(str(profile), acl_check=lambda _: None)['monitor_ledger'], str(self.source))


if __name__ == '__main__':
    unittest.main()
