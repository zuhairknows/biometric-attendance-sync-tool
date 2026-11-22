"""Service-owned HTTPS with an absolute I/O deadline and no background sender.

The release pins a numeric address to avoid unbounded DNS in the polling lane.
TLS still verifies the site's hostname. No proxy, redirect, retry or connection
pool can continue a submission after this call has returned.
"""
import errno
import http.client
import io
import ipaddress
import json
import select
import socket
import ssl
import time
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit


class DeadlineIO(io.RawIOBase):
    def __init__(self, stream, deadline, stopped):
        self.stream, self.deadline, self.stopped = stream, deadline, stopped

    def readable(self):
        return True

    def check(self):
        if self.stopped() or time.monotonic() >= self.deadline:
            raise TimeoutError('recovery_transport_deadline')

    def wait(self, writing=False):
        self.check()
        ready = select.select([] if writing else [self.stream], [self.stream] if writing else [],
                              [], min(.1, max(0, self.deadline - time.monotonic())))
        self.check()
        return bool(ready[1] if writing else ready[0])

    def call(self, operation):
        while True:
            self.check()
            try:
                return operation()
            except ssl.SSLWantWriteError:
                self.wait(True)
            except (ssl.SSLWantReadError, BlockingIOError):
                self.wait()

    def readinto(self, buffer):
        return self.call(lambda: self.stream.recv_into(buffer))


class HTTPS:
    MAX_BODY = 2 * 1024 * 1024

    def __init__(self, url, address, *, stopped=lambda: False, context=None):
        origin = urlsplit(url)
        if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                or origin.path not in ('', '/') or origin.query or origin.fragment):
            raise ValueError('recovery_origin_invalid')
        self.origin, self.url = origin, url.rstrip('/')
        self.address = str(ipaddress.ip_address(address))
        self.stopped = stopped
        self.context = context or ssl.create_default_context()
        if not self.context.check_hostname or self.context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError('recovery_tls_verification_required')

    def request(self, method, url, **options):
        if (method not in ('GET', 'POST') or not url.startswith(self.url + '/api/')
                or options.get('allow_redirects') is not False):
            raise ValueError('recovery_request_invalid')
        # Budget supplies the remaining absolute allowance, including pacing.
        allowance = options.pop('_remaining', 10)
        if not 0 < allowance <= 120:
            raise ValueError('recovery_deadline_invalid')
        if self.stopped():
            raise TimeoutError('recovery_transport_cancelled')
        deadline = time.monotonic() + min(10, allowance)
        headers = dict(options.get('headers', {}))
        if any('\r' in str(v) or '\n' in str(v) for pair in headers.items() for v in pair):
            raise ValueError('recovery_headers_invalid')
        body = json.dumps(options['json']).encode() if 'json' in options else b''
        if len(body) > self.MAX_BODY:
            raise ValueError('recovery_body_limit')
        path = url[len(self.url):]
        if options.get('params'):
            path += '?' + urlencode(options['params'])
        headers.update(Host=self.origin.netloc, Connection='close', **{'Content-Length': str(len(body))})
        if body:
            headers['Content-Type'] = 'application/json'
        wire = (method + ' ' + path + ' HTTP/1.1\r\n' + ''.join(
            str(k) + ': ' + str(v) + '\r\n' for k, v in headers.items()) + '\r\n').encode('ascii') + body
        family = socket.AF_INET6 if ':' in self.address else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        stream = raw
        try:
            raw.setblocking(False)
            code = raw.connect_ex((self.address, self.origin.port or 443))
            if code not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035):
                raise OSError(code, 'recovery_connect_failed')
            io_deadline = DeadlineIO(raw, min(deadline, time.monotonic() + 3), self.stopped)
            while not io_deadline.wait(True):
                pass
            if raw.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
                raise OSError('recovery_connect_failed')
            stream = self.context.wrap_socket(raw, server_hostname=self.origin.hostname, do_handshake_on_connect=False)
            io_deadline = DeadlineIO(stream, deadline, self.stopped)
            io_deadline.call(stream.do_handshake)
            view = memoryview(wire)
            while view:
                sent = io_deadline.call(lambda: stream.send(view))
                if not sent:
                    raise OSError('recovery_connection_closed')
                view = view[sent:]
            # HTTPResponse parses framing; all underlying reads use the same clock.
            reader = io.BufferedReader(io_deadline)
            adapter = SimpleNamespace(makefile=lambda *args, **kwargs: reader)
            response = http.client.HTTPResponse(adapter)
            try:
                response.begin()
                content = response.read(self.MAX_BODY + 1)
                io_deadline.check()
                if len(content) > self.MAX_BODY or response.length not in (None, 0):
                    raise ValueError('recovery_response_limit')
                return SimpleNamespace(status_code=response.status, headers=dict(response.getheaders()),
                                       content=content, json=lambda: json.loads(content))
            finally:
                response.close()
                reader.close()
        finally:
            stream.close()
            raw.close()
