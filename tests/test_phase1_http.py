import unittest
from scripts.phase1_http_harness import run_scenarios, loopback_request


class LoopbackIntegrationTests(unittest.TestCase):
    def test_fixture_to_sqlite_to_real_loopback_http_recovery(self):
        report = run_scenarios()
        self.assertEqual(report['result'], 'passed')
        self.assertEqual(report['scenario_count'], 10)
        self.assertEqual(report['live_hardware'], 'NOT EXECUTED')

    def test_harness_refuses_external_network_urls(self):
        with self.assertRaisesRegex(ValueError, '127.0.0.1'):
            loopback_request('POST', 'https://erp.example.test', {}, {})
