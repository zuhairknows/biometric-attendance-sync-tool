"""Offline evidence validation and comparison. Standard library only; no live clients."""
from collections import Counter, defaultdict
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ReconciliationError(Exception):
    """Fixed diagnostic codes only: input values can contain employee information."""


DEVICES = ('FP2_DEVICE_08', 'FP2_DEVICE_11')
CATEGORIES = ('matched', 'missing', 'unmatched_employee', 'ambiguous', 'duplicate_source')
COUNT_KEYS = ('total_fetched', 'valid', 'invalid', 'raw_incomplete', 'validation_counts',
              'valid_in_requested_range', 'oldest_valid_punch', 'newest_valid_punch',
              'oldest_valid_in_range', 'newest_valid_in_range')
DETAIL_FIELDS = ('device_id', 'source_order_index', 'source_sha256', 'day', 'range_status',
                 'user_id', 'device_uid', 'source_timestamp', 'comparison_timestamp',
                 'employee', 'expected_log_type', 'classification', 'reason',
                 'destination_match', 'checkin_names', 'checkin_device_ids',
                 'duplicate_group_size', 'requires_manual_review')
LOCAL_TIME = re.compile(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\Z')


def require(condition, code):
    if not condition:
        raise ReconciliationError(code)


def local_time(value):
    # No float epochs, timezone guessing, rounding, or silent whitespace trimming.
    if not isinstance(value, str) or not LOCAL_TIME.fullmatch(value):
        raise ValueError
    return dt.datetime.fromisoformat(value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def verified_json(path, expected_hash):
    require(isinstance(expected_hash, str) and re.fullmatch('[0-9a-fA-F]{64}', expected_hash), 'invalid_sha256')
    path = Path(path).resolve(strict=True)
    require(path.is_file() and path.stat().st_size <= 512 * 1024 * 1024, 'input_size_or_type_invalid')
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    require(digest == expected_hash.lower(), 'sha256_mismatch')
    value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique_object,
                       parse_constant=lambda _: require(False, 'nonfinite_json_number'))
    require(isinstance(value, dict), 'json_document_must_be_object')
    return value, digest, path


def child_file(parent, name):
    require(isinstance(name, str) and name not in ('', '.', '..') and '/' not in name and '\\' not in name
            and ':' not in name, 'manifest_filename_invalid')
    result = (parent / name).resolve(strict=True)
    require(result.parent == parent.resolve(), 'manifest_path_escape')
    return result


def capture_counts(capture):
    """Recompute published counts over the FULL snapshot, before date filtering."""
    rows = capture['records']
    counts = dict(total_fetched=len(rows), valid=0, invalid=0, raw_incomplete=0,
                  validation_counts=Counter(), valid_in_requested_range=0,
                  oldest_valid_punch=None, newest_valid_punch=None,
                  oldest_valid_in_range=None, newest_valid_in_range=None)
    requested = capture['requested_range']
    start = local_time(requested['start']) if requested['start'] is not None else None
    end = local_time(requested['end']) if requested['end'] is not None else None
    require(not (start and end and start > end), 'capture_range_invalid')
    for index, row in enumerate(rows):
        require(isinstance(row, dict) and type(row.get('source_order_index')) is int
                and row['source_order_index'] == index, 'source_order_invalid')
        require(type(row.get('valid')) is bool and type(row.get('raw_complete')) is bool
                and isinstance(row.get('normalized'), dict) and 'raw' in row, 'capture_record_schema_invalid')
        categories = row.get('validation_categories')
        require(isinstance(categories, list) and all(isinstance(c, str) for c in categories)
                and len(set(categories)) == len(categories) and row['valid'] == (not categories), 'capture_validation_inconsistent')
        counts['valid' if row['valid'] else 'invalid'] += 1
        counts['raw_incomplete'] += int(not row['raw_complete'])
        counts['validation_counts'].update(categories)
        if row['valid']:
            normalized = row['normalized']
            value = local_time(normalized['timestamp'])
            require(normalized['timestamp'] == value.isoformat(' ', timespec='microseconds'), 'capture_timestamp_format_invalid')
            require(isinstance(normalized.get('user_id'), str) and bool(normalized['user_id'].strip()), 'capture_user_id_invalid')
            for field in ('punch', 'status'):
                require(type(normalized.get(field)) is int and 0 <= normalized[field] <= 255, 'capture_code_invalid')
            require(normalized.get('uid') is None or (type(normalized['uid']) is int and normalized['uid'] >= 0), 'capture_uid_invalid')
            stamp = normalized['timestamp']
            counts['oldest_valid_punch'] = min(counts['oldest_valid_punch'] or stamp, stamp)
            counts['newest_valid_punch'] = max(counts['newest_valid_punch'] or stamp, stamp)
            if (start is None or value >= start) and (end is None or value <= end):
                counts['valid_in_requested_range'] += 1
                counts['oldest_valid_in_range'] = min(counts['oldest_valid_in_range'] or stamp, stamp)
                counts['newest_valid_in_range'] = max(counts['newest_valid_in_range'] or stamp, stamp)
    return counts


def load_captures(summary_path, summary_hash):
    manifest, digest, path = verified_json(summary_path, summary_hash)
    require(manifest.get('mode') == 'capture' and manifest.get('status') == 'complete'
            and manifest.get('all_selected_devices_captured') is True
            and manifest.get('date_filter_affects_summary_only') is True, 'capture_manifest_not_complete')
    reports = manifest.get('devices')
    require(isinstance(reports, list) and len(reports) == 2, 'capture_device_set_invalid')
    captures, sources = [], [{'role': 'capture_summary', 'sha256': digest}]
    seen = set()
    for report in reports:
        device = report['device_id']
        require(device in DEVICES and device not in seen and report.get('status') == 'captured', 'capture_device_set_invalid')
        seen.add(device)
        source_path = child_file(path.parent, report['evidence_file'])
        capture, sha, source_path = verified_json(source_path, report['sha256'])
        require(capture.get('schema') == 'zkteco-offline-capture-v1' and isinstance(capture.get('records'), list), 'capture_schema_invalid')
        for key in ('device_id', 'host', 'port', 'capture_started_at'):
            require(capture.get(key) == report.get(key), 'capture_metadata_mismatch')
        require(capture.get('device_cleanup') == report.get('cleanup') and isinstance(report.get('cleanup'), dict)
                and report['cleanup'].get('disconnect') == 'confirmed'
                and report['cleanup'].get('reenable') in ('confirmed', 'not_requested'), 'capture_cleanup_unconfirmed')
        for key in ('capture_started_at', 'capture_finished_at'):
            require(dt.datetime.fromisoformat(capture[key]).tzinfo is not None, 'capture_timestamp_invalid')
        require(dt.datetime.fromisoformat(capture['capture_started_at']) <= dt.datetime.fromisoformat(capture['capture_finished_at'])
                <= dt.datetime.fromisoformat(report['capture_finished_at']), 'capture_timestamp_order_invalid')
        counts = capture_counts(capture)
        require(report.get('fetched_count_from_sdk') == len(capture['records']), 'capture_fetched_count_mismatch')
        for key in COUNT_KEYS:
            if key in ('total_fetched', 'valid', 'invalid', 'raw_incomplete', 'valid_in_requested_range'):
                require(type(capture['summary'].get(key)) is int and type(report.get(key)) is int, 'capture_count_type_invalid')
            require(capture['summary'].get(key) == counts[key] == report.get(key), 'capture_counts_mismatch')
        captures.append((capture, sha))
        sources.append({'role': device, 'sha256': sha})
    return captures, sources, [path, *(child_file(path.parent, r['evidence_file']) for r in reports)]


def load_exports(manifest_path, manifest_hash, start, end, site_timezone):
    manifest, digest, path = verified_json(manifest_path, manifest_hash)
    require(manifest.get('schema') == 'frappe-offline-reconciliation-v1', 'export_schema_invalid')
    require(manifest.get('site_timezone') == site_timezone, 'site_timezone_mismatch')
    coverage_start = start.replace(microsecond=0) if manifest.get('timestamp_precision') == 'seconds' else start
    require(local_time(manifest['coverage_start']) <= coverage_start and local_time(manifest['coverage_end']) >= end, 'export_coverage_insufficient')
    require(dt.datetime.fromisoformat(manifest['exported_at']).tzinfo is not None, 'export_timestamp_invalid')
    for field in ('checkins_all_devices', 'checkins_all_employees', 'mapping_all_statuses',
                  'exports_complete', 'consistent_snapshot', 'server_semantics_verified', 'no_identity_mutating_hooks'):
        require(manifest.get(field) is True, 'export_attestation_required')
    require(isinstance(manifest.get('server_source_reference'), str) and bool(manifest['server_source_reference'].strip()), 'server_source_reference_required')
    require(manifest.get('employee_field') == 'attendance_device_id'
            and manifest.get('mapping_resolution') == 'frappe.db.get_values', 'employee_mapping_semantics_unsupported')
    require(manifest.get('duplicate_key') in ('employee_time', 'employee_time_log_type')
            and manifest.get('timestamp_precision') in ('seconds', 'microseconds'), 'duplicate_semantics_unsupported')
    policies = manifest.get('device_policies')
    require(isinstance(policies, dict) and set(policies) == set(DEVICES), 'device_policies_invalid')
    for policy in policies.values():
        require(isinstance(policy, dict) and policy.get('punch_direction') in ('AUTO', 'IN', 'OUT', None), 'punch_direction_invalid')
        for field in ('in_codes', 'out_codes'):
            require(isinstance(policy.get(field), list) and all(type(c) is int and 0 <= c <= 255 for c in policy[field]), 'punch_codes_invalid')
    mapping_spec, checkin_spec = manifest['employee_mappings'], manifest['employee_checkins']
    mapping_path = child_file(path.parent, mapping_spec['file'])
    mapping, mapping_hash, _ = verified_json(mapping_path, mapping_spec['sha256'])
    require(mapping.get('schema') == 'frappe-employee-lookups-v1' and isinstance(mapping.get('lookups'), list), 'mapping_schema_invalid')
    require(type(mapping_spec.get('rows')) is int and mapping_spec['rows'] == len(mapping['lookups']), 'mapping_row_count_mismatch')
    lookups = {}
    for row in mapping['lookups']:
        user = row.get('user_id')
        require(isinstance(user, str) and bool(user.strip()) and user not in lookups and isinstance(row.get('matches'), list), 'mapping_lookup_invalid')
        employees = set()
        for match in row['matches']:
            require(isinstance(match.get('employee'), str) and bool(match['employee']) and match['employee'] not in employees
                    and isinstance(match.get('attendance_device_id'), str) and isinstance(match.get('status'), str), 'mapping_match_invalid')
            employees.add(match['employee'])
        lookups[user] = row['matches']
    checkin_path = child_file(path.parent, checkin_spec['file'])
    require(checkin_path.stat().st_size <= 512 * 1024 * 1024, 'input_size_or_type_invalid')
    data = checkin_path.read_bytes()
    checkin_hash = hashlib.sha256(data).hexdigest()
    require(checkin_hash == checkin_spec['sha256'], 'sha256_mismatch')
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
    require(reader.fieldnames == ['name', 'employee', 'time', 'device_id', 'log_type'], 'checkin_columns_invalid')
    checkins, names = [], set()
    for row in reader:
        require(None not in row and all(isinstance(v, str) for v in row.values())
                and bool(row['name']) and bool(row['employee']) and row['name'] not in names
                and row['log_type'] in ('', 'IN', 'OUT'), 'checkin_row_invalid')
        names.add(row['name'])
        checkins.append(row)
    require(type(checkin_spec.get('rows')) is int and checkin_spec['rows'] == len(checkins), 'checkin_row_count_mismatch')
    sources = [{'role': 'export_manifest', 'sha256': digest}, {'role': 'employee_mappings', 'sha256': mapping_hash},
               {'role': 'employee_checkins', 'sha256': checkin_hash}]
    return manifest, lookups, checkins, sources, [path, mapping_path, checkin_path]


def site_zone(name):
    # Windows may not have tzdata installed. Riyadh has no clock transitions in this scope.
    if name == 'Asia/Riyadh':
        return dt.timezone(dt.timedelta(hours=3))
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ReconciliationError('site_timezone_unavailable') from None


def uncertain_wall_time(value, zone):
    # Differing fold offsets cover both repeated and nonexistent local times.
    return value.replace(tzinfo=zone, fold=0).utcoffset() != value.replace(tzinfo=zone, fold=1).utcoffset()


def source_reason(record, zone):
    if not record['valid'] or not record['raw_complete']:
        return 'source_invalid_or_incomplete'
    raw, normalized = record['raw'], record['normalized']
    if not isinstance(raw, dict):
        return 'source_raw_shape_uncertain'
    user = raw.get('user_id')
    if type(user) not in (str, int) or str(user) != normalized['user_id']:
        return 'source_normalization_disagrees'
    stamp = raw.get('timestamp')
    if isinstance(stamp, dict) and stamp.get('__type__') == 'datetime':
        if stamp.get('fold') != 0:
            return 'source_timestamp_fold_uncertain'
        stamp = stamp.get('iso')
    try:
        value = local_time(stamp)
        if value != local_time(normalized['timestamp']):
            return 'source_normalization_disagrees'
        if uncertain_wall_time(value, zone):
            return 'source_timestamp_clock_transition'
    except (ValueError, TypeError):
        return 'source_timestamp_representation_uncertain'
    for key in ('punch', 'status', 'uid'):
        if raw.get(key) != normalized.get(key):
            return 'source_normalization_disagrees'
    if len(normalized['user_id']) > 140:
        return 'source_user_id_oversized'
    return None


def direction(normalized, policy):
    value = policy['punch_direction']
    if value == 'AUTO':
        # Same OUT-before-IN precedence as erpnext_sync.py and punch_normalization.py.
        return 'OUT' if normalized['punch'] in policy['out_codes'] else 'IN' if normalized['punch'] in policy['in_codes'] else ''
    return value or ''


def reconcile(captures, contract, lookups, checkins, start, end, timezone):
    zone = site_zone(timezone)
    precision = contract['timestamp_precision']
    with_type = contract['duplicate_key'] == 'employee_time_log_type'
    index, same_time, uncertain_employees = defaultdict(list), defaultdict(list), set()
    for checkin in checkins:
        try:
            stamp = local_time(checkin['time'])
            if uncertain_wall_time(stamp, zone) or (precision == 'seconds' and stamp.microsecond):
                raise ValueError
        except ValueError:
            # An unorderable checkin could already represent any punch for this employee.
            uncertain_employees.add(checkin['employee'])
            continue
        compare_start = start.replace(microsecond=0) if precision == 'seconds' else start
        if not compare_start <= stamp <= end:
            continue
        base = (checkin['employee'], stamp)
        same_time[base].append(checkin)
        index[base + ((checkin['log_type'],) if with_type else ())].append(checkin)
    rows, excluded, full_count = [], Counter(), 0
    groups = defaultdict(list)
    for capture, sha in captures:
        device = capture['device_id']
        for record in capture['records']:
            full_count += 1
            normalized = record['normalized']
            try:
                stamp = local_time(normalized.get('timestamp'))
            except (ValueError, TypeError):
                stamp = None
            reason = source_reason(record, zone)
            # Never exclude uncertain evidence using an unverified normalized timestamp.
            if reason is None and stamp is not None and not start <= stamp <= end:
                excluded[device] += 1
                continue
            row = dict.fromkeys(DETAIL_FIELDS, '')
            row.update(device_id=device, source_order_index=record['source_order_index'], source_sha256=sha,
                       day=stamp.date().isoformat() if stamp else 'unknown', range_status='within' if stamp and start <= stamp <= end else 'uncertain',
                       user_id=normalized.get('user_id', ''), device_uid=normalized.get('uid'),
                       source_timestamp=normalized.get('timestamp', ''), classification='ambiguous',
                       reason=reason or '', destination_match='not_evaluated', duplicate_group_size=1,
                       requires_manual_review=True)
            rows.append(row)
            if reason:
                continue
            matches = lookups.get(normalized['user_id'])
            if matches is None:
                row['reason'] = 'employee_lookup_not_exported'
                continue
            if not matches:
                row.update(classification='unmatched_employee', reason='authoritative_lookup_returned_no_employee')
                continue
            if len(matches) != 1:
                row['reason'] = 'multiple_employee_mappings'
                continue
            employee = matches[0]['employee']
            row['employee'] = employee
            if matches[0]['status'] != 'Active':
                row['reason'] = 'employee_not_active'
                continue
            if employee in uncertain_employees:
                row['reason'] = 'existing_checkin_timestamp_uncertain'
                continue
            comparison = stamp.replace(microsecond=0) if precision == 'seconds' else stamp
            log_type = direction(normalized, contract['device_policies'][device])
            row.update(comparison_timestamp=comparison.isoformat(' ', timespec='microseconds'), expected_log_type=log_type)
            base = (employee, comparison)
            key = base + ((log_type,) if with_type else ())
            destination = index.get(key, [])
            row.update(checkin_names=';'.join(c['name'] for c in destination),
                       checkin_device_ids=';'.join(c['device_id'] for c in destination))
            if len(destination) > 1:
                row.update(reason='multiple_destination_checkins', destination_match='ambiguous')
            elif destination:
                row.update(classification='matched', reason='destination_identity_exists', destination_match='matched')
            elif same_time.get(base):
                # A different log type at this time needs review even if the verified server permits it.
                row.update(reason='same_time_different_log_type', destination_match='ambiguous',
                           checkin_names=';'.join(c['name'] for c in same_time[base]),
                           checkin_device_ids=';'.join(c['device_id'] for c in same_time[base]))
            else:
                row.update(classification='missing', reason='no_destination_identity_in_complete_export', destination_match='missing')
            groups[key].append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        # Mark EVERY member; selecting an arbitrary first row would imply a recovery decision.
        collision = len({r['source_timestamp'] for r in group}) > 1
        for row in group:
            row['duplicate_group_size'] = len(group)
            if row['classification'] == 'ambiguous' or collision:
                row.update(classification='ambiguous', reason='source_destination_identity_collision')
            else:
                row.update(classification='duplicate_source', reason='repeated_destination_identity_in_sources')
    def stats(counts):
        return dict(records=sum(counts.values()), **{c: counts[c] for c in CATEGORIES})
    totals, by_device, by_day = Counter(), defaultdict(Counter), defaultdict(Counter)
    for row in rows:
        category = row['classification']
        totals[category] += 1
        by_device[row['device_id']][category] += 1
        by_day[row['day']][category] += 1
    summary = dict(schema='attendance-reconciliation-report-v1', mode='offline_read_only',
                   range_start=start.isoformat(' '), range_end=end.isoformat(' '), site_timezone=timezone,
                   source_records=full_count, excluded_valid_out_of_range=sum(excluded.values()),
                   recovery_authorized=False, all_missing_require_review=True,
                   duplicate_key=contract['duplicate_key'], timestamp_precision=precision,
                   totals=stats(totals), per_device={}, per_day={})
    for device in DEVICES:
        summary['per_device'][device] = dict(stats(by_device[device]),
                                            excluded_valid_out_of_range=excluded[device])
    for day, counts in sorted(by_day.items()):
        summary['per_day'][day] = stats(counts)
    summary['uncertain_existing_checkin_employees'] = len(uncertain_employees)
    return rows, summary


def csv_cell(value):
    text = '' if value is None else str(value)
    # Prevent spreadsheet formula execution. Exact evidence stays in the hashed source JSON.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


def write_details(stream, rows):
    writer = csv.DictWriter(stream, fieldnames=DETAIL_FIELDS)
    writer.writeheader()
    writer.writerows({key: csv_cell(row[key]) for key in DETAIL_FIELDS} for row in rows)
