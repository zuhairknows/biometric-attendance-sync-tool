"""Conservative comparison using Phase 1 source identities and reviewed server rules."""
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json

from scripts.attendance_reconciliation import ReconciliationError, local_time, site_zone, uncertain_wall_time
from .client import VerificationError
from .source import verified_source


CLAIMS = ('unfiltered_employee_and_checkin_visibility', 'server_duplicate_semantics',
          'source_and_site_timezone_match', 'historical_payload_policy',
          'destination_quiescent_during_scan', 'no_identity_mutating_hooks',
          'employee_lookup_database_equality')
CATEGORIES = ('already_existing', 'potentially_missing', 'unmatched_employee',
              'duplicate_source', 'invalid', 'ambiguous', 'unable_to_verify')
LIMITATIONS = [
    'Only punches present in this ledger are investigated; device history is not fetched.',
    'Unknown source timestamps are included for review regardless of the requested range.',
    'REST scans are not a transactional snapshot; repeat scans detect only observable drift.',
    'Visibility, deployed rules, historical payload policy and quiescence are reviewed assertions.',
    'Current employee status/mapping does not prove historical employment or assignment.',
    'Results are point-in-time evidence, never recovery authorization; historical holds remain unchanged.',
    'All selected evidence is held in memory; split very large investigations into smaller ranges.',
]


def contract_blockers(contract, start, end, devices, url):
    try:
        valid = (contract['schema'] == 'attendance-investigation-contract-v1'
                 and contract['site_url'] == url
                 and contract['timestamp_precision'] in ('seconds', 'microseconds')
                 and contract['duplicate_key'] in ('employee_time', 'employee_time_log_type')
                 and local_time(contract['coverage_start']) <= start <= end <= local_time(contract['coverage_end'])
                 and set(devices) <= set(contract['device_ids'])
                 and isinstance(contract['evidence_reference'], str) and bool(contract['evidence_reference'].strip())
                 and all(contract['verification'].get(c) is True for c in CLAIMS))
        site_zone(contract['site_timezone'])
        return [] if valid else ['comparison_contract_unverified']
    except (AttributeError, KeyError, TypeError, ValueError, ReconciliationError):
        return ['comparison_contract_unverified']


def investigate(repository, client, start, end, devices, contract, cancelled=lambda: False):
    if (not isinstance(start, dt.datetime) or not isinstance(end, dt.datetime)
            or start.tzinfo is not None or end.tzinfo is not None or start > end
            or not devices or len(set(devices)) != len(devices)
            or any(not isinstance(d, str) or not d.strip() for d in devices)):
        raise VerificationError('invalid_range_or_devices')
    began = dt.datetime.now(dt.timezone.utc).isoformat()
    source = repository.read(start, end, devices, cancelled)
    blockers = contract_blockers(contract, start, end, devices, client.url)
    rows = []
    employees, checkins = {}, []
    if not blockers and source:
        try:
            users = set()
            for row in source:
                try:
                    verified_source(row)
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
                users.add(row['user_id'])
            lower = start.replace(microsecond=0) if contract['timestamp_precision'] == 'seconds' else start
            employees, checkins = client.snapshot(lower, end, users)
        except VerificationError as exc:
            blockers.append(str(exc))
    if cancelled():
        raise KeyboardInterrupt
    mapping, index, same_time = defaultdict(list), defaultdict(list), defaultdict(list)
    zone = site_zone(contract['site_timezone']) if not blockers else None
    precision = contract.get('timestamp_precision')
    with_type = contract.get('duplicate_key') == 'employee_time_log_type'
    uncertain = set()
    if not blockers:
        try:
            if source and set(employees) != users:
                raise ValueError
            for user, matches in employees.items():
                if not isinstance(matches, list):
                    raise ValueError
                for employee in matches:
                    if (not isinstance(employee['attendance_device_id'], str)
                            or not isinstance(employee['status'], str)):
                        raise ValueError
                # Keep the server's full result, even when stored IDs differ from user.
                mapping[user] = matches
            for checkin in checkins:
                employee = checkin['employee']
                if not isinstance(employee, str) or not employee:
                    raise ValueError
                try:
                    stamp = local_time(checkin['time'])
                    if (uncertain_wall_time(stamp, zone) or (precision == 'seconds' and stamp.microsecond)
                            or not lower <= stamp <= end or checkin['log_type'] not in ('', 'IN', 'OUT', None)):
                        raise ValueError
                except (ValueError, TypeError):
                    uncertain.add(employee)
                    continue
                base = (employee, stamp)
                same_time[base].append(checkin)
                # Preserve SQL NULL separately from empty string, as in recovery preview.
                index[base + ((checkin['log_type'],) if with_type else ())].append(checkin)
        except (KeyError, TypeError, ValueError):
            blockers.append('destination_data_unverified')
    groups = defaultdict(list)
    for source_row in source:
        if cancelled():
            raise KeyboardInterrupt
        row = dict(source_row, classification='unable_to_verify', reason='comparison_unverified',
                   employee='', comparison_timestamp='', checkin_names='', duplicate_group_size=1,
                   eligible_for_recovery=False, historical_hold=source_row['error_category'] == 'cutover_hold')
        rows.append(row)
        try:
            stamp, log_type = verified_source(row)
        except (KeyError, ValueError, TypeError, OverflowError):
            row.update(classification='invalid', reason='source_evidence_invalid_or_disagrees')
            continue
        if blockers:
            continue
        if uncertain_wall_time(stamp, zone) or (precision == 'seconds' and stamp.microsecond):
            row.update(classification='ambiguous', reason='unsupported_or_ambiguous_source_precision')
            continue
        matches = mapping.get(row['user_id'], [])
        if not matches:
            row.update(classification='unmatched_employee', reason='no_employee_mapping')
            continue
        if len(matches) != 1 or matches[0]['status'] != 'Active':
            row.update(classification='ambiguous', reason='multiple_or_inactive_employee_mapping')
            continue
        employee = matches[0]['name']
        row.update(employee=employee, comparison_timestamp=str(stamp))
        key = (employee, stamp) + ((log_type,) if with_type else ())
        groups[key].append(row)
        found = index[key]
        row['checkin_names'] = ';'.join(c['name'] for c in found)
        if employee in uncertain:
            row.update(reason='destination_timestamp_or_storage_uncertain')
        elif len(found) > 1 or (not found and same_time[(employee, stamp)]):
            row.update(classification='ambiguous', reason='destination_identity_conflict')
        elif found:
            row.update(classification='already_existing', reason='destination_identity_exists')
        elif row['state'] in ('synced', 'duplicate', 'delivering') or row['checkin_name']:
            row.update(reason='ledger_delivery_evidence_conflicts_with_absence')
        else:
            row.update(classification='potentially_missing', reason='absent_under_verified_comparison')
    for group in groups.values():
        if len(group) > 1:
            for row in group:
                row['duplicate_group_size'] = len(group)
                if row['classification'] in ('already_existing', 'potentially_missing'):
                    row.update(classification='duplicate_source', reason='shared_destination_identity')
    def counts(items):
        values = Counter(r['classification'] for r in items)
        return dict(records=sum(values.values()), **{c: values[c] for c in CATEGORIES})
    summary = dict(schema='attendance-investigation-v1', status='unverified' if blockers else 'complete',
        started_at=began, completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        range_start=str(start), range_end=str(end), range_inclusive=True, device_ids=devices,
        recovery_authorized=False, execution_supported=False, blockers=blockers,
        matching_policy={k: contract.get(k) for k in ('site_timezone', 'timestamp_precision', 'duplicate_key')},
        source_snapshot_sha256=hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
        destination_observation_sha256=hashlib.sha256(json.dumps(
            dict(employees=employees, checkins=checkins), sort_keys=True).encode()).hexdigest(),
        destination_observed_counts=dict(employee_lookups=len(employees),
            employee_matches=sum(len(matches) for matches in employees.values()), checkins=len(checkins)),
        contract_sha256=hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest(),
        totals=counts(rows), per_device={d: counts(r for r in rows if r['device_id'] == d) for d in devices},
        historical_holds=sum(r['historical_hold'] for r in rows), limitations=LIMITATIONS)
    return rows, summary
