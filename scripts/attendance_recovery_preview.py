"""Offline recovery review only. No transport, insertion payload, or execution mode."""
from collections import Counter, defaultdict
import csv
import datetime as dt
import hashlib
import io
import json

from scripts.attendance_reconciliation import (
    DETAIL_FIELDS, child_file, csv_cell, load_captures, load_exports, local_time,
    reconcile, require, verified_json,
)


MAX_SNAPSHOT_AGE = dt.timedelta(minutes=15)
CLAIMS = (
    'deployed_sources_reviewed', 'effective_hooks_and_overrides_reviewed',
    'null_request_defaults_to_empty_string', 'sql_null_distinct_from_empty_string',
    'employee_lookup_semantics_verified', 'timestamp_and_timezone_verified',
    'historical_device_settings_verified', 'shift_and_custom_validations_reviewed',
    'unfiltered_repeatable_read_snapshot', 'independent_counts_verified',
)
EXTRA_FIELDS = ('review_status', 'review_reason', 'in_review_scope')


def fingerprint(value):
    """A stable review identifier, deliberately not an authorization token."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def anchor(row):
    return (row['device_id'], row['source_sha256'], int(row['source_order_index']))


def read_baseline(path, digest):
    require(path.stat().st_size <= 512 * 1024 * 1024, 'baseline_too_large')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == digest, 'baseline_sha256_mismatch')
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
    require(reader.fieldnames == list(DETAIL_FIELDS), 'baseline_columns_unsupported')
    rows = {}
    for row in reader:
        require(None not in row and all(isinstance(v, str) for v in row.values()), 'baseline_row_invalid')
        key = anchor(row)
        require(key not in rows, 'baseline_source_anchor_repeated')
        rows[key] = row
    return rows


def aware(value):
    stamp = dt.datetime.fromisoformat(value)
    require(stamp.tzinfo is not None, 'review_timestamp_requires_offset')
    return stamp


def build_preview(capture_summary, capture_hash, exports_manifest, exports_hash,
                  review_manifest, review_hash, *, now=None):
    """Read pinned files; return report data and ALL input paths for output separation.

    Human review claims are trusted assertions bound by an independently supplied hash,
    not discoveries made by this offline program. False/missing claims block eligibility.
    Existing export validators remain strict; unsupported exports fail before reporting.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    require(now.tzinfo is not None, 'clock_requires_offset')
    review, digest, review_path = verified_json(review_manifest, review_hash)
    require(review.get('schema') == 'attendance-recovery-review-v1', 'review_schema_invalid')
    require(review.get('capture_summary_sha256') == capture_hash.lower()
            and review.get('exports_manifest_sha256') == exports_hash.lower(), 'review_input_binding_mismatch')
    start, end = local_time(review['range_start']), local_time(review['range_end'])
    require(start <= end, 'invalid_date_range')
    zone = review['site_timezone']
    captures, sources, paths = load_captures(capture_summary, capture_hash)
    contract, mappings, checkins, export_sources, export_paths = load_exports(
        exports_manifest, exports_hash, start, end, zone)
    # This first implementation supports ONLY the reviewed null -> blank policy.
    require(contract['duplicate_key'] == 'employee_time_log_type'
            and contract['timestamp_precision'] == 'seconds'
            and all(p['punch_direction'] is None for p in contract['device_policies'].values()),
            'recovery_policy_unsupported')

    baseline_spec = review['baseline_details']
    baseline_path = child_file(review_path.parent, baseline_spec['file'])
    baseline = read_baseline(baseline_path, baseline_spec['sha256'])
    scope = review['approved_source_records']
    require(isinstance(scope, list) and bool(scope), 'review_scope_required')
    selected = set()
    for item in scope:
        require(type(item.get('source_order_index')) is int and item['source_order_index'] >= 0,
                'review_source_index_invalid')
        key = anchor(item)
        require(key not in selected and key in baseline, 'review_scope_anchor_invalid')
        require(baseline[key]['classification'] == 'missing', 'review_scope_not_baseline_missing')
        selected.add(key)

    # CSV loses SQL NULL information. Require an exact, hashed sidecar from SAME snapshot.
    spec = review['log_type_storage']
    storage_path = child_file(review_path.parent, spec['file'])
    storage, storage_hash, _ = verified_json(storage_path, spec['sha256'])
    require(storage.get('schema') == 'employee-checkin-log-type-storage-v1'
            and isinstance(storage.get('states'), list), 'log_type_storage_schema_invalid')
    states = {}
    for item in storage['states']:
        name, state = item['name'], item['state']
        require(isinstance(name, str) and name not in states
                and state in ('null', 'empty', 'nonblank', 'unknown'), 'log_type_storage_row_invalid')
        states[name] = state
    require(set(states) == {c['name'] for c in checkins}, 'log_type_storage_incomplete')
    comparison_checkins, uncertain_storage = [], set()
    for checkin in checkins:
        state = states[checkin['name']]
        require(state == 'unknown' or (state == 'nonblank') == bool(checkin['log_type']),
                'log_type_storage_disagrees_with_csv')
        copy = dict(checkin)
        # Distinct internal sentinels prevent collapsing SQL NULL/unknown into blank.
        # The comparator conservatively requires review for other types at the same time.
        if state in ('null', 'unknown'):
            copy['log_type'] = '__' + state + '__'
            try:
                uncertain_storage.add((checkin['employee'], local_time(checkin['time'])))
            except ValueError:
                pass  # The shared comparator marks this employee's timestamps uncertain.
        comparison_checkins.append(copy)

    blockers = []
    claims = review.get('verification', {})
    require(isinstance(claims, dict), 'verification_schema_invalid')
    for claim in CLAIMS:
        entry = claims.get(claim, {})
        if not (isinstance(entry, dict) and entry.get('verified') is True
                and isinstance(entry.get('evidence_reference'), str) and entry['evidence_reference'].strip()):
            blockers.append('unverified_' + claim)
    snapshot = review.get('snapshot', {})
    require(isinstance(snapshot, dict), 'snapshot_schema_invalid')
    if not isinstance(snapshot.get('id'), str) or not snapshot['id'].strip():
        blockers.append('snapshot_identity_missing')
    if storage.get('snapshot_id') != snapshot.get('id'):
        blockers.append('log_type_storage_snapshot_mismatch')
    counts = snapshot.get('independent_counts', {})
    if not (isinstance(counts, dict)
            and type(counts.get('checkins')) is int and counts['checkins'] == len(checkins)
            and type(counts.get('lookup_keys')) is int and counts['lookup_keys'] == len(mappings)
            and type(counts.get('mapping_matches')) is int
            and counts['mapping_matches'] == sum(len(matches) for matches in mappings.values())):
        blockers.append('independent_counts_mismatch')
    try:
        began, finished = aware(snapshot['started_at']), aware(snapshot['completed_at'])
        exported = aware(contract['exported_at'])
        observed = aware(review['verified_at'])
        if not began <= finished <= exported <= observed <= now:
            blockers.append('snapshot_or_review_clock_order_invalid')
        if not dt.timedelta(0) <= now - began <= MAX_SNAPSHOT_AGE:
            blockers.append('destination_snapshot_not_fresh')
        if any(aware(c['capture_finished_at']) > began for c, _ in captures):
            blockers.append('destination_snapshot_predates_capture')
    except (KeyError, TypeError, ValueError):
        blockers.append('snapshot_or_review_timestamp_missing_or_invalid')

    rows, comparison = reconcile(captures, contract, mappings, comparison_checkins, start, end, zone)
    by_anchor = {anchor(row): row for row in rows}
    require(selected <= by_anchor.keys(), 'review_scope_not_in_verified_capture_range')
    # The baseline is only a selection/evidence reference, never authority for current mapping.
    # Require its source identity columns to agree with today's validated capture evidence.
    for key in selected:
        for field in ('user_id', 'source_timestamp', 'device_uid'):
            require(baseline[key][field] == csv_cell(by_anchor[key][field]), 'baseline_source_identity_disagrees')
    for row in rows:
        row['in_review_scope'] = anchor(row) in selected
        category = row['classification']
        if row['reason'] == 'same_time_different_log_type' and (
                row['employee'], local_time(row['comparison_timestamp'])) in uncertain_storage:
            row['reason'] = 'same_time_sql_null_or_unknown_log_type_requires_review'
        if category == 'matched':
            status, reason = 'already_matched', row['reason']
        elif category == 'ambiguous':
            status, reason = 'ambiguous', row['reason']
        elif category != 'missing':
            status, reason = 'excluded', row['reason']
        elif not row['in_review_scope']:
            status, reason = 'excluded', 'not_in_approved_review_scope'
        elif local_time(row['source_timestamp']).microsecond:
            status, reason = 'ambiguous', 'fractional_source_timestamp_requires_separate_review'
        elif blockers:
            status, reason = 'blocked', 'global_verification_blockers'
        else:
            status, reason = 'eligible_for_review', 'missing_under_attested_policy_not_authorized_for_recovery'
        row.update(review_status=status, review_reason=reason)

    sources += export_sources + [
        dict(role='review_manifest', sha256=digest),
        dict(role='baseline_details', sha256=baseline_spec['sha256']),
        dict(role='log_type_storage', sha256=storage_hash),
    ]
    def counts(items):
        return dict(sorted(Counter(r['review_status'] for r in items).items()))
    per_device, per_day = defaultdict(list), defaultdict(list)
    for row in rows:
        per_device[row['device_id']].append(row)
        per_day[row['day']].append(row)
    # Includes all rows and input hashes; the identifier changes when any reviewed input changes.
    batch_hash = fingerprint(dict(inputs=sources, range=[review['range_start'], review['range_end']], rows=rows))
    summary = dict(schema='attendance-recovery-preview-v1', mode='offline_dry_run',
        execution_supported=False, recovery_authorized=False, approval_required=True,
        provisional=True, review_fingerprint=batch_hash, created_at=now.isoformat(),
        maximum_snapshot_age_seconds=int(MAX_SNAPSHOT_AGE.total_seconds()),
        matching_policy=dict(employee_field='attendance_device_id', duplicate_key='employee_time_log_type',
                             timestamp_precision='seconds', source_client_log_type=None,
                             expected_storage_log_type='', sql_null_equals_empty_string=False),
        blockers=sorted(set(blockers)), inputs=sources, counts=counts(rows),
        scoped_counts=counts(r for r in rows if r['in_review_scope']),
        per_device={k: counts(v) for k, v in sorted(per_device.items())},
        per_day={k: counts(v) for k, v in sorted(per_day.items())},
        comparison=comparison)
    return rows, summary, paths + export_paths + [review_path, baseline_path, storage_path]


def write_preview(stream, rows):
    fields = DETAIL_FIELDS + EXTRA_FIELDS
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows({field: csv_cell(row[field]) for field in fields} for row in rows)
