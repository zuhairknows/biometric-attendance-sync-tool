"""Monitoring gate acceptance: synthetic files, mocked service, no live integrations."""
from contextlib import ExitStack
import unittest
from unittest import mock

from manager_app.web import ORIGIN, create_app
from scripts import biometric_manager as launcher
from test_manager import ManagerFixture


class MonitorOnlyTests(ManagerFixture):
    def setUp(self):
        super().setUp()
        # Deliberately supply a complete valid recovery/investigation profile. The gate
        # must protect it even when omission of inputs would not block operations.
        self.app = create_app(self.controller, self.values, demo=True, monitor_only=True)
        self.state = self.app.extensions['manager']
        self.addCleanup(self.state.close)
        self.client = self.app.test_client()
        self.get('/')

    def get(self, path, **options):
        return self.client.get(path, base_url=ORIGIN, **options)

    def post(self, path, data=None, **options):
        with self.client.session_transaction(base_url=ORIGIN) as session:
            token = session['csrf']
        return self.client.post(path, base_url=ORIGIN, data=dict(csrf=token, **(data or {})), **options)

    def complete_refresh(self, destination='dashboard_page'):
        response = self.post('/refresh', {'destination': destination})
        self.assertEqual(response.status_code, 303)
        self.state.thread.join(10)
        self.assertFalse(self.state.thread.is_alive())
        self.assertEqual(self.state.job['status'], 'complete')
        return response

    def test_dashboard_devices_assets_and_diagnostic_job_remain_functional(self):
        response = self.complete_refresh()
        self.assertEqual(self.get(response.location).status_code, 200)
        status = self.get(response.location + '/status')
        self.assertEqual(status.json, {'status': 'complete'})
        self.assertEqual(self.state.dashboard['total'], 3)
        self.assertEqual(self.state.dashboard['integrity'], 'OK')
        self.complete_refresh('devices_page')
        for path in ('/', '/devices', '/static/manager.css', '/static/manager.js'):
            with self.subTest(path=path):
                with self.get(path) as response:
                    self.assertEqual(response.status_code, 200)
                with self.client.head(path, base_url=ORIGIN) as response:
                    self.assertEqual(response.status_code, 200)
        self.assertIn(b'FP2_DEVICE_12', self.get('/devices').data)

    def test_navigation_hides_disabled_sections_and_labels_mode(self):
        for path in ('/', '/devices'):
            response = self.get(path)
            self.assertIn(b'Monitoring only', response.data)
            self.assertIn(b'href="/devices"', response.data)
            self.assertNotIn(b'href="/investigation"', response.data)
            self.assertNotIn(b'href="/recovery"', response.data)
            self.assertIn(b'exports are disabled', response.data)

    def test_every_prohibited_route_blocks_get_head_and_valid_csrf_post_without_io(self):
        routes = []
        # Enumerate registered business routes, so additions cannot escape this check.
        for rule in self.app.url_map.iter_rules():
            if rule.endpoint in ('static', 'dashboard_page', 'devices_page', 'refresh', 'job_page', 'job_status'):
                continue
            path = rule.rule.replace('<identity>', self.batch['id'])
            if '<action>' in path:
                routes.extend(path.replace('<action>', action) for action in ('approve', 'simulate', 'export', 'execute'))
            else:
                routes.append(path)
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with ExitStack() as patches:
            operations = [patches.enter_context(mock.patch.object(self.controller, method,
                side_effect=AssertionError('blocked operation reached'))) for method in
                ('investigate', 'batches', 'batch', 'approve', 'simulate', 'export_recovery')]
            operations.append(patches.enter_context(mock.patch.object(self.state, 'start', side_effect=AssertionError('no job'))))
            operations.append(patches.enter_context(mock.patch('manager_app.web.validate_output', side_effect=AssertionError('no export'))))
            for path in routes:
                with self.subTest(path=path):
                    self.assertEqual(self.get(path).status_code, 403)
                    self.assertEqual(self.client.head(path, base_url=ORIGIN).status_code, 403)
                    self.assertEqual(self.post(path, dict(ids=['1'], confirmed='1',
                        reference='SYNTHETIC review', fingerprint=self.batch['fingerprint'])).status_code, 403)
            for operation in operations:
                operation.assert_not_called()
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertIsNone(self.state.thread)

    def test_direct_urls_variants_and_request_flags_cannot_bypass_gate(self):
        for path in ('/investigation?monitor_only=false', '/investigation/', '/investigation%2Frun',
                     '/recovery//refresh', '/recovery/unknown/export', '/export', '/download'):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 403)
                self.assertEqual(self.post(path).status_code, 403)
        self.assertEqual(self.post('/refresh', {'destination': 'investigation_page'}).status_code, 400)
        self.assertEqual(self.post('/refresh', {'monitor_only': 'false'}).status_code, 400)
        for path in ('/', '/devices', '/static/manager.css', '/jobs/unknown/status'):
            self.assertEqual(self.post(path).status_code, 403)

    def test_future_endpoints_are_denied_by_default(self):
        # Use a fresh app because Flask correctly disallows registration after a request.
        app = create_app(self.controller, self.values, monitor_only=True)
        operation = mock.Mock(return_value='must never be reached')
        app.add_url_rule('/future-operation', endpoint='future_operation', view_func=lambda: operation(), methods=['GET', 'POST'])
        client = app.test_client()
        self.assertEqual(client.get('/future-operation', base_url=ORIGIN).status_code, 403)
        self.assertEqual(client.post('/future-operation', base_url=ORIGIN).status_code, 403)
        operation.assert_not_called()

    def test_csrf_host_and_security_headers_remain_enforced(self):
        self.assertEqual(self.client.post('/refresh', base_url=ORIGIN).status_code, 403)
        self.assertEqual(self.post('/refresh', headers={'Origin': 'https://evil.test'}).status_code, 403)
        self.assertEqual(self.get('/', headers={'Host': 'evil.test'}).status_code, 400)
        self.assertEqual(self.get('/', headers={'X-Forwarded-Host': 'evil.test'}).status_code, 400)
        self.assertEqual(self.client.put('/refresh', base_url=ORIGIN).status_code, 405)
        self.assertEqual(self.get('/investigation').headers['Cache-Control'], 'no-store')
        self.assertIn("form-action 'self'", self.get('/').headers['Content-Security-Policy'])

    def test_missing_relative_and_corrupt_monitor_ledger_fail_without_creation_or_repair(self):
        missing = self.root / 'missing.sqlite3'
        corrupt = self.root / 'corrupt.sqlite3'
        corrupt.write_bytes(b'SYNTHETIC invalid ledger')
        for value in ('', 'relative.sqlite3', str(missing), str(corrupt)):
            with self.subTest(path=value):
                self.state.values['monitor_ledger'] = value
                self.complete_refresh()
                self.assertEqual(self.state.dashboard['integrity'], 'Unavailable')
                self.assertEqual(self.get('/').data.count('<strong>—</strong>'.encode()), 7)
        self.assertFalse(missing.exists())
        self.assertEqual(corrupt.read_bytes(), b'SYNTHETIC invalid ledger')

    def test_minimal_profile_and_invalid_device_config_fail_safely(self):
        for values in ({}, {'monitor_ledger': str(self.source), 'device_config': str(self.root / 'missing.py')}):
            app = create_app(self.controller, values, monitor_only=True)
            client = app.test_client()
            response = client.get('/devices', base_url=ORIGIN)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'could not be verified' if values else b'Configured devices unavailable', response.data)
            self.assertEqual(client.get('/recovery', base_url=ORIGIN).status_code, 403)
            self.assertFalse((self.root / 'missing.py').exists())

    def test_cli_flag_is_forwarded_for_profile_and_demo_without_changing_defaults(self):
        for args, expected in ((['--demo', '--monitor-only'], True),
                               (['--profile', 'C:/SYNTHETIC/profile.json', '--monitor-only'], True),
                               (['--demo'], False), (['--profile', 'C:/SYNTHETIC/profile.json'], False)):
            with self.subTest(args=args), mock.patch('manager_app.profile.load_profile', return_value=self.values), \
                    mock.patch('manager_app.demo.create_demo', return_value=(mock.Mock(), self.controller, self.values)), \
                    mock.patch('manager_app.runtime.serve') as serve:
                self.assertEqual(launcher.main(args + ['--no-browser']), 0)
                app = serve.call_args.args[0]
                response = app.test_client().get('/investigation', base_url=ORIGIN)
                self.assertEqual(response.status_code, 403 if expected else 200)
                self.assertFalse(serve.call_args.kwargs['open_browser'])

    def test_monitoring_demo_startup_does_not_prepare_investigation_or_recovery(self):
        from manager_app.demo import create_demo
        with mock.patch('manager_app.demo.investigate') as investigate, \
                mock.patch('manager_app.demo.recovery.prepare') as prepare, \
                mock.patch('manager_app.demo.reporting.write_csv') as export:
            temporary, controller, values = create_demo(monitor_only=True)
            self.addCleanup(temporary.cleanup)
            self.addCleanup(controller.close)
            self.assertEqual(set(values), {'monitor_ledger', 'device_config'})
            self.assertEqual(controller.dashboard(values['monitor_ledger'])['total'], 3)
            for operation in (investigate, prepare, export):
                operation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
