"""Synthetic production-mode gates and real subprocess deadline enforcement."""
import contextlib
import io
import json
import multiprocessing
from pathlib import Path
import time
import unittest
from unittest import mock

from historical_attendance.bounded import run_bounded
from historical_attendance.client import ReadOnlyFrappeClient, VerificationError
from scripts import investigate_attendance as cli
import test_historical_investigation as fixtures


def allow_test_acl(directory):
    """Test-only ACL seam; production always uses the actual Windows guard."""


def inline_runner(task, options, **limits):
    return task(**options)


def simple_task():
    return [], {'synthetic': True}


def broken_task():
    raise RuntimeError('PRIVATE EMPLOYEE TEST_SECRET')


class NeverRequest:
    def __call__(self, *args, **kwargs):
        raise AssertionError('No HTTP request expected in empty-range test')


def stalled_http_task(marker):
    def request(method, url, **kwargs):
        Path(marker).write_text(method, encoding='utf-8')
        time.sleep(60)  # Simulate a body that never completes despite inactivity timeouts.
        Path(marker).write_text('UNSAFE_CONTINUATION', encoding='utf-8')
    client = ReadOnlyFrappeClient('https://synthetic.example.test', 'TEST', 'SECRET', request)
    return client.scan('Employee', [])


class ProductionVerificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InvestigationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def production_args(self):
        self.fixture.policy.update(site_url='https://erp.myfpf.com', destination_environment='production')
        # Deliberately do NOT attest quiescence or infer other deployment claims.
        self.fixture.policy['verification'] = {'read_only_api_verified': True}
        return self.fixture.cli_args() + ['--production-read-only', '--acknowledge-production', 'https://erp.myfpf.com']

    def invoke(self, args, runner=inline_runner, request=None):
        out = io.StringIO()
        before = self.fixture.path.read_bytes()
        with mock.patch.dict('os.environ', INVESTIGATION_API_KEY='TEST_KEY', INVESTIGATION_API_SECRET='TEST_SECRET'):
            with contextlib.redirect_stdout(out):
                code = cli.main(args, request=request or self.fixture.request, acl_check=allow_test_acl, runner=runner)
        self.assertEqual(self.fixture.path.read_bytes(), before)
        self.assertNotIn('TEST_SECRET', out.getvalue())
        return code, out.getvalue()

    def summary(self):
        return json.loads(next((self.root / 'output').glob('investigation_summary_*.json')).read_text())

    def test_production_requires_matching_acknowledgement_and_mode(self):
        args = self.production_args()
        for invalid in (args[:-3], args[:-1] + ['https://wrong.example.test']):
            code, _ = self.invoke(invalid)
            self.assertEqual(code, 1)
            self.assertFalse(self.fixture.calls)

    def test_known_production_origin_cannot_omit_environment_label(self):
        args = self.production_args()
        self.fixture.policy.pop('destination_environment')
        args = self.fixture.cli_args()
        self.assertEqual(self.invoke(args)[0], 1)
        self.assertFalse(self.fixture.calls)

    def test_read_only_endpoint_evidence_is_mandatory(self):
        self.production_args()
        self.fixture.policy['verification']['read_only_api_verified'] = False
        args = self.fixture.cli_args() + ['--production-read-only', '--acknowledge-production', 'https://erp.myfpf.com']
        self.assertEqual(self.invoke(args)[0], 1)
        self.assertFalse(self.fixture.calls)

    def test_production_reads_without_falsely_attesting_quiescence(self):
        code, out = self.invoke(self.production_args())
        self.assertEqual(code, 2)  # Never a full reconciliation pass.
        self.assertIn('reads_complete', out)
        summary = self.summary()
        self.assertEqual(summary['status'], 'unverified')
        self.assertIn('destination_quiescent_during_scan', summary['unverified_claims'])
        self.assertEqual(summary['totals']['potentially_missing'], 0)
        self.assertEqual(summary['totals']['unable_to_verify'], 1)
        self.assertFalse(summary['recovery_authorized'])
        self.assertEqual(summary['execution_limits'], dict(max_get_requests=32, deadline_seconds=120, request_timeout_seconds=10))
        self.assertTrue(self.fixture.calls)
        self.assertTrue(all(c[0] == 'GET' and c[2]['timeout'] == 10 for c in self.fixture.calls))

    def test_production_never_reconciles_even_if_all_claims_true(self):
        from historical_attendance.engine import CLAIMS
        self.production_args()
        self.fixture.policy['verification'].update({claim: True for claim in CLAIMS})
        args = self.fixture.cli_args() + ['--production-read-only', '--acknowledge-production', 'https://erp.myfpf.com']
        self.assertEqual(self.invoke(args)[0], 2)
        self.assertEqual(self.summary()['totals']['potentially_missing'], 0)

    def test_production_hard_request_limit_stops_without_automatic_retry(self):
        args = self.production_args() + ['--max-get-requests', '1']
        self.assertEqual(self.invoke(args)[0], 2)
        self.assertEqual(len(self.fixture.calls), 1)
        summary = self.summary()
        self.assertEqual(summary['api_requests_made'], 1)
        self.assertEqual(summary['compatibility_status'], 'not_verified')
        self.assertIn('investigation_request_limit_exceeded', summary['blockers'])

    def test_invalid_limits_rejected_before_requests(self):
        args = self.production_args()
        for flag, value in (('--max-get-requests', '0'), ('--deadline-seconds', '0'),
                            ('--deadline-seconds', '601'), ('--request-timeout-seconds', '0')):
            self.assertEqual(self.invoke(args + [flag, value])[0], 1)
        self.assertFalse(self.fixture.calls)

    def test_deadline_failure_does_not_publish_report(self):
        runner = mock.Mock(side_effect=VerificationError('investigation_deadline_exceeded'))
        code, out = self.invoke(self.production_args(), runner=runner)
        self.assertEqual(code, 1)
        self.assertIn('investigation_deadline_exceeded', out)
        self.assertEqual(list((self.root / 'output').iterdir()), [])

    def test_real_worker_success_and_sanitized_failure(self):
        self.assertEqual(run_bounded(simple_task, {}, seconds=5, directory=self.root), ([], {'synthetic': True}))
        with self.assertRaisesRegex(VerificationError, '^investigation_worker_failed$'):
            run_bounded(broken_task, {}, seconds=5, directory=self.root)
        self.assertFalse(list(self.root.glob('.investigation-worker-*.part')))

    def test_real_deadline_terminates_stalled_get_worker(self):
        marker = self.root / 'synthetic-get.txt'
        began = time.monotonic()
        with self.assertRaisesRegex(VerificationError, '^investigation_deadline_exceeded$'):
            run_bounded(stalled_http_task, dict(marker=str(marker)), seconds=2, directory=self.root)
        self.assertLess(time.monotonic() - began, 5)  # Deadline plus termination/cleanup allowance.
        self.assertEqual(marker.read_text(), 'GET')
        self.assertFalse(multiprocessing.active_children())
        self.assertFalse(list(self.root.glob('.investigation-worker-*.part')))

    def test_real_cli_worker_and_report_path_without_network(self):
        args = self.production_args()
        args[args.index('--start') + 1] = '2026-10-17 23:59:59'
        args[args.index('--end') + 1] = '2026-10-17 23:59:59'
        code, out = self.invoke(args, runner=run_bounded, request=NeverRequest())
        self.assertEqual(code, 2)
        self.assertIn('not_verified', out)
        self.assertEqual(self.summary()['api_requests_made'], 0)


if __name__ == '__main__':
    unittest.main()
