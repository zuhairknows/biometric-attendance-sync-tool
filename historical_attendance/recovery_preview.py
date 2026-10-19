"""Local ledger evidence only; never a destination comparison or recovery input."""
import json
from pathlib import Path
import re

from .recovery import eligible_source, file_hash
from .repository import SQLiteRepository
from .source import verified_source
from .reporting import FIELDS, export, validate_output
from scripts.attendance_reconciliation import local_time
from scripts.capture_history_safety import check_directory_acl, PROTECTED_ROOTS

NOTICE = 'Local recovery preview - ERPNext destination unverified. Not authorized for recovery.'
CATEGORIES = (
    'Candidate for destination verification', 'Already synchronized',
    'Historical cutover hold', 'Uncertain or previously attempted',
    'Unmatched employee', 'Invalid or ambiguous',
)
MESSAGES = {
    'preview_snapshot_invalid': 'Preview blocked: use the approved private, self-contained SQLite backup outside production and checkout directories. Check its approved backup_hash and ensure no journal/WAL sidecars exist.',
    'preview_output_invalid': 'Preview blocked: check the private report directory, access, free space and separation from source and production directories.',
    'preview_failed': 'Preview could not complete. Check snapshot integrity/schema and selected dates/devices. No accepted result is available.',
    'preview_inputs_changed': 'Preview blocked: the approved snapshot or published reports changed. Review the inputs and run a new preview.',
}


class PreviewError(ValueError):
    """Fixed codes only: raw filesystem/database errors must not reach the browser."""


def snapshot(path, approved_hash, acl_check=check_directory_acl):
    try:
        original = Path(path)
        if (not original.is_absolute() or str(original).startswith('\\\\')
                or original.is_symlink() or not re.fullmatch('[0-9a-fA-F]{64}', approved_hash)):
            raise ValueError
        source = original.resolve(strict=True)
        protected = (*PROTECTED_ROOTS, Path(__file__).resolve().parents[1])
        if not source.is_file() or any(p.resolve() == source or p.resolve() in source.parents for p in protected):
            raise ValueError
        acl_check(source.parent)
        acl_check(source)
        with source.open('rb') as stream:
            header = stream.read(20)
        if (header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01'
                or any(Path(str(source) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
                or file_hash(source) != approved_hash.lower()):
            raise ValueError
        return source
    except Exception:
        raise PreviewError('preview_snapshot_invalid') from None


def output(directory, source, acl_check):
    try:
        return validate_output(directory, ledger_path=source, acl_check=acl_check)
    except Exception:
        raise PreviewError('preview_output_invalid') from None


def classify(source):
    # Keep original identity, state and audit columns, even for invalid evidence.
    row = {name: source.get(name, '') for name in FIELDS}
    row.update(eligible_for_recovery=False, historical_hold=source['error_category'] == 'cutover_hold',
               employee='', comparison_timestamp='', checkin_names='', duplicate_group_size='')
    try:
        verified_source(source)
        valid = True
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError):
        valid = False
    if row['historical_hold']:
        category, reason = CATEGORIES[2], 'Local historical hold preserved; no release or destination verification.'
        if not valid:
            reason += ' Source evidence also invalid or ambiguous.'
    elif not valid or source['state'] == 'invalid_record':
        category, reason = CATEGORIES[5], 'Source identity, timestamp or payload could not be verified.'
    elif source['state'] in ('synced', 'duplicate'):
        category, reason = CATEGORIES[1], 'Ledger records completed delivery only; ERPNext existence has not been checked.'
    elif source['state'] == 'unmatched_employee':
        category, reason = CATEGORIES[4], 'Ledger records an unresolved employee mapping; no current mapping lookup performed.'
    elif (eligible_source(source) and not any(source.get(k) is not None for k in
            ('last_attempt', 'lease_until', 'last_http_status')) and source['next_retry'] == 0
            and not source.get('error_message')):
        category, reason = CATEGORIES[0], 'Untouched local pending record; employee mapping, cutover policy and destination identity still require verification.'
    else:
        category, reason = CATEGORIES[3], 'Previous attempt, rejection or uncertain local delivery evidence; review before any delivery.'
    row.update(classification=category, reason=reason)
    return row


def preview(*, backup, backup_hash, directory, start, end, devices,
            acl_check=check_directory_acl, cancelled=lambda: False):
    start, end = local_time(start), local_time(end)
    if (start > end or not devices or len(devices) != len(set(devices))
            or any(not re.fullmatch(r'FP[12]_DEVICE_\d{2,3}', d) for d in devices)):
        raise PreviewError('preview_failed')
    source = snapshot(backup, backup_hash, acl_check)
    directory = output(directory, source, acl_check)
    rows = [classify(row) for row in SQLiteRepository(source).read(start, end, devices, cancelled)]
    # Recheck before publishing: a receipt must bind unchanged approved source bytes.
    snapshot(backup, backup_hash, acl_check)
    if cancelled():
        raise PreviewError('preview_failed')
    counts = lambda records: {c: sum(r['classification'] == c for r in records) for c in CATEGORIES}
    summary = dict(schema='local-recovery-preview-v1', purpose='local_state_preview',
        status='destination_unverified', notice=NOTICE, recovery_authorized=False, execution_supported=False,
        eligible_for_recovery=False, destination_verified=False, source_file_sha256=backup_hash.lower(),
        range_start=str(start), range_end=str(end), device_ids=devices, total=len(rows), totals=counts(rows),
        per_device={d: counts([r for r in rows if r['device_id'] == d]) for d in devices},
        limitations=['Source times are original device wall times; timezone and destination precision are not verified.',
            'No employee mapping or ERPNext comparison was performed. No record is confirmed missing.',
            'Only records in this snapshot are visible. Invalid timestamps remain included with unknown range membership.',
            'Local synced/duplicate and unmatched states are historical evidence, not current destination or employee verification.',
            'Candidate classification does not authorize delivery or resolve historical cutover policy.'])
    receipt = export(directory, rows, summary, ledger_path=source, acl_check=acl_check, notice=NOTICE)
    summary_path = directory / receipt['file']
    published = json.loads(summary_path.read_text(encoding='utf-8'))
    return dict(rows=rows, summary=published, json_path=str(summary_path),
                csv_path=str(directory / published['reports'][0]['file']))


def verify_export(result, *, backup, backup_hash, directory, acl_check=check_directory_acl):
    source = snapshot(backup, backup_hash, acl_check)
    directory = output(directory, source, acl_check)
    try:
        summary = result['summary']
        if (summary['schema'] != 'local-recovery-preview-v1' or summary['source_file_sha256'] != backup_hash.lower()
                or any(Path(result[k]).parent != directory for k in ('csv_path', 'json_path'))
                or file_hash(Path(result['csv_path'])) != summary['reports'][0]['sha256']
                or json.loads(Path(result['json_path']).read_text(encoding='utf-8')) != summary):
            raise ValueError
    except Exception:
        raise PreviewError('preview_inputs_changed') from None
