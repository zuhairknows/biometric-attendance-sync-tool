"""Private, engine-owned recovery audit. Never attaches or updates the source ledger."""
from contextlib import contextmanager
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3

from .client import VerificationError
from scripts.capture_history_safety import preflight_output, check_directory_acl

STATUSES = ('Pending Approval', 'Approved', 'Already Exists', 'Recovered', 'Skipped', 'Needs Review', 'Failed')
SCHEMA = (
    'CREATE TABLE batches(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, metadata TEXT NOT NULL)',
    'CREATE TABLE items(batch TEXT NOT NULL REFERENCES batches(id), id INTEGER NOT NULL, '
    'source TEXT NOT NULL, employee TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(batch,id))',
    'CREATE TABLE intents(identity TEXT PRIMARY KEY, batch TEXT NOT NULL, item INTEGER NOT NULL, '
    'created TEXT NOT NULL, outcome TEXT)',
    'CREATE TABLE events(seq INTEGER PRIMARY KEY, batch TEXT NOT NULL, item INTEGER, '
    'at TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL, reference TEXT NOT NULL)',
)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class RecoveryStore:
    def __init__(self, workspace, *, acl_check=check_directory_acl, readonly=False):
        workspace = preflight_output(workspace, live=False, acl_check=acl_check)
        # Distinct suffix prevents confusing the audit store with a source ledger.
        self.path = workspace / 'recovery-state.recovery-audit'
        self.readonly = readonly
        self.db = (sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=1)
                   if readonly else sqlite3.connect(self.path, timeout=1))
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            if readonly:
                self.db.execute('PRAGMA query_only=ON')
                self.db.execute('BEGIN')  # One consistent view while the normal audit writer may commit.
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version == 0 and not tables and not readonly:
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    for statement in SCHEMA:
                        self.db.execute(statement)
                    self.db.execute('PRAGMA user_version=1')
            elif version != 1:
                raise VerificationError('recovery_store_schema_unverified')
            actual = [r[0] for r in self.db.execute("SELECT sql FROM sqlite_master WHERE type='table'")]
            if set(''.join(s.split()) for s in actual) != set(''.join(s.split()) for s in SCHEMA):
                raise VerificationError('recovery_store_schema_unverified')
            if (self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok'
                    or self.db.execute('PRAGMA foreign_key_check').fetchone() is not None):
                raise VerificationError('recovery_store_integrity_unverified')
            if not readonly:
                self.db.execute('PRAGMA journal_mode=DELETE')
                self.db.execute('PRAGMA synchronous=FULL')
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def batch(self, batch_id):
        row = self.db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
        if row is None:
            raise VerificationError('unknown_recovery_batch')
        return dict(row)

    def items(self, batch_id):
        self.batch(batch_id)
        return [dict(r) for r in self.db.execute('SELECT * FROM items WHERE batch=? ORDER BY id', (batch_id,))]

    def event(self, batch, item, status, reason, reference=''):
        if status not in STATUSES:
            raise VerificationError('invalid_recovery_status')
        self.db.execute('INSERT INTO events(batch,item,at,status,reason,reference) VALUES(?,?,?,?,?,?)',
                        (batch, item, now(), status, reason, reference))

    def update(self, batch, item, status, reason, reference=''):
        # Callers group transitions and their audit event in the SAME durable commit.
        if self.db.execute('UPDATE items SET status=?,reason=? WHERE batch=? AND id=?',
                           (status, reason, batch, item)).rowcount != 1:
            raise VerificationError('unknown_recovery_item')
        self.event(batch, item, status, reason, reference)

    @contextmanager
    def exclusive(self):
        """Reject overlapping recovery commands, including two clients on one thread.

        Separate from Phase 1's global worker mutex, which Windows may reacquire
        recursively on its owning thread. This file lock protects the audit DB.
        """
        if self.readonly:
            yield  # Read transaction above needs no writer lock or lock-file mutation.
            return
        with Path(str(self.path) + '.lock').open('a+b') as stream:
            try:
                if os.name == 'nt':
                    import msvcrt
                    if stream.seek(0, 2) == 0:
                        stream.write(b'0')
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise VerificationError('another_recovery_writer_running') from None
            try:
                yield
            finally:
                if os.name == 'nt':
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)
