"""Loopback receiver fixture proves the current synchronous transport limitation."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import time
import unittest
from unittest import mock

import requests

from historical_attendance.client import VerificationError
from historical_attendance.service_recovery import Budget


class DeadlineTests(unittest.TestCase):
    def budget(self, transport=None, stopped=lambda: False):
        now = [1000.]
        def sleep(seconds):
            now[0] += seconds
        transport = transport or mock.Mock()
        budget = Budget(transport, dict(rate=2), stopped=stopped,
            clock=lambda: now[0], monotonic=lambda: now[0], sleep=sleep)
        budget.account = mock.Mock()
        return budget, transport, now

    def test_explicit_phase_timeouts_and_redirect_policy_cannot_be_widened(self):
        budget, transport, now = self.budget()
        budget.request('GET', 'https://synthetic.example.test/', timeout=999, allow_redirects=True)
        self.assertEqual(transport.call_args.kwargs['timeout'], (3, 10))
        self.assertFalse(transport.call_args.kwargs['allow_redirects'])
        # Pacing consumes some of the final interval; both phases use what remains.
        budget.end = budget.deadline = now[0] + .75
        budget.request('GET', 'https://synthetic.example.test/')
        self.assertEqual(transport.call_args.kwargs['timeout'], (.25, .25))

    def test_finite_request_count_counts_failures_without_automatic_retry(self):
        transport = mock.Mock(side_effect=requests.Timeout('SYNTHETIC'))
        budget, _, _ = self.budget(transport)
        with self.assertRaises(requests.Timeout):
            budget.request('POST', 'https://synthetic.example.test/')
        transport.assert_called_once()
        budget.account.assert_called_once()
        self.assertEqual(budget.used, 1)

    def test_request_cap_prevents_the_next_transport_call(self):
        budget, transport, _ = self.budget()
        for _ in range(128):
            budget.request('GET', 'https://synthetic.example.test/')
        with self.assertRaises(VerificationError):
            budget.request('GET', 'https://synthetic.example.test/')
        self.assertEqual(transport.call_count, 128)
        self.assertEqual(budget.account.call_count, 128)

    def test_cancellation_during_request_rejects_response_and_further_calls(self):
        stopped = [False]
        def transport(*args, **kwargs):
            stopped[0] = True
            return object()
        budget, _, _ = self.budget(transport, stopped=lambda: stopped[0])
        with self.assertRaises(VerificationError):
            budget.request('GET', 'https://synthetic.example.test/')
        with self.assertRaises(VerificationError):
            budget.request('GET', 'https://synthetic.example.test/')
        self.assertEqual(budget.used, 1)

    def test_trickling_response_exceeds_deadline_but_cannot_become_success(self):
        received = []
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append('GET')
                body = b'{"message":"synthetic-only"}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                # Each byte arrives within the socket inactivity timeout. The
                # complete body deliberately takes longer than the total budget.
                for value in body:
                    self.wfile.write(bytes([value]))
                    self.wfile.flush()
                    time.sleep(.025)

        # This thread is only a test receiver, never a sending worker or service.
        server = HTTPServer(('127.0.0.1', 0), Receiver)
        receiver = threading.Thread(target=server.serve_forever, daemon=True)
        receiver.start()
        try:
            with requests.Session() as session:
                session.trust_env = False
                budget = Budget(session.request, dict(rate=2), stopped=lambda: False)
                budget.end = time.monotonic() + .25
                started = time.monotonic()
                url = f'http://127.0.0.1:{server.server_port}/synthetic'
                with self.assertRaises(VerificationError):
                    budget.request('GET', url, allow_redirects=False)
                self.assertGreater(time.monotonic() - started, .25)
                with self.assertRaises(VerificationError):
                    budget.request('GET', url, allow_redirects=False)
            self.assertEqual(received, ['GET'])
        finally:
            server.shutdown()
            server.server_close()
            receiver.join(5)
            self.assertFalse(receiver.is_alive())
