"""Publish detailed private evidence first, then its completion summary."""
import csv
import datetime as dt
import json
import uuid

from scripts.attendance_reconciliation import csv_cell
from scripts.reconcile_attendance import publish
from sync_ledger import CAPTURE_FIELDS


FIELDS = ('id',) + CAPTURE_FIELDS + (
    'attempt_count', 'next_retry', 'last_attempt', 'lease_until', 'last_http_status',
    'checkin_name', 'first_captured', 'last_observed', 'created_at', 'updated_at',
    'range_status', 'historical_hold', 'employee', 'comparison_timestamp', 'classification',
    'reason', 'checkin_names', 'duplicate_group_size', 'eligible_for_recovery')


def write_csv(stream, rows):
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows({f: csv_cell(row[f]) for f in FIELDS} for row in rows)


def export(directory, rows, summary):
    run = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex
    details = publish(directory, 'investigation_details_' + run + '.csv', lambda s: write_csv(s, rows))
    summary = dict(summary, reports=[details])
    # This file is the completion marker. CSV without a summary is incomplete.
    return publish(directory, 'investigation_summary_' + run + '.json',
                   lambda s: json.dump(summary, s, indent=2, sort_keys=True))
