"""Publish detailed private evidence first, then its completion summary."""
import csv
import datetime as dt
import json
from pathlib import Path
import uuid

from scripts.attendance_reconciliation import csv_cell
from scripts.reconcile_attendance import publish
from sync_ledger import CAPTURE_FIELDS
from scripts.capture_history_safety import preflight_output, check_directory_acl, CaptureError


FIELDS = ('id',) + CAPTURE_FIELDS + (
    'attempt_count', 'next_retry', 'last_attempt', 'lease_until', 'last_http_status',
    'checkin_name', 'first_captured', 'last_observed', 'created_at', 'updated_at',
    'range_status', 'historical_hold', 'employee', 'comparison_timestamp', 'classification',
    'reason', 'checkin_names', 'duplicate_group_size', 'eligible_for_recovery')


def write_csv(stream, rows, notice=None):
    fields = FIELDS + (('report_notice',) if notice else ())
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows({f: csv_cell(notice if f == 'report_notice' else row[f]) for f in fields} for row in rows)


def validate_output(directory, *, ledger_path, input_paths=(), acl_check=check_directory_acl):
    """One shared policy for CLI preflight and direct application publication."""
    directory = preflight_output(directory, live=False, acl_check=acl_check)
    for value in (ledger_path, *input_paths):
        path = Path(value)
        if not path.is_absolute():
            raise CaptureError('input_path_must_be_absolute')
        path = path.resolve(strict=True)
        if not path.is_file() or directory == path.parent or path.parent in directory.parents:
            raise CaptureError('output_must_be_separate_from_inputs')
    return directory


def export(directory, rows, summary, *, ledger_path, input_paths=(), acl_check=check_directory_acl, notice=None):
    # Recheck immediately before publication, including calls outside the CLI.
    directory = validate_output(directory, ledger_path=ledger_path, input_paths=input_paths, acl_check=acl_check)
    run = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex
    details = publish(directory, 'investigation_details_' + run + '.csv', lambda s: write_csv(s, rows, notice))
    summary = dict(summary, reports=[details])
    # This file is the completion marker. CSV without a summary is incomplete.
    return publish(directory, 'investigation_summary_' + run + '.json',
                   lambda s: json.dump(summary, s, indent=2, sort_keys=True))
