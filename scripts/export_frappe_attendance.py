"""Prepare reconciliation exports. Default is an offline plan; live export needs approval."""
import argparse
import contextlib
import csv
import datetime as dt
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.attendance_reconciliation import (ReconciliationError, load_captures, load_exports,
    local_time, require, source_reason, site_zone)
from scripts.capture_history_safety import preflight_output, check_directory_acl
from scripts.reconcile_attendance import publish
from scripts.frappe_export_review import AUTHORITATIVE_URL, literal_policy, load_review, read_json, fingerprint
from scripts.frappe_export_database import Snapshot, connect


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ReconciliationError('invalid_export_arguments_use_help')


def selected_users(captures, start, end, timezone):
    """Select exact keys; uncertain records never silently become recoverable candidates."""
    users, uncertain = set(), 0
    zone = site_zone(timezone)
    for capture, _ in captures:
        for row in capture['records']:
            try:
                stamp = local_time(row['normalized'].get('timestamp'))
            except (ValueError, TypeError):
                stamp = None
            reason = source_reason(row, zone)
            if reason:
                uncertain += 1
            if reason is None and stamp is not None and not start <= stamp <= end:
                continue
            # Include exact usable identifiers even for uncertain/out-of-range evidence retained
            # by reconciliation. Missing or malformed IDs stay ambiguous, not guessed mappings.
            user = row['normalized'].get('user_id')
            if isinstance(user, str) and user.strip() and len(user) <= 140:
                users.add(user)
    return sorted(users), uncertain


def database_profile(path):
    profile = read_json(path)
    require(set(profile) == {'host', 'port', 'database', 'user', 'tls_ca'}, 'database_profile_keys_invalid')
    require(isinstance(profile['host'], str) and re.fullmatch(r'[A-Za-z0-9._-]+', profile['host'])
            and type(profile['port']) is int and 1 <= profile['port'] <= 65535
            and isinstance(profile['database'], str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', profile['database'])
            and isinstance(profile['user'], str) and bool(profile['user']), 'database_profile_invalid')
    require(isinstance(profile['tls_ca'], str) and Path(profile['tls_ca']).is_absolute()
            and Path(profile['tls_ca']).is_file() and Path(profile['tls_ca']).stat().st_size <= 1024 * 1024, 'database_tls_ca_required')
    return profile


def csv_export(stream, rows):
    # This is machine input, not a spreadsheet report: preserve IDs exactly, never add apostrophes.
    writer = csv.DictWriter(stream, fieldnames=['name', 'employee', 'time', 'device_id', 'log_type'])
    writer.writeheader()
    writer.writerows(rows)


def main(argv=None, *, connector=connect, password_reader=getpass.getpass, acl_check=check_directory_acl):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--capture-summary', required=True)
    parser.add_argument('--capture-summary-sha256', required=True)
    parser.add_argument('--integration-config', required=True, help='Read literal site/direction settings only; never import')
    parser.add_argument('--verification', required=True, help='Administrator-approved review JSON and source bundle')
    parser.add_argument('--verification-sha256', required=True, help='Independent trusted approval hash')
    parser.add_argument('--output', required=True, help='Existing empty protected directory outside inputs/checkout')
    parser.add_argument('--db-profile', help='Non-secret connection/TLS profile; password is prompted')
    parser.add_argument('--start', type=local_time, default=dt.datetime(2026, 10, 11))
    parser.add_argument('--end', type=local_time, default=dt.datetime(2026, 10, 15, 23, 59, 59, 999999))
    parser.add_argument('--page-size', type=int, default=1000)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--plan', action='store_true', help='Default: validate local inputs, no DB imports/connections/writes')
    mode.add_argument('--live-export', action='store_true')
    parser.add_argument('--operator-authorized', action='store_true', help='Required for separately approved live read-only export')
    snapshot = connection = None
    password = None
    try:
        args = parser.parse_args(argv)
        require(args.start <= args.end and 1 <= args.page_size <= 10000, 'export_range_or_page_size_invalid')
        require(not args.live_export or args.operator_authorized, 'explicit_live_export_authorization_required')
        captures, sources, paths = load_captures(args.capture_summary, args.capture_summary_sha256)
        policy = literal_policy(args.integration_config)
        review, semantics, hooks, review_sha, review_paths = load_review(args.verification, args.verification_sha256, policy)
        profile = None
        if args.db_profile:
            profile = database_profile(args.db_profile)
            require(fingerprint(profile) == review.get('database_profile_sha256'), 'database_profile_not_approved')
            require(hashlib.sha256(Path(profile['tls_ca']).read_bytes()).hexdigest() == review.get('tls_ca_sha256'), 'database_tls_ca_not_approved')
        users, uncertain = selected_users(captures, args.start, args.end, review['site_timezone'])
        directory = preflight_output(args.output, live=False, acl_check=acl_check)
        paths += review_paths + [Path(args.integration_config).resolve()]
        if args.db_profile:
            paths.append(Path(args.db_profile).resolve())
        for path in paths:
            require(directory != path.parent and path.parent not in directory.parents, 'export_output_must_be_separate')
        require(not any(directory.iterdir()), 'export_output_must_be_empty')
        if not args.live_export:
            print(json.dumps(dict(mode='offline_plan', authoritative_url=AUTHORITATIVE_URL,
                selected_user_count=len(users), uncertain_source_records=uncertain,
                device_count=len(captures), semantics=semantics, verification_sha256=review_sha,
                database_contacted=False, files_written=False, certification_issued=False), indent=2))
            return 0
        require(args.db_profile is not None, 'database_profile_required')
        # Resolve warnings before opening a connection: getpass must never fall back to echo.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = password_reader('Read-only database password (never stored): ')
        require(isinstance(password, str) and bool(password), 'database_password_required')
        # Third-party driver errors/output must not echo parameters or database row values.
        with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            connection = connector(profile, password)
            password = None
            snapshot = Snapshot(connection, profile['database'], review, semantics, hooks, args.page_size)
            snapshot.begin()
            require(all(dt.datetime.fromisoformat(c['capture_finished_at']) <= snapshot.timestamp for c, _ in captures), 'snapshot_predates_capture')
            mappings, total_matches = snapshot.mappings(users)
            coverage_start = args.start.replace(microsecond=0) if semantics['timestamp_precision'] == 'seconds' else args.start
            checkins = snapshot.checkins(coverage_start, args.end)
            completeness = snapshot.completeness
            export_timestamp = snapshot.timestamp.isoformat()
            snapshot_upper_bound = snapshot.started_not_after.isoformat()
            column_evidence = snapshot.column_evidence
            snapshot.close()
            snapshot = connection = None
        require(dt.datetime.now(dt.timezone.utc) <= dt.datetime.fromisoformat(review['expires_at']), 'review_expired_during_export')
        # All verification flags below follow completed checks (plus hash-pinned administrator
        # provenance review). No certificate is emitted when any check/cleanup fails.
        mapping_spec = publish(directory, 'employee-mappings.json', lambda s: json.dump(
            dict(schema='frappe-employee-lookups-v1', lookups=mappings), s, indent=2, allow_nan=False))
        mapping_spec['rows'] = len(mappings)
        checkin_spec = publish(directory, 'employee-checkins.csv', lambda s: csv_export(s, checkins))
        checkin_spec['rows'] = len(checkins)
        manifest = dict(schema='frappe-offline-reconciliation-v1', authoritative_url=AUTHORITATIVE_URL,
            exported_at=export_timestamp,
            site_timezone=review['site_timezone'], coverage_start=str(coverage_start), coverage_end=str(args.end),
            exports_complete=True, consistent_snapshot=True, checkins_all_devices=True, checkins_all_employees=True,
            mapping_all_statuses=True, server_semantics_verified=True, no_identity_mutating_hooks=True,
            server_source_reference='sha256:review:' + review_sha, employee_field=semantics['employee_field'],
            mapping_resolution='frappe.db.get_values', duplicate_key=semantics['duplicate_key'],
            timestamp_precision=semantics['timestamp_precision'], device_policies=policy['device_policies'],
            employee_mappings=mapping_spec, employee_checkins=checkin_spec,
            completeness=dict(completeness, employee_lookup_count=len(mappings), employee_match_count=total_matches,
                read_only_transaction=True, isolation='REPEATABLE READ', rollback_confirmed=True,
                select_only_grants_verified=True, base_tables_innodb_verified=True,
                snapshot_started_not_before=export_timestamp, snapshot_started_not_after=snapshot_upper_bound,
                lookup_equivalence_basis='reviewed database column equality, no ORM permission filtering'),
            capture_inputs=sources, verification_sha256=review_sha)
        # Snapshot metadata must survive closing the connection without any further query.
        manifest['completeness']['column_metadata_sha256'] = column_evidence
        candidate = publish(directory, '.export-validation-' + uuid.uuid4().hex + '.part', lambda s: json.dump(manifest, s, indent=2))
        candidate_path = directory / candidate['file']
        load_exports(candidate_path, candidate['sha256'], args.start, args.end, review['site_timezone'])
        candidate_path.unlink()
        require(dt.datetime.now(dt.timezone.utc) <= dt.datetime.fromisoformat(review['expires_at']), 'review_expired_before_certification')
        published = publish(directory, 'export-manifest.json', lambda s: json.dump(manifest, s, indent=2, allow_nan=False))
        print(json.dumps(dict(status='complete', certification_issued=True, selected_user_count=len(mappings),
            employee_match_count=total_matches, checkin_count=len(checkins),
            manifest_sha256=published['sha256'], employee_mappings_sha256=mapping_spec['sha256'],
            employee_checkins_sha256=checkin_spec['sha256']), indent=2))
        return 0
    except ReconciliationError as exc:
        print(json.dumps(dict(status='failed', error_category=str(exc), certification_issued=False)))
        return 1
    except KeyboardInterrupt:
        print(json.dumps(dict(status='failed', error_category='export_interrupted', certification_issued=False)))
        return 130
    except Exception:
        print(json.dumps(dict(status='failed', error_category='export_validation_connection_or_persistence_failed', certification_issued=False)))
        return 1
    finally:
        password = None
        target = snapshot if snapshot is not None else connection
        if target is not None:
            # A failed export must still release its transaction. Errors here never become success.
            with contextlib.suppress(Exception), open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                target.close()


if __name__ == '__main__':
    raise SystemExit(main())
