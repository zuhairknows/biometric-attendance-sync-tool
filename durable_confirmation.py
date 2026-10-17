"""One approved confirmation batch; no sender, polling or global policy changes."""
import datetime as dt
import json
import time

from sync_ledger import Ledger
from durable_startup import validate_activation

APPROVED = {270480: 'EMP-CKIN-10-2026-052260', 270334: 'EMP-CKIN-10-2026-052130'}
COOKIE = 1714148376
DESTINATION = 'ca257ae329c012dbf7e09277711b3fcf0f4e53aa567a72d3ea24da95f144c0ec'
BOUNDARY = '2026-10-17 08:58:22'
GUARDED = ('identity_key', 'device_id', 'user_id', 'device_uid', 'punch_timestamp',
           'punch_code', 'status_code', 'source_evidence', 'payload', 'state',
           'attempt_count', 'last_attempt', 'lease_until', 'next_retry', 'updated_at',
           'error_category', 'error_message', 'last_http_status', 'checkin_name')


def require(value):
    if not value:
        raise ValueError('Confirmation refused')


def binding(path, config):
    marker = validate_activation(path, config)
    require(marker['ledger_cookie'] == COOKIE and marker['destination_sha256'] == DESTINATION
            and marker['cutover_time'] == BOUNDARY)
    return marker


class ConfirmationLedger(Ledger):
    def confirm_approved(self, snapshot, name, config, marker):
        """Short atomic CAS: never take a submission lease or interrupt the worker."""
        require(not self.readonly and APPROVED.get(snapshot['id']) == name)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            require(binding(self.path, config) == marker)
            require(self.db.execute('PRAGMA application_id').fetchone()[0] == COOKIE)
            current = self.db.execute('SELECT * FROM punches WHERE id=?', (snapshot['id'],)).fetchone()
            require(current is not None and all(current[key] == snapshot[key] for key in GUARDED))
            require(current['state'] == 'retry' and current['error_category'] == 'confirmation_pending'
                    and current['lease_until'] is None)
            now = time.time()
            # Engine-owned transition and audit commit together, or neither commits.
            self.db.execute("UPDATE punches SET state='duplicate',error_category='destination_confirmed',"
                            "error_message='Approved two-record GET confirmation',checkin_name=?,"
                            'next_retry=0,last_http_status=200,updated_at=? WHERE id=?',
                            (name, now, snapshot['id']))
            self.db.execute('INSERT INTO delivery_attempts(punch_id,attempted_at,outcome,http_status,'
                            'error_category,error_message) VALUES(?,?,?,?,?,?)',
                            (snapshot['id'], now, 'duplicate', 200, 'destination_confirmed',
                             'Approved two-record GET confirmation: ' + name))


def reconcile(path, config, get, apply=False):
    """get accepts only a resource name, fields and filters; it cannot submit."""
    marker = binding(path, config)
    results = {}
    # A preview opens SQLite read-only. Execution never creates a missing ledger.
    with ConfirmationLedger(path, readonly=not apply) as ledger:
        for record_id, name in APPROVED.items():
            row = ledger.db.execute('SELECT * FROM punches WHERE id=?', (record_id,)).fetchone()
            require(row is not None)
            row = dict(row)
            completed = (row['state'] == 'duplicate' and row['error_category'] == 'destination_confirmed'
                         and row['checkin_name'] == name)
            require(row['lease_until'] is None and (completed or
                    (row['state'] == 'retry' and row['error_category'] == 'confirmation_pending')))
            payload = json.loads(row['payload'])
            require(payload['log_type'] in (None, '') and payload['employee_field_value'] == row['user_id'])
            stamp = dt.datetime.fromisoformat(payload['timestamp'])
            require(stamp.tzinfo is None and stamp == dt.datetime.fromisoformat(row['punch_timestamp'])
                    and stamp >= dt.datetime.fromisoformat(BOUNDARY))
            employees = get('Employee', ['name', 'status', 'attendance_device_id'],
                            [['attendance_device_id', '=', payload['employee_field_value']]])
            require(isinstance(employees, list) and len(employees) == 1)
            employee = employees[0]
            require(employee['status'] == 'Active' and employee['attendance_device_id'] == row['user_id'])
            second = stamp.replace(microsecond=0)
            checkins = get('Employee Checkin', ['name', 'employee', 'time', 'log_type', 'device_id'],
                          [['employee', '=', employee['name']], ['time', '=', str(second)], ['log_type', '=', '']])
            require(isinstance(checkins, list) and len(checkins) == 1)
            checkin = checkins[0]
            require(checkin['name'] == name and checkin['employee'] == employee['name']
                    and checkin['device_id'] == row['device_id']
                    and checkin['log_type'] == '' and dt.datetime.fromisoformat(checkin['time']) == second)
            require(binding(path, config) == marker)
            if apply and not completed:
                ledger.confirm_approved(row, name, config, marker)
            results[record_id] = 'ALREADY_CONFIRMED' if completed else ('CONFIRMED' if apply else 'PREVIEW_CONFIRMED')
    return results
