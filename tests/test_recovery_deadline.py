"""Loopback receiver fixture proves the current synchronous transport limitation."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import time
import unittest

import requests

from historical_attendance.client import VerificationError
from historical_attendance.service_recovery import Budget


class DeadlineTests(unittest.TestCase):
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
