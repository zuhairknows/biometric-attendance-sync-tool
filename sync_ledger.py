"""Small SQLite ledger. All correctness state lives here, never in rotating logs."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import time

SCHEMA_VERSION = 1
STATES = ('pending', 'delivering', 'retry', 'synced', 'duplicate',
          'unmatched_employee', 'rejected', 'invalid_record')
COMPLETED = ('synced', 'duplicate')
CAPTURE_FIELDS = ('identity_key', 'device_id', 'user_id', 'device_uid', 'punch_timestamp',
                  'punch_code', 'status_code', 'source_evidence', 'payload', 'state',
                  'error_category', 'error_message')

SCHEMA = [
    'CREATE TABLE schema_version (version INTEGER NOT NULL)',
    """CREATE TABLE punches (
        id INTEGER PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE,
        device_id TEXT NOT NULL, user_id TEXT NOT NULL, device_uid TEXT,
        punch_timestamp TEXT, punch_code TEXT, status_code TEXT,
        source_evidence TEXT NOT NULL, payload TEXT,
        state TEXT NOT NULL CHECK(state IN ('pending','delivering','retry','synced',
          'duplicate','unmatched_employee','rejected','invalid_record')),
        attempt_count INTEGER NOT NULL DEFAULT 0, next_retry REAL NOT NULL DEFAULT 0,
        last_attempt REAL, lease_until REAL, last_http_status INTEGER,
        error_category TEXT, error_message TEXT, checkin_name TEXT,
        first_captured REAL NOT NULL, last_observed REAL NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
    'CREATE INDEX due_punches ON punches(state, next_retry, id)',
    'CREATE INDEX device_watermark ON punches(device_id, state, punch_timestamp)',
    """CREATE TABLE delivery_attempts (id INTEGER PRIMARY KEY, punch_id INTEGER NOT NULL
        REFERENCES punches(id), attempted_at REAL NOT NULL, outcome TEXT NOT NULL,
        http_status INTEGER, error_category TEXT, error_message TEXT)""",
    'CREATE INDEX punch_attempts ON delivery_attempts(punch_id, id)',
    """CREATE TABLE device_state (device_id TEXT PRIMARY KEY, last_successful_fetch TEXT,
        latest_observed_punch TEXT, last_successful_delivery REAL, last_fetch_attempt TEXT,
        fetch_ok INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE destination_state (id INTEGER PRIMARY KEY CHECK(id=1),
        cooldown_until REAL NOT NULL DEFAULT 0, prefer_retry INTEGER NOT NULL DEFAULT 0,
        prefer_recent INTEGER NOT NULL DEFAULT 0)""",
    'INSERT INTO destination_state(id) VALUES(1)',
    'CREATE TABLE shift_state (shift_name TEXT PRIMARY KEY, watermark TEXT NOT NULL)',
    """CREATE TABLE legacy_imports (source_path TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
        imported_at REAL NOT NULL, report TEXT NOT NULL)""",
]


class Ledger:
    def __init__(self, path, readonly=False):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError('SQLite database path must be absolute')
        self.readonly = readonly
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=10) if readonly else sqlite3.connect(str(self.path), timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA busy_timeout=10000')
            self.db.execute('PRAGMA foreign_keys=ON')
            self._initialize()
        except BaseException:
            self.db.close()
            raise

    def _initialize(self):
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables:
            if 'schema_version' not in tables:
                raise RuntimeError('Unrecognized SQLite database; refusing initialization')
            versions = self.db.execute('SELECT version FROM schema_version').fetchall()
            if len(versions) != 1 or versions[0][0] != SCHEMA_VERSION:
                raise RuntimeError('Unsupported SQLite schema version; database left unchanged')
            self._validate_schema()
        elif self.readonly:
            raise RuntimeError('Database has no ledger schema')
        else:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                for sql in SCHEMA:
                    self.db.execute(sql)
                self.db.execute('INSERT INTO schema_version VALUES(?)', (SCHEMA_VERSION,))
        if not self.readonly:
            # FULL + rollback journal keeps backups simple and commits durable. WAL is unnecessary
            # with one delivery worker and avoids copying an incomplete database without its WAL.
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')

    def _validate_schema(self):
        # Version alone is insufficient: a missing table, uniqueness constraint or queue
        # index can otherwise be accepted as healthy. Compare against our schema in memory.
        reference = sqlite3.connect(':memory:')
        try:
            for sql in SCHEMA:
                reference.execute(sql)
            expected = reference.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
            for kind, name, definition in expected:
                actual = self.db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind, name)).fetchone()
                # Preserve string-literal case: CHECK(state IN ('pending')) differs from 'PENDING'.
                canonical = lambda sql: ''.join(sql.split())
                if actual is None or canonical(actual[0]) != canonical(definition):
                    raise RuntimeError('Corrupted or unrecognized ledger schema; database left unchanged')
            rows = self.db.execute('SELECT id,cooldown_until,prefer_retry,prefer_recent FROM destination_state').fetchall()
            if len(rows) != 1 or rows[0]['id'] != 1:
                raise RuntimeError('Corrupted ledger destination state; database left unchanged')
        finally:
            reference.close()

    def verify_integrity(self):
        # Full scans are an explicit operator check, not an expensive per-tick operation.
        if any(row[0] != 'ok' for row in self.db.execute('PRAGMA integrity_check')):
            raise RuntimeError('SQLite integrity check failed')
        if self.db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise RuntimeError('SQLite foreign key check failed')
        return {'integrity': 'ok', 'schema_version': SCHEMA_VERSION}

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def begin_fetch(self, device_id, started_at):
        # A failed scan must block watermark advancement even if an older scan succeeded.
        with self.db:
            self.db.execute('INSERT INTO device_state(device_id,last_fetch_attempt,fetch_ok) VALUES(?,?,0) '
                            'ON CONFLICT(device_id) DO UPDATE SET last_fetch_attempt=excluded.last_fetch_attempt, fetch_ok=0',
                            (device_id, started_at))

    def polling_due(self, device_ids, frequency_minutes, now):
        attempts = []
        for device_id in device_ids:
            row = self.db.execute('SELECT last_fetch_attempt FROM device_state WHERE device_id=?', (device_id,)).fetchone()
            if not row or not row[0]:
                return True
            attempts.append(dt.datetime.fromisoformat(row[0]))
        return bool(attempts) and max(attempts) < now - dt.timedelta(minutes=frequency_minutes)

    def ingest(self, rows, device_id, fetched_at=None, legacy=None):
        now = time.time()
        report = dict(imported=0, already_known=0, invalid=0, excluded=0, failed=0)
        latest = None
        # Rows and successful-fetch metadata commit together. Any disk/SQL error rolls everything back.
        with self.db:
            for row in rows:
                if row is None:
                    report['excluded'] += 1
                    continue
                fields = CAPTURE_FIELDS + ('first_captured', 'last_observed', 'created_at', 'updated_at')
                values = tuple(row[key] for key in CAPTURE_FIELDS) + (now, now, now, now)
                inserted = self.db.execute(
                    'INSERT INTO punches(' + ','.join(fields) + ') VALUES(' + ','.join('?' for _ in fields) + ') '
                    'ON CONFLICT(identity_key) DO NOTHING', values).rowcount
                report['imported' if inserted else 'already_known'] += 1
                report['invalid'] += int(row['state'] == 'invalid_record')
                if not inserted:
                    self.db.execute('UPDATE punches SET last_observed=? WHERE identity_key=?', (now, row['identity_key']))
                if row['punch_timestamp']:
                    latest = max(latest or row['punch_timestamp'], row['punch_timestamp'])
            if fetched_at:
                self.db.execute('INSERT INTO device_state(device_id,last_successful_fetch,latest_observed_punch,fetch_ok) VALUES(?,?,?,1) '
                                'ON CONFLICT(device_id) DO UPDATE SET last_successful_fetch=excluded.last_successful_fetch, '
                                'latest_observed_punch=MAX(COALESCE(device_state.latest_observed_punch,\'\'),COALESCE(excluded.latest_observed_punch,\'\')),fetch_ok=1',
                                (device_id, fetched_at, latest))
            if legacy:
                self.db.execute('INSERT INTO legacy_imports VALUES(?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET '
                                'source_sha256=excluded.source_sha256,imported_at=excluded.imported_at,report=excluded.report',
                                (*legacy, now, json.dumps(report)))
        return report

    def claim(self, now=None, lease_seconds=120):
        now = time.time() if now is None else now
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE punches SET state='retry',next_retry=?,lease_until=NULL,updated_at=?, "
                            "error_category='uncertain_submission',error_message='Expired submission lease requires destination confirmation' "
                            "WHERE state='delivering' AND lease_until<=?", (now, now, now))
            destination = self.db.execute('SELECT * FROM destination_state WHERE id=1').fetchone()
            if destination['cooldown_until'] > now:
                return None
            # Alternate new and retry work so a large outage backlog cannot starve new attendance.
            states = ('retry', 'pending') if destination['prefer_retry'] else ('pending', 'retry')
            row = None
            for state in states:
                # An outage leaves many untouched rows pending, not just retrying. Alternate
                # oldest/newest pending work too, so fresh captures can progress in that backlog.
                order = 'next_retry DESC,id DESC' if state == 'pending' and destination['prefer_recent'] else 'next_retry,id'
                row = self.db.execute('SELECT * FROM punches WHERE state=? AND next_retry<=? ORDER BY ' + order + ' LIMIT 1', (state, now)).fetchone()
                if row:
                    break
            if row is None:
                return None
            self.db.execute("UPDATE punches SET state='delivering', attempt_count=attempt_count+1,last_attempt=?,lease_until=?,updated_at=? WHERE id=?",
                            (now, now + lease_seconds, now, row['id']))
            self.db.execute('UPDATE destination_state SET prefer_retry=? WHERE id=1', (int(row['state'] != 'retry'),))
            if row['state'] == 'pending':
                self.db.execute('UPDATE destination_state SET prefer_recent=1-prefer_recent WHERE id=1')
            return dict(self.db.execute('SELECT * FROM punches WHERE id=?', (row['id'],)).fetchone())

    def finish(self, row, outcome, now=None, retry_at=0, cooldown_until=0):
        now = time.time() if now is None else now
        state, http_status, category, message, name = outcome
        if state not in STATES or state in ('pending', 'delivering', 'invalid_record'):
            raise ValueError('Invalid delivery transition')
        with self.db:
            changed = self.db.execute('UPDATE punches SET state=?,next_retry=?,lease_until=NULL,last_http_status=?, '
                                     'error_category=?,error_message=?,checkin_name=?,updated_at=? '
                                     "WHERE id=? AND state='delivering' AND attempt_count=?",
                                     (state, retry_at, http_status, category, message, name, now, row['id'], row['attempt_count'])).rowcount
            if changed != 1:
                raise RuntimeError('Delivery lease no longer belongs to this attempt')
            self.db.execute('INSERT INTO delivery_attempts(punch_id,attempted_at,outcome,http_status,error_category,error_message) VALUES(?,?,?,?,?,?)',
                            (row['id'], now, state, http_status, category, message))
            self.db.execute('DELETE FROM delivery_attempts WHERE punch_id=? AND id NOT IN '
                            '(SELECT id FROM delivery_attempts WHERE punch_id=? ORDER BY id DESC LIMIT 20)', (row['id'], row['id']))
            if cooldown_until:
                self.db.execute('UPDATE destination_state SET cooldown_until=MAX(cooldown_until,?) WHERE id=1', (cooldown_until,))
            if state in COMPLETED:
                self.db.execute('INSERT INTO device_state(device_id,last_successful_delivery) VALUES(?,?) '
                                'ON CONFLICT(device_id) DO UPDATE SET last_successful_delivery=excluded.last_successful_delivery', (row['device_id'], now))

    def safe_watermark(self, device_ids, *, cutover=None):
        # Only the caller's validated activation boundary can scope live delivery.
        # Omitting it retains the original conservative calculation for diagnostics.
        if cutover is not None and (not isinstance(cutover, dt.datetime)
                                   or cutover.tzinfo is not None or cutover.microsecond):
            raise ValueError('Watermark cutover must be a naive whole-second datetime')
        if not device_ids:
            return None
        candidates = []
        for device_id in device_ids:
            device = self.db.execute('SELECT * FROM device_state WHERE device_id=?', (device_id,)).fetchone()
            if not device or not device['fetch_ok'] or not device['last_successful_fetch']:
                return None
            # Permanent errors still block. Parse timestamps before excluding anything:
            # a malformed/unknown time or a wrong hold label must never advance a shift.
            earliest = None
            rows = self.db.execute("SELECT DISTINCT punch_timestamp,state,error_category FROM punches "
                                   "WHERE device_id=? AND state NOT IN ('synced','duplicate')", (device_id,))
            for row in rows:
                try:
                    stamp = dt.datetime.fromisoformat(row['punch_timestamp'])
                except (TypeError, ValueError):
                    return None
                if stamp.tzinfo is not None or str(stamp) != row['punch_timestamp']:
                    return None
                if (cutover is not None and row['state'] == 'rejected'
                        and row['error_category'] == 'cutover_hold' and stamp < cutover):
                    continue  # Watermark scope only; evidence/state/payload remain untouched.
                earliest = min(earliest, stamp) if earliest is not None else stamp
            candidate = dt.datetime.fromisoformat(device['last_successful_fetch'])
            if earliest is not None:
                if earliest < dt.datetime.min + dt.timedelta(seconds=1):
                    return None
                candidate = min(candidate, earliest - dt.timedelta(seconds=1))
            candidates.append(candidate)
        # Frappe stores second-resolution watermarks; always round down.
        return min(candidates).replace(microsecond=0)

    def shift_watermark(self, shift):
        row = self.db.execute('SELECT watermark FROM shift_state WHERE shift_name=?', (shift,)).fetchone()
        return dt.datetime.fromisoformat(row[0]) if row else None

    def save_shift_watermark(self, shift, value):
        with self.db:
            self.db.execute('INSERT INTO shift_state VALUES(?,?) ON CONFLICT(shift_name) DO UPDATE SET watermark=excluded.watermark', (shift, str(value)))

    def destination_cooldown(self):
        return self.db.execute('SELECT cooldown_until FROM destination_state WHERE id=1').fetchone()[0]

    def has_uncertain_peer(self, row):
        # Different SDK UIDs/devices can describe the same destination identity. Conservatively
        # hold the same biometric ID/second across all directions while a peer is uncertain.
        return self.db.execute("SELECT 1 FROM punches WHERE id!=? AND user_id=? "
            "AND substr(punch_timestamp,1,19)=? AND (state='delivering' OR "
            "(state='retry' AND COALESCE(error_category,'') NOT IN ('authentication','rate_limited','peer_uncertain'))) LIMIT 1",
            (row['id'], row['user_id'], row['punch_timestamp'][:19])).fetchone() is not None

    def pause_destination(self, until):
        with self.db:
            self.db.execute('UPDATE destination_state SET cooldown_until=MAX(cooldown_until,?) WHERE id=1', (until,))

    def diagnostics(self):
        counts = dict.fromkeys(STATES, 0)
        counts.update({r[0]: r[1] for r in self.db.execute('SELECT state,COUNT(*) FROM punches GROUP BY state')})
        pending = self.db.execute("SELECT MIN(punch_timestamp) FROM punches WHERE state IN ('pending','retry','delivering')").fetchone()[0]
        devices = {r[0]: r[1] for r in self.db.execute("SELECT device_id,COUNT(*) FROM punches WHERE state IN ('pending','retry','delivering') GROUP BY device_id")}
        oldest_capture = self.db.execute("SELECT MIN(first_captured) FROM punches WHERE state IN ('pending','retry','delivering')").fetchone()[0]
        quarantine = self.db.execute("SELECT COUNT(*) FROM punches WHERE state IN ('unmatched_employee','rejected','invalid_record')").fetchone()[0]
        held = self.db.execute("SELECT COUNT(*) FROM punches WHERE error_category='cutover_hold'").fetchone()[0]
        uncertain = self.db.execute("SELECT COUNT(*) FROM punches WHERE state='retry' AND "
                                    "COALESCE(error_category,'') NOT IN ('authentication','rate_limited','peer_uncertain')").fetchone()[0]
        related = self.db.execute("SELECT COUNT(*) FROM punches WHERE state='retry' AND error_category='peer_uncertain'").fetchone()[0]
        failed = self.db.execute('SELECT COUNT(*) FROM device_state WHERE fetch_ok=0').fetchone()[0]
        return dict(schema_version=SCHEMA_VERSION, total=sum(counts.values()), states=counts,
                    backlog_age_seconds=max(0, time.time() - oldest_capture) if oldest_capture is not None else 0,
                    failed_scans=failed, quarantined=quarantine, held_historical=held, uncertain_submissions=uncertain,
                    held_related_records=related,
                    oldest_pending_timestamp=pending, pending_per_device=devices,
                    last_successful_delivery=self.db.execute('SELECT MAX(last_successful_delivery) FROM device_state').fetchone()[0],
                    destination_cooldown_until=self.db.execute('SELECT cooldown_until FROM destination_state WHERE id=1').fetchone()[0])
