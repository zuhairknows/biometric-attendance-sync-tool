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
        cooldown_until REAL NOT NULL DEFAULT 0, prefer_retry INTEGER NOT NULL DEFAULT 0)""",
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
            self.db.execute("UPDATE punches SET state='retry',next_retry=?,lease_until=NULL,updated_at=? "
                            "WHERE state='delivering' AND lease_until<=?", (now, now, now))
            destination = self.db.execute('SELECT * FROM destination_state WHERE id=1').fetchone()
            if destination['cooldown_until'] > now:
                return None
            # Alternate new and retry work so a large outage backlog cannot starve new attendance.
            states = ('retry', 'pending') if destination['prefer_retry'] else ('pending', 'retry')
            row = None
            for state in states:
                row = self.db.execute('SELECT * FROM punches WHERE state=? AND next_retry<=? ORDER BY next_retry,id LIMIT 1', (state, now)).fetchone()
                if row:
                    break
            if row is None:
                return None
            self.db.execute("UPDATE punches SET state='delivering', attempt_count=attempt_count+1,last_attempt=?,lease_until=?,updated_at=? WHERE id=?",
                            (now, now + lease_seconds, now, row['id']))
            self.db.execute('UPDATE destination_state SET prefer_retry=? WHERE id=1', (int(row['state'] != 'retry'),))
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

    def safe_watermark(self, device_ids):
        if not device_ids:
            return None
        candidates = []
        for device_id in device_ids:
            device = self.db.execute('SELECT * FROM device_state WHERE device_id=?', (device_id,)).fetchone()
            if not device or not device['fetch_ok'] or not device['last_successful_fetch']:
                return None
            # Permanent errors also block. An unknown timestamp cannot be ordered safely.
            unresolved = self.db.execute("SELECT MIN(punch_timestamp),SUM(punch_timestamp IS NULL) FROM punches "
                                         "WHERE device_id=? AND state NOT IN ('synced','duplicate')", (device_id,)).fetchone()
            if unresolved[1]:
                return None
            candidate = dt.datetime.fromisoformat(device['last_successful_fetch'])
            if unresolved[0]:
                earliest = dt.datetime.fromisoformat(unresolved[0])
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

    def pause_destination(self, until):
        with self.db:
            self.db.execute('UPDATE destination_state SET cooldown_until=MAX(cooldown_until,?) WHERE id=1', (until,))

    def diagnostics(self):
        counts = dict.fromkeys(STATES, 0)
        counts.update({r[0]: r[1] for r in self.db.execute('SELECT state,COUNT(*) FROM punches GROUP BY state')})
        pending = self.db.execute("SELECT MIN(punch_timestamp) FROM punches WHERE state IN ('pending','retry','delivering')").fetchone()[0]
        devices = {r[0]: r[1] for r in self.db.execute("SELECT device_id,COUNT(*) FROM punches WHERE state IN ('pending','retry','delivering') GROUP BY device_id")}
        return dict(schema_version=SCHEMA_VERSION, total=sum(counts.values()), states=counts,
                    oldest_pending_timestamp=pending, pending_per_device=devices,
                    last_successful_delivery=self.db.execute('SELECT MAX(last_successful_delivery) FROM device_state').fetchone()[0],
                    destination_cooldown_until=self.db.execute('SELECT cooldown_until FROM destination_state WHERE id=1').fetchone()[0])
