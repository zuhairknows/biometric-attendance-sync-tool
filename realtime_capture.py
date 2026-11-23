"""Realtime punch capture for station terminals (ZKTeco ADMS push).

The five-minute attendance poll in ``erpnext_sync.main()`` is right for
attendance and far too slow for a station terminal, where an operator stands at
a machine waiting for a label to print. This module gives those few devices a
sub-second path without touching the poll that already works.

Why an ADMS listener and not pyzk's ``live_capture()``
------------------------------------------------------
``live_capture()`` holds an SDK connection open on port 4370 for the whole
shift, and the attendance poll needs that same port every five minutes. ZK
terminals are unreliable about concurrent SDK connections, so a held connection
puts a working attendance sync at risk to gain a feature. ADMS is the opposite
shape: the device opens an HTTP connection when somebody punches and closes it
again, so nothing is held and the poll is untouched. It also survives NAT,
because the device dials out.

The cost is a device-side change - Comm -> Cloud Server Setting on each station
terminal - and that is deliberate: it is reversible from the device's own menu,
and it leaves every other device in the fleet alone.

Layering
--------
``parse_attlog`` and ``AdmsRouter`` know nothing about HTTP or ERPNext, so they
are tested directly. ``AdmsListener`` is the thin socket wrapper around them.
Nothing here imports ``erpnext_sync``; the caller supplies ``on_punch``.
"""

import datetime
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_LISTEN_PORT = 8090

# A station punch further than this from the sync PC's clock is not acted on;
# see AdmsRouter.is_current.
DEFAULT_MAX_AGE_SECONDS = 300

# Told to the device on handshake. Realtime=1 is the whole point: it makes the
# terminal POST each punch as it happens instead of batching on a timer.
HANDSHAKE_OPTIONS = (
    "Stamp=9999\n"
    "OpStamp=9999\n"
    "ErrorDelay=30\n"
    "Delay=10\n"
    "TransTimes=00:00;14:05\n"
    "TransInterval=1\n"
    "TransFlag=1111000000\n"
    "Realtime=1\n"
    "Encrypt=0\n"
)

ATTLOG_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)


@dataclass
class PunchEvent:
    """One verification at a terminal, as the device reported it."""

    device_id: str
    serial_number: str
    user_id: str
    timestamp: datetime.datetime
    status: str = ""
    verify_mode: str = ""
    workcode: str = ""
    raw: str = ""


@dataclass
class RouterStats:
    handshakes: int = 0
    punches: int = 0
    rejected_serials: list = field(default_factory=list)
    malformed_lines: int = 0
    stale_punches: int = 0


def parse_attlog_timestamp(value):
    """Parse a device timestamp, or return None rather than raising.

    A single unreadable row must not cost the rest of the batch, so callers
    skip a None instead of failing the request.
    """
    text = (value or "").strip()
    if not text:
        return None
    for pattern in ATTLOG_TIMESTAMP_FORMATS:
        try:
            return datetime.datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def parse_attlog(body, device_id, serial_number):
    """Parse an ATTLOG payload into PunchEvents, skipping rows it cannot read.

    The layout is tab separated and positional::

        user_id \t timestamp \t status \t verify_mode \t workcode \t ...

    Firmware adds columns on the right over time (mask flag, temperature), so
    anything past ``workcode`` is ignored rather than rejected. Returns
    ``(events, malformed_count)``.
    """
    events = []
    malformed = 0
    for line in (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            malformed += 1
            continue
        user_id = parts[0].strip()
        timestamp = parse_attlog_timestamp(parts[1])
        if not user_id or timestamp is None:
            malformed += 1
            continue
        events.append(
            PunchEvent(
                device_id=device_id,
                serial_number=serial_number,
                user_id=user_id,
                timestamp=timestamp,
                status=parts[2].strip() if len(parts) > 2 else "",
                verify_mode=parts[3].strip() if len(parts) > 3 else "",
                workcode=parts[4].strip() if len(parts) > 4 else "",
                raw=line,
            )
        )
    return events, malformed


def build_device_index(devices):
    """Map serial number and IP to device_id for devices flagged realtime.

    Serial is what the device sends and is preferred; IP is the fallback for a
    terminal whose serial has not been recorded in the config yet.
    """
    by_serial = {}
    by_ip = {}
    for device in devices or []:
        if not device.get("realtime"):
            continue
        device_id = str(device.get("device_id") or "")
        if not device_id:
            continue
        serial = str(device.get("serial_number") or "").strip()
        if serial:
            by_serial[serial.upper()] = device_id
        ip = str(device.get("ip") or device.get("host") or "").strip()
        if ip:
            by_ip[ip] = device_id
    return by_serial, by_ip


class AdmsRouter:
    """The ADMS conversation, with no sockets in it.

    ``on_punch`` is called once per punch. An exception from it is contained:
    one bad punch must not make the device retry the whole batch, because the
    terminal would then replay punches we already handled.
    """

    def __init__(self, devices, on_punch, logger=None, max_age_seconds=None, clock=None):
        self.by_serial, self.by_ip = build_device_index(devices)
        self.on_punch = on_punch
        self.logger = logger
        self.stats = RouterStats()
        # A terminal switched to ADMS may upload the punches it already holds,
        # and the poll has recorded those as attendance long ago. Firing the
        # station action for them would print a label per old punch, so a
        # punch further than this from the sync PC's clock (either way: a
        # terminal whose clock is wrong is not trusted either) is acknowledged
        # and skipped. None turns the check off.
        self.max_age_seconds = max_age_seconds
        self.clock = clock or datetime.datetime.now

    def is_current(self, event):
        if self.max_age_seconds is None:
            return True
        drift = abs((self.clock() - event.timestamp).total_seconds())
        return drift <= self.max_age_seconds

    def resolve_device_id(self, serial_number, client_ip):
        if serial_number:
            device_id = self.by_serial.get(str(serial_number).upper())
            if device_id:
                return device_id
        if client_ip:
            return self.by_ip.get(client_ip)
        return None

    def handle(self, method, path, query, body, client_ip=""):
        """Return ``(status_code, text)`` for one device request."""
        serial_number = (query.get("SN", [""])[0] or "").strip()
        device_id = self.resolve_device_id(serial_number, client_ip)
        if not device_id:
            # An unknown terminal is refused rather than trusted: this listener
            # creates ERP records, so it accepts only configured devices.
            self.stats.rejected_serials.append(serial_number or client_ip)
            self._log("warning", "Rejected ADMS request from unknown device", serial_number, client_ip)
            return 401, "Unauthorized"

        if path.endswith("/cdata") and method == "GET":
            self.stats.handshakes += 1
            self._log("info", "ADMS handshake", device_id, serial_number)
            return 200, "GET OPTION FROM: " + (serial_number or device_id) + "\n" + HANDSHAKE_OPTIONS

        if path.endswith("/cdata") and method == "POST":
            table = (query.get("table", [""])[0] or "").upper()
            if table and table != "ATTLOG":
                # OPERLOG, USERINFO and friends: acknowledged so the device
                # stops resending, but not acted on.
                return 200, "OK"
            events, malformed = parse_attlog(body, device_id, serial_number)
            self.stats.malformed_lines += malformed
            if malformed:
                self._log("warning", "Skipped malformed ATTLOG rows", device_id, str(malformed))
            current = [event for event in events if self.is_current(event)]
            stale = len(events) - len(current)
            if stale:
                # Acknowledged below like the rest, so the terminal marks them
                # sent and does not offer them again.
                self.stats.stale_punches += stale
                self._log("warning", "Skipped ATTLOG rows outside the station age limit", device_id, str(stale))
            for event in current:
                self.stats.punches += 1
                try:
                    self.on_punch(event)
                except Exception:
                    self._log("exception", "on_punch failed", device_id, event.user_id)
            return 200, "OK: " + str(len(events))

        if path.endswith("/getrequest"):
            # No command queue yet. The device polls this; "OK" means nothing
            # to do. Commands to the terminal would be returned here.
            return 200, "OK"

        if path.endswith("/devicecmd"):
            return 200, "OK"

        return 404, "Not Found"

    def _log(self, level, *parts):
        if not self.logger:
            return
        message = "\t".join(str(part) for part in parts if part != "")
        try:
            getattr(self.logger, level)(message)
        except Exception:
            pass


def _make_handler(router):
    class AdmsRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "BiometricAttendanceSync"
        sys_version = ""

        def _dispatch(self, method):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            client_ip = self.client_address[0] if self.client_address else ""
            try:
                status_code, text = router.handle(method, parsed.path, query, body, client_ip)
            except Exception:
                router._log("exception", "ADMS request failed", self.path)
                status_code, text = 500, "Error"
            payload = text.encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def log_message(self, format, *args):
            # BaseHTTPRequestHandler writes to stderr by default, which in a
            # Windows service means nowhere useful.
            return

    return AdmsRequestHandler


class AdmsListener:
    """Owns the listening socket and its thread.

    Deliberately separate from ``erpnext_sync.main()``: if this dies, the
    attendance poll carries on untouched.
    """

    def __init__(self, devices, on_punch, host="0.0.0.0", port=DEFAULT_LISTEN_PORT, logger=None,
                 max_age_seconds=None):
        self.router = AdmsRouter(devices, on_punch, logger=logger, max_age_seconds=max_age_seconds)
        self.host = host
        self.port = port
        self.logger = logger
        self._server = None
        self._thread = None

    @property
    def stats(self):
        return self.router.stats

    def has_realtime_devices(self):
        return bool(self.router.by_serial or self.router.by_ip)

    def start(self):
        if self._server is not None:
            return self
        self._server = ThreadingHTTPServer((self.host, self.port), _make_handler(self.router))
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="adms-listener",
            daemon=True,
        )
        self._thread.start()
        self.router._log("info", "ADMS listener started", self.host + ":" + str(self.actual_port))
        return self

    @property
    def actual_port(self):
        if self._server is None:
            return self.port
        return self._server.server_address[1]

    def stop(self):
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        finally:
            self._server = None
            self._thread = None
            self.router._log("info", "ADMS listener stopped")


def start_if_configured(devices, on_punch, host="0.0.0.0", port=DEFAULT_LISTEN_PORT, logger=None,
                        max_age_seconds=DEFAULT_MAX_AGE_SECONDS):
    """Start a listener only when some device is flagged realtime.

    Returns None otherwise, so an unconfigured install opens no port at all.
    """
    listener = AdmsListener(devices, on_punch, host=host, port=port, logger=logger,
                            max_age_seconds=max_age_seconds)
    if not listener.has_realtime_devices():
        return None
    return listener.start()
