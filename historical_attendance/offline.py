"""File-only adapter for Phase 2A. No HTTP client, device or delivery transport."""
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

from .client import VerificationError
from .engine import CLAIMS, investigate
from .repository import SQLiteRepository
from .reporting import export, validate_output
from scripts.attendance_reconciliation import child_file, local_time, uncertain_wall_time, site_zone, verified_json
from scripts.capture_history_safety import check_directory_acl, PROTECTED_ROOTS

LABEL = 'Offline comparison — Not authorized for recovery'
MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 100000
HEADERS = {
    'ID': 'name', 'Attendance Device ID': 'attendance_device_id', 'Status': 'status',
    'Employee': 'employee', 'Time': 'time', 'Log Type': 'log_type',
    'Device ID': 'device_id', 'Modified': 'modified',
}


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def import_root(value, acl_check=check_directory_acl):
    root = Path(value)
    require(root.is_absolute() and root.is_dir() and not str(root).startswith('\\\\'), 'offline_import_directory_invalid')
    root = root.resolve()
    require(not any(root == p.resolve() or p.resolve() in root.parents for p in PROTECTED_ROOTS),
            'offline_import_directory_invalid')
    acl_check(root)
    return root


def approved_file(root, name, acl_check):
    # child_file rejects paths, traversal, alternate streams and resolved escapes.
    path = child_file(root, name)
    require(path.is_file() and not (root / name).is_symlink(), 'offline_input_path_invalid')
    acl_check(path)
    return path


def choices(directory, acl_check=check_directory_acl):
    root = import_root(directory, acl_check)
    files = []
    for path in root.iterdir():
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.csv', '.sqlite3'):
            files.append(path.name)
            require(len(files) <= 200, 'offline_import_directory_too_large')
    files.sort()
    return dict(snapshots=[n for n in files if n.lower().endswith('.sqlite3')],
                csvs=[n for n in files if n.lower().endswith('.csv')])


def read_csv(path, required, expected_hash):
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, 'offline_csv_size_limit')
    # Hash the exact bytes parsed, not just a separate before/after file read.
    require(hashlib.sha256(data).hexdigest() == expected_hash, 'offline_export_hash_mismatch')
    # Strict UTF-8/BOM CSV only. Never strip or normalize identity values.
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''), strict=True)
    headers = [HEADERS.get(h, h) for h in (reader.fieldnames or [])]
    require(len(headers) == len(set(headers)) and set(required) <= set(headers)
            and set(headers) <= set(HEADERS.values()), 'offline_csv_headers_invalid')
    rows, names = [], set()
    for raw in reader:
        require(None not in raw and all(v is not None for v in raw.values()), 'offline_csv_row_malformed')
        row = {HEADERS.get(k, k): v for k, v in raw.items()}
        require(bool(row['name']) and row['name'] not in names, 'offline_csv_duplicate_or_empty_id')
        names.add(row['name'])
        rows.append(row)
        require(len(rows) <= MAX_ROWS, 'offline_csv_row_limit')
    return rows


class FrozenSource:
    def __init__(self, rows):
        self.rows = rows

    def read(self, *args):
        return self.rows


class ExportDestination:
    """snapshot implements the existing engine seam, exclusively from reviewed files."""
    requests_made = 0

    def __init__(self, contract, evidence, employees, checkins, blockers):
        self.url = contract.get('site_url', '')
        self.evidence, self.employees, self.checkins, self.blockers = evidence, employees, checkins, blockers

    def snapshot(self, start, end, users):
        require(not self.blockers, 'offline_exports_unverified')
        lookups = self.evidence.get('employee_lookup_results', {})
        require(isinstance(lookups, dict), 'offline_employee_equality_unverified')
        by_name = {r['name']: r for r in self.employees}
        mapping = {}
        for user in users:
            result = lookups.get(user, {})
            require(isinstance(result, dict), 'offline_employee_equality_unverified')
            names = result.get('employee_names')
            # These are independently reviewed database-equality results for the
            # ORIGINAL source ID, not a Python comparison of exported device IDs.
            require(result.get('verified') is True and bool(result.get('evidence_reference'))
                    and isinstance(names, list) and all(isinstance(n, str) and n in by_name for n in names)
                    and len(names) == len(set(names)), 'offline_employee_equality_unverified')
            mapping[user] = [by_name[n] for n in names]
        return mapping, [r for r in self.checkins if start <= local_time(r['time']) <= end]


def compare(*, directory, evidence_name, evidence_hash, snapshot, employees, checkins,
            start, end, devices, output, acl_check=check_directory_acl):
    root = import_root(directory, acl_check)
    evidence_path = approved_file(root, evidence_name, acl_check)
    require(evidence_path.stat().st_size <= MAX_BYTES, 'offline_evidence_size_limit')
    evidence, evidence_hash, _ = verified_json(evidence_path, evidence_hash)
    source = approved_file(root, snapshot, acl_check)
    require(source.suffix.lower() == '.sqlite3', 'offline_source_type_invalid')
    require(employees != checkins, 'offline_exports_must_be_distinct')
    require(start <= end and devices and len(devices) == len(set(devices)), 'invalid_range_or_devices')
    files = evidence.get('files', {})
    require(files.get('snapshot', {}).get('file') == snapshot
            and digest(source) == files['snapshot'].get('sha256'), 'offline_source_changed_or_unapproved')
    # As in Phase 2B preparation, reject WAL/hot-journal inputs before SQLite
    # could create sidecars. A protected self-contained backup is mandatory.
    with source.open('rb') as stream:
        header = stream.read(20)
    require(header[:16] == b'SQLite format 3\x00' and header[18:20] == b'\x01\x01'
            and not any(Path(str(source) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')),
            'self_contained_rollback_journal_backup_required')
    contract = evidence.get('comparison_contract', {})
    require(isinstance(contract, dict), 'offline_evidence_invalid')
    # All original Phase 2A verification claims remain operator assertions. No
    # claims are generated or set true here; HTTP endpoint safety is irrelevant
    # because this adapter has no HTTP implementation.
    inputs = [evidence_path]
    validate_output(output, ledger_path=source, input_paths=inputs, acl_check=acl_check)
    before_source = digest(source)
    source_rows = SQLiteRepository(source).read(start, end, devices)
    blockers, imported, observed = [], {}, dict(evidence=evidence_hash, snapshot=before_source)
    try:
        require(evidence.get('schema') == 'attendance-offline-evidence-v1'
                and evidence.get('export_set_consistent') is True
                and bool(evidence.get('review_reference')), 'offline_export_evidence_unverified')
        for role, selected, required in (
                ('employees', employees, ('name', 'attendance_device_id', 'status')),
                ('checkins', checkins, ('name', 'employee', 'time', 'log_type', 'device_id'))):
            meta = files.get(role, {})
            require(meta.get('file') == selected and selected.lower().endswith('.csv'), 'offline_export_selection_unapproved')
            path = approved_file(root, selected, acl_check)
            inputs.append(path)
            observed[role] = digest(path)
            require(observed[role] == meta.get('sha256'), 'offline_export_hash_mismatch')
            rows = read_csv(path, required, meta.get('sha256'))
            parsed = urlsplit(meta.get('origin', ''))
            created = dt.datetime.fromisoformat(meta.get('created_at', ''))
            require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                    and not parsed.password and not parsed.query and not parsed.fragment
                    and parsed.path in ('', '/') and meta['origin'] == contract.get('site_url')
                    and created.tzinfo is not None and created <= dt.datetime.now(dt.timezone.utc)
                    and meta.get('count_verified') is True and bool(meta.get('count_reference'))
                    and type(meta.get('independent_count')) is int and meta['independent_count'] == len(rows),
                    'offline_export_count_or_origin_unverified')
            imported[role] = rows
        require(files['employees'].get('filters') == [] and files['employees'].get('includes_inactive') is True,
                'offline_employee_coverage_unverified')
        lower, upper = (local_time(files['checkins'][key]) for key in ('coverage_start', 'coverage_end'))
        require(lower <= start <= end <= upper and files['checkins'].get('filters') ==
                [['time', '>=', str(lower)], ['time', '<=', str(upper)]], 'offline_checkin_coverage_unverified')
        require(evidence.get('log_type_encoding_verified') is True
                and evidence.get('null_log_type_token') == '__SQL_NULL__', 'offline_null_blank_unverified')
        zone = site_zone(contract['site_timezone'])
        require(contract.get('timestamp_precision') in ('seconds', 'microseconds'), 'offline_precision_unverified')
        for row in imported['employees']:
            require(row['status'] in ('Active', 'Inactive', 'Left'), 'offline_employee_status_invalid')
        for row in imported['checkins']:
            stamp = local_time(row['time'])
            require(lower <= stamp <= upper and not uncertain_wall_time(stamp, zone)
                    and (contract['timestamp_precision'] != 'seconds' or not stamp.microsecond)
                    and bool(row['employee']) and row['log_type'] in ('', 'IN', 'OUT', '__SQL_NULL__'),
                    'offline_checkin_timestamp_or_direction_unverified')
            if row['log_type'] == '__SQL_NULL__':
                row['log_type'] = None
    except VerificationError as error:
        blockers.append(str(error))
    except Exception:
        # CSV, metadata and permission errors may include private paths/values.
        blockers.append('offline_export_invalid_or_unreadable')
    destination = ExportDestination(contract, evidence, imported.get('employees', []), imported.get('checkins', []), blockers)
    rows, summary = investigate(FrozenSource(source_rows), destination, start, end, devices, contract)
    summary['blockers'] = list(dict.fromkeys(summary['blockers'] + blockers))
    summary.update(schema='attendance-offline-comparison-v1', purpose='offline_comparison',
                   label=LABEL, status='unverified' if summary['blockers'] else 'complete',
                   input_hashes={role: observed.get(role) for role in ('evidence', 'snapshot', 'employees', 'checkins')},
                   evidence_file_sha256=evidence_hash)
    summary['unverified_claims'] = [claim for claim in CLAIMS if contract.get('verification', {}).get(claim) is not True]
    summary['export_provenance'] = {role: {key: files.get(role, {}).get(key) for key in
        ('origin', 'created_at', 'filters', 'independent_count', 'count_verified', 'count_reference',
         'coverage_start', 'coverage_end', 'includes_inactive')} for role in ('employees', 'checkins')}
    summary['limitations'] = [note for note in summary['limitations'] if not note.startswith('REST scans')] + [
        'File hashes bind the export set; no live destination reads or repeat API scans were performed.',
        'Completeness, database equality and deployed comparison rules depend on independent administrator evidence.',
        'CSV blank cells cannot distinguish SQL NULL without the reviewed encoding evidence.',
        LABEL]
    # Detect changes during import/comparison before issuing a completed report.
    require(digest(source) == before_source == files['snapshot']['sha256']
            and digest(evidence_path) == evidence_hash, 'offline_inputs_changed')
    for path in inputs[1:]:
        role = 'employees' if path.name == employees else 'checkins'
        require(digest(path) == files[role]['sha256'], 'offline_inputs_changed')
    report = export(output, rows, summary, ledger_path=source, input_paths=inputs, acl_check=acl_check, notice=LABEL)
    # Keep the common manager presentation shape without making it a recovery report.
    summary_path = Path(output) / report['file']
    published = json.loads(summary_path.read_text(encoding='utf-8'))
    return dict(summary=published, rows=rows, json_path=str(summary_path),
                csv_path=str(Path(output) / published['reports'][0]['file']))
