"""Explicit recovery batches; no source state transitions and no production enablement."""
from collections import Counter
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import uuid

from durable_delivery import FrappeClient
from durable_startup import worker_lock
from sync_ledger import Ledger
from .client import ReadOnlyFrappeClient, VerificationError
from .engine import CATEGORIES, contract_blockers, investigate
from .repository import SQLiteRepository
from .reporting import FIELDS, validate_output
from .source import verified_source
from scripts.attendance_reconciliation import csv_cell, local_time, verified_json


def require(value, reason):
    if not value:
        raise VerificationError(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def bindings(metadata):
    for entry in metadata['inputs']:
        require(file_hash(entry['path']) == entry['sha256'], 'recovery_input_changed')


def eligible_source(source):
    return (source['state'] == 'pending' and source['attempt_count'] == 0
            and source['error_category'] is None and not source['checkin_name'])


def planned_item(source, employee):
    eligible = eligible_source(source)
    return dict(id=source['id'], source=source, employee=employee,
        status='Pending Approval' if eligible else 'Skipped',
        reason='verified_missing_requires_approval' if eligible else 'source_state_or_historical_hold_excluded')


def verify_batch(store, batch, fingerprint):
    require(batch['fingerprint'] == fingerprint, 'batch_fingerprint_mismatch')
    metadata = json.loads(batch['metadata'])
    planned = [planned_item(json.loads(item['source']), item['employee']) for item in store.items(batch['id'])]
    require(digest(dict(metadata=metadata, items=planned)) == fingerprint, 'batch_contents_changed')
    bindings(metadata)
    return metadata


def preparation(ledger_path, summary_path, summary_sha256, contract_path, contract_sha256, *, receipt_review):
    """Return a validated plan without creating a queue or changing any source.

    receipt_review references the operator's verified supervisor completion receipt;
    it is an explicit review assertion, not cryptographic proof of a deployment.
    """
    require(isinstance(receipt_review, str) and bool(receipt_review.strip()), 'completed_investigation_review_required')
    summary, _, summary_path = verified_json(summary_path, summary_sha256)
    contract, _, contract_path = verified_json(contract_path, contract_sha256)
    require(Path(ledger_path).is_absolute(), 'absolute_source_path_required')
    source_path = Path(ledger_path).resolve(strict=True)
    # Reject WAL/hot-journal inputs before SQLite can create/read sidecar state.
    with source_path.open('rb') as stream:
        header = stream.read(20)
    require(header[:16] == b'SQLite format 3\x00' and header[18:20] == b'\x01\x01'
            and not any(Path(str(source_path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')),
            'self_contained_rollback_journal_backup_required')
    source_hash = file_hash(source_path)
    # Pure validation is also used by the service handoff; it creates no queue or database.
    require(summary.get('schema') == 'attendance-investigation-v1' and summary.get('status') == 'complete'
            and summary.get('blockers') == [] and 'compatibility_status' not in summary
            and summary.get('purpose') is None and summary.get('recovery_authorized') is False
            and summary.get('execution_supported') is False, 'complete_reconciliation_required')
    start, end = local_time(summary['range_start']), local_time(summary['range_end'])
    devices = summary['device_ids']
    require(not contract_blockers(contract, start, end, devices, contract['site_url']), 'verified_contract_required')
    # Match the established Phase 1 confirmation identity; no new precision/null policy.
    require(contract['duplicate_key'] == 'employee_time_log_type' and contract['timestamp_precision'] == 'seconds',
            'recovery_policy_unsupported')
    require(summary.get('contract_file_sha256') == contract_sha256.lower()
            and summary.get('contract_sha256') == digest(contract), 'report_contract_binding_mismatch')
    require(summary.get('matching_policy') == {k: contract[k] for k in ('site_timezone', 'timestamp_precision', 'duplicate_key')},
            'report_policy_mismatch')
    specs = summary.get('reports', [])
    require(len(specs) == 1, 'one_investigation_csv_required')
    name = specs[0]['file']
    require(isinstance(name, str) and Path(name).name == name and '/' not in name and '\\' not in name,
            'report_filename_invalid')
    detail_path = summary_path.parent / name
    require(file_hash(detail_path) == specs[0]['sha256'], 'investigation_csv_hash_mismatch')
    source = SQLiteRepository(source_path).read(start, end, devices)
    require(digest(source) == summary.get('source_snapshot_sha256'), 'investigation_snapshot_mismatch')
    reader = csv.DictReader(io.StringIO(detail_path.read_text(encoding='utf-8'), newline=''))
    require(reader.fieldnames == list(FIELDS), 'investigation_csv_columns_invalid')
    reports = list(reader)
    require(len(reports) == len(source), 'investigation_rows_incomplete')
    source_by_id = {str(r['id']): r for r in source}
    require(len({r.get('id') for r in reports}) == len(reports), 'investigation_rows_repeated')
    for row in reports:
        original = source_by_id.get(row['id'])
        require(original is not None and all(row.get(k) == csv_cell(v) for k, v in original.items()),
                'investigation_source_disagrees')
    counts = Counter(r['classification'] for r in reports)
    require(summary['totals']['records'] == len(source)
            and set(counts) <= set(CATEGORIES)
            and all(summary['totals'].get(k) == counts[k] for k in CATEGORIES)
            and all(r['eligible_for_recovery'] == 'False' for r in reports), 'investigation_counts_disagree')
    metadata = dict(source=str(source_path), contract=contract, range_start=str(start), range_end=str(end),
        devices=devices, receipt_review=receipt_review, created=dt.datetime.now(dt.timezone.utc).isoformat(),
        inputs=[dict(path=str(p), sha256=h.lower()) for p, h in (
            (source_path, source_hash), (contract_path, contract_sha256),
            (summary_path, summary_sha256), (detail_path, specs[0]['sha256']))])
    bindings(metadata)
    planned = []
    for report in reports:
        if report['classification'] != 'potentially_missing':
            continue
        original = source_by_id[report['id']]
        stamp, _ = verified_source(original)
        require(not stamp.microsecond and bool(report['employee']) and report['duplicate_group_size'] == '1',
                'candidate_identity_unverified')
        # Held/rejected records are visible for audit, never silently copied to a new queue.
        require(not report['employee'].startswith("'"), 'escaped_employee_name_requires_review')
        planned.append(planned_item(original, report['employee']))
    planned.sort(key=lambda item: item['id'])
    return metadata, planned


def prepare(store, ledger_path, summary_path, summary_sha256, contract_path, contract_sha256, *, receipt_review, acl_check=None):
    metadata, planned = preparation(ledger_path, summary_path, summary_sha256, contract_path, contract_sha256,
        receipt_review=receipt_review)
    protection = {} if acl_check is None else dict(acl_check=acl_check)
    validate_output(store.path.parent, ledger_path=Path(metadata['source']),
        input_paths=[Path(entry['path']) for entry in metadata['inputs'][1:]], **protection)
    batch_id = uuid.uuid4().hex
    fingerprint = digest(dict(metadata=metadata, items=planned))
    with store.exclusive(), store.db:
        store.db.execute('INSERT INTO batches VALUES(?,?,?)', (batch_id, fingerprint, json.dumps(metadata)))
        for item in planned:
            store.db.execute('INSERT INTO items VALUES(?,?,?,?,?,?)', (batch_id, item['id'], json.dumps(item['source']),
                item['employee'], item['status'], item['reason']))
            store.event(batch_id, item['id'], item['status'], item['reason'], receipt_review)
    return batch_id, fingerprint


def approve(store, batch_id, fingerprint, ids, reference):
    require(bool(reference.strip()) and ids and len(set(ids)) == len(ids), 'explicit_record_approval_required')
    with store.exclusive():
        batch = store.batch(batch_id)
        verify_batch(store, batch, fingerprint)
        items = {r['id']: r for r in store.items(batch_id)}
        require(all(i in items and items[i]['status'] in ('Pending Approval', 'Approved') for i in ids),
                'record_not_approvable')
        with store.db:
            for record_id in ids:
                if items[record_id]['status'] == 'Pending Approval':
                    store.update(batch_id, record_id, 'Approved', 'operator_selected_record', reference)


class _OneSource:
    def __init__(self, row):
        self.row = row

    def read(self, *args):
        return [self.row]


def execute(store, batch_id, fingerprint, target, normal_worker_path, *, stop_requested=lambda: False):
    """One explicitly invoked finite batch; at most ONE POST intent per identity.

    Live transports are deliberately unavailable in Phase 2B. Future enablement
    needs separate operational approval and verified cross-host exclusion.
    """
    from .recovery_simulation import SimulationTarget
    require(type(target) is SimulationTarget, 'production_execution_disabled')
    batch = store.batch(batch_id)
    metadata = verify_batch(store, batch, fingerprint)
    require(metadata['contract'].get('destination_environment') == 'synthetic'
            and target.url == metadata['contract']['site_url'], 'production_execution_disabled')
    require(not any(target.path.samefile(Path(entry['path'])) for entry in metadata['inputs'])
            and not target.path.samefile(store.path), 'simulation_must_not_modify_pinned_inputs')
    require(Path(normal_worker_path).is_absolute(), 'absolute_worker_lock_binding_required')
    # Same global mutex as normal delivery. Never stop or reconfigure its owner.
    with store.exclusive(), worker_lock(normal_worker_path):
        bindings(metadata)
        for item in store.items(batch_id):
            if stop_requested():
                break
            if item['status'] not in ('Approved', 'Needs Review'):
                continue
            bindings(metadata)
            source = json.loads(item['source'])
            with Ledger(Path(metadata['source']), readonly=True) as ledger:
                uncertain_peer = ledger.has_uncertain_peer(source)
            if uncertain_peer:
                with store.db:
                    store.update(batch_id, item['id'], 'Needs Review', 'phase1_uncertain_source_peer')
                continue
            stamp, log_type = verified_source(source)
            reader = ReadOnlyFrappeClient(target.url, 'SYNTHETIC', 'SYNTHETIC', target.request,
                timeout=10, max_requests=32)
            rows, summary = investigate(_OneSource(source), reader, stamp, stamp, [source['device_id']], metadata['contract'])
            result = rows[0]
            safe = (summary['status'] == 'complete' and not summary['blockers']
                    and result['employee'] == item['employee'])
            if safe and result['classification'] == 'already_existing':
                with store.db:
                    store.update(batch_id, item['id'], 'Already Exists', 'destination_revalidated', result['checkin_names'])
                continue
            if not safe or result['classification'] != 'potentially_missing':
                with store.db:
                    store.update(batch_id, item['id'], 'Needs Review', 'destination_or_mapping_unverified')
                continue
            identity = digest([target.url, item['employee'], str(stamp), log_type])
            prior = store.db.execute('SELECT 1 FROM intents WHERE identity=?', (identity,)).fetchone()
            if prior or item['status'] == 'Needs Review':
                with store.db:
                    store.update(batch_id, item['id'], 'Needs Review', 'prior_intent_never_blindly_resubmitted')
                continue
            bindings(metadata)
            # Persist uncertainty BEFORE any POST. A crash before/after the request
            # burns this identity's submission permission until destination proof exists.
            with store.db:
                store.db.execute('INSERT INTO intents VALUES(?,?,?,?,NULL)',
                    (identity, batch_id, item['id'], dt.datetime.now(dt.timezone.utc).isoformat()))
                store.update(batch_id, item['id'], 'Needs Review', 'submission_intent_recorded')
            outcome, _ = FrappeClient(target.config, target.request).send(json.loads(source['payload']), 0)
            with store.db:
                store.db.execute('UPDATE intents SET outcome=? WHERE identity=?', (json.dumps(outcome), identity))
                store.event(batch_id, item['id'], 'Needs Review', 'phase1_delivery_response', outcome[2] or '')
            # Even HTTP 200 is provisional until an independent complete read matches.
            rows, verification = investigate(_OneSource(source), reader, stamp, stamp, [source['device_id']], metadata['contract'])
            confirmed = (verification['status'] == 'complete' and rows[0]['classification'] == 'already_existing'
                         and rows[0]['employee'] == item['employee'])
            with store.db:
                if confirmed and outcome[0] == 'synced' and rows[0]['checkin_names'] == outcome[4]:
                    store.update(batch_id, item['id'], 'Recovered', 'independent_destination_confirmation', outcome[4])
                elif confirmed:
                    store.update(batch_id, item['id'], 'Already Exists', 'confirmed_after_uncertain_outcome', rows[0]['checkin_names'])
                elif outcome[0] in ('rejected', 'unmatched_employee'):
                    store.update(batch_id, item['id'], 'Failed', 'phase1_business_rejection')
                else:
                    store.update(batch_id, item['id'], 'Needs Review', 'submission_not_independently_verified')


def results(store, batch_id):
    with store.exclusive():
        items = store.items(batch_id)
        events = [dict(r) for r in store.db.execute('SELECT * FROM events WHERE batch=? ORDER BY seq', (batch_id,))]
        intents = [dict(r) for r in store.db.execute('SELECT * FROM intents WHERE batch=? ORDER BY item', (batch_id,))]
    return dict(schema='attendance-recovery-batch-v1', batch=batch_id, fingerprint=store.batch(batch_id)['fingerprint'],
        production_execution_supported=False, counts=dict(Counter(r['status'] for r in items)),
        items=items, events=events, submission_intents=intents)


def export_results(store, batch_id, output, *, acl_check=None):
    from scripts.reconcile_attendance import publish
    data = results(store, batch_id)
    metadata = json.loads(store.batch(batch_id)['metadata'])
    protection = {} if acl_check is None else dict(acl_check=acl_check)
    output = validate_output(output, ledger_path=Path(metadata['source']),
        input_paths=[store.path, *(Path(entry['path']) for entry in metadata['inputs'])], **protection)
    stem = 'recovery_' + batch_id + '_' + uuid.uuid4().hex
    fields = ('batch', 'id', 'employee', 'status', 'reason', 'source')
    def write_csv(stream):
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: csv_cell(item[k]) for k in fields} for item in data['items'])
    detail = publish(output, stem + '.csv', write_csv)
    data['reports'] = [detail]
    return publish(output, stem + '.json', lambda stream: json.dump(data, stream, indent=2, sort_keys=True))
