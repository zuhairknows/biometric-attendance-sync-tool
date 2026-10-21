"""Private, allowlisted management diagnostics. Never serialize exceptions/inputs."""
import csv
import json
from pathlib import Path
import sqlite3
import uuid
from contextlib import contextmanager

from scripts.capture_history_safety import preflight_output

STAGES = {
    'monitor_path': 'Check the configured absolute ledger path and the manager account access. No database is created.',
    'monitor_open_schema': 'Check database/sidecar access and the deployed ledger schema version. Do not migrate, repair or change journal mode.',
    'monitor_snapshot': 'The read-only transaction could not start. Retry manually after startup; do not stop synchronization.',
    'monitor_integrity': 'Full integrity and foreign-key checks must pass; quick_check alone does not establish this.',
    'monitor_counts': 'The verified ledger diagnostics could not be read. Preserve the source and review the diagnostic code.',
    'monitor_display': 'Stored diagnostics could not be rendered. No partial healthy result was accepted; do not edit ledger values to bypass this check.',
    'recovery_evidence': 'Review the independently approved Phase 2A summary/hash, contract, snapshot and selected scope. Local preview, offline and compatibility-only reports cannot authorize recovery.',
    'recovery_handoff': 'Check private inbox access and the exact request fingerprint. Approval and dispatch are separate; held records need their own selected authorization.',
    'recovery_status': 'Check read-only access to the canonical service ledger and unchanged handoff. No service restart or queue reset is required.',
    'diagnostics': 'Check the approved diagnostics directory, private ACLs and write access. No console or temporary-directory fallback is used.',
    'request': 'Check the selected files, devices, date range and acknowledgement. Refresh the form if its session expired.',
    'profile': 'Check offline_directory, offline_evidence, offline_evidence_hash and output in the private startup profile.',
    'import_directory': 'Check the existing private local import directory and its ACLs.',
    'evidence_path': 'Check the approved evidence file exists inside the import directory and passes private file ACL checks.',
    'source_path': 'Check the selected snapshot exists inside the import directory and passes private file ACL checks.',
    'output': 'Check the report directory exists, is private and writable, has free space and is separate from inputs and service state.',
    'worker': 'Check the isolated worker could start and complete within 120 seconds. Do not retry automatically or increase limits.',
    'evidence_hash': 'Check the evidence JSON and its independently approved SHA256. Do not approve unknown claims.',
    'source_hash': 'Check the selected snapshot matches the approved evidence and has not changed.',
    'source_format': 'Use a self-contained rollback-journal SQLite backup without WAL, SHM or journal sidecars.',
    'source_read': 'Check backup integrity and ledger schema compatibility using approved offline diagnostics.',
    'export_times': 'Missing export creation times remain unknown and force Unverified; malformed or future times are invalid.',
    'export_evidence': 'Review independent counts, origins, coverage, filters, timestamp policy and export consistency evidence.',
    'employees_csv': 'Check the employee CSV file ACLs, SHA256, headers, rows and independent count.',
    'checkins_csv': 'Check the Checkin CSV file ACLs, SHA256, headers, rows and independent count.',
    'comparison': 'Review comparison claims and authoritative employee equality evidence. Uncertain absence cannot establish missing punches.',
    'input_recheck': 'Inputs must still match their approved hashes. Treat changed inputs as incomplete evidence.',
    'publication': 'Check protected report write access and free space. Partial files are not completed evidence.',
    'report_read': 'Check the protected completion summary can be read and verified.',
    'export_verify': 'Check protected report hashes and original input bindings. Do not use changed artifacts.',
}
# Exact codes only. Unknown exception text (even text resembling a code) is discarded.
CODES = set('''ok result_complete result_unverified request_rejected validation_failed unexpected_error
permission_denied file_not_found invalid_json invalid_csv invalid_value missing_setting sqlite_error
diagnostics_unavailable diagnostic_directory_separation completed_report_unverified invalid_range_or_devices
absolute_approved_path_required unexpected_form_fields duplicate_form_fields
input_path_must_be_absolute output_must_be_separate_from_inputs
output_acl_not_private output_acl_check_unavailable output_must_be_existing_absolute_directory
output_must_be_dedicated_outside_checkout output_must_be_local output_contains_production_state_or_logs
output_not_writable insufficient_disk_space invalid_sha256 sha256_mismatch duplicate_json_key
input_size_or_type_invalid json_document_must_be_object nonfinite_json_number
manifest_filename_invalid manifest_path_escape self_contained_rollback_journal_backup_required
investigation_cancelled investigation_deadline_exceeded investigation_worker_failed
offline_import_directory_invalid offline_import_directory_too_large offline_input_path_invalid
offline_evidence_size_limit offline_source_type_invalid offline_exports_must_be_distinct
offline_source_changed_or_unapproved offline_evidence_invalid offline_export_evidence_unverified
offline_export_selection_unapproved offline_export_hash_mismatch offline_csv_size_limit
offline_csv_headers_invalid offline_csv_row_malformed offline_csv_duplicate_or_empty_id offline_csv_row_limit
offline_export_count_or_origin_unverified offline_employee_coverage_unverified offline_checkin_coverage_unverified
offline_null_blank_unverified offline_precision_unverified offline_employee_status_invalid
offline_checkin_timestamp_or_direction_unverified offline_employee_equality_unverified
offline_exports_unverified offline_export_invalid_or_unreadable offline_inputs_changed
offline_employees_creation_time_missing offline_checkins_creation_time_missing
offline_employees_creation_time_invalid offline_checkins_creation_time_invalid
comparison_contract_unverified destination_data_unverified'''.split())
LEDGER_ERRORS = {
    'Unsupported SQLite schema version; database left unchanged': 'source_schema_unsupported',
    'Database has no ledger schema': 'source_schema_missing',
    'Unrecognized SQLite database; refusing initialization': 'source_schema_invalid',
    'Corrupted or unrecognized ledger schema; database left unchanged': 'source_schema_invalid',
    'Corrupted ledger destination state; database left unchanged': 'source_destination_state_invalid',
    'SQLite integrity check failed': 'source_integrity_failed',
    'SQLite foreign key check failed': 'source_foreign_keys_failed',
}
CODES.update(LEDGER_ERRORS.values())
CODES.update('''sqlite_busy sqlite_locked sqlite_cantopen sqlite_readonly sqlite_ioerr
sqlite_corrupt sqlite_notadb diagnostic_value_invalid'''.split())
CODES.update('''recovery_preview_required recovery_evidence_required recovery_production_disabled
recovery_scope_changed recovery_handoff_changed canonical_source_changed canonical_ledger_binding_required
canonical_ledger_binding_changed selection_not_verified request_changed approval_changed request_expired
explicit_selected_hold_authorization_required selected_approval_invalid read_only_api_unverified
complete_reconciliation_required comparison_contract_unverified snapshot_must_be_separate_from_live_ledger
recovery_profile_incomplete recovery_request_not_found recovery_ledger_unavailable'''.split())
CODES.update('''recovery_release_path_invalid recovery_release_disabled recovery_release_binding_invalid
recovery_release_expired recovery_release_prerequisites_missing recovery_release_approval_missing
recovery_release_code_changed recovery_release_address_invalid'''.split())
EVENTS = {'check', 'passed', 'failed', 'unverified', 'complete'}
# Monitor-only failure metadata. Never accept arbitrary operation names or text.
MONITOR_OPEN_OPERATIONS = frozenset(('unknown', 'source_connect', 'source_busy_timeout',
    'source_foreign_keys', 'source_tables', 'source_schema_version', 'reference_connect',
    'reference_schema_build', 'reference_schema_read', 'source_schema_compare',
    'source_destination_state', 'reference_close', 'source_close_on_error'))
MONITOR_SOURCE_BINDING_FIELDS = frozenset(('argument_equals_profile', 'resolved_equals_profile',
    'resolved_equals_probe_target', 'uri_equals_probe', 'readonly_argument',
    'builtin_sqlite_connect', 'background_thread'))


def safe_code(error):
    # Match only exact known codes; never emit type names, traces or exception args.
    value = str(error)
    if value in CODES:
        return value
    if isinstance(error, RuntimeError) and value in LEDGER_ERRORS:
        return LEDGER_ERRORS[value]
    for kind, code in ((PermissionError, 'permission_denied'), (FileNotFoundError, 'file_not_found'),
                       (json.JSONDecodeError, 'invalid_json'), (csv.Error, 'invalid_csv'),
                       (sqlite3.Error, 'sqlite_error'), (KeyError, 'missing_setting'),
                       (ValueError, 'invalid_value')):
        if isinstance(error, kind):
            return code
    return 'unexpected_error'


class DiagnosticError(Exception):
    def __init__(self, stage, code, reference):
        self.stage = stage if isinstance(stage, str) and stage in STAGES else 'worker'
        self.code = code if isinstance(code, str) and code in CODES else 'unexpected_error'
        self.reference = reference if isinstance(reference, str) and len(reference) == 32 and all(c in '0123456789abcdef' for c in reference) else 'unavailable'
        super().__init__('offline_comparison_blocked')

    @property
    def message(self):
        message = f'Offline diagnostic {self.reference}: {self.stage} / {self.code}. {STAGES[self.stage]}'
        if self.code == 'output_acl_not_private' and self.stage in ('source_path', 'evidence_path', 'employees_csv', 'checkins_csv', 'input_recheck'):
            message += ' The existing ACL policy checks input files individually; inherited-only file ACLs do not pass. Do not change production ACLs.'
        return message


class Trace:
    def __init__(self, values, acl_check, *, kind='offline'):
        self.reference, self.stage = uuid.uuid4().hex, 'diagnostics'
        self.kind = kind
        try:
            self.directory = preflight_output(values.get('diagnostics_directory') or values.get('output', ''), acl_check=acl_check)
            # Diagnostics must not write into the approved import/source directory.
            if values.get('offline_directory'):
                imports = Path(values['offline_directory']).resolve()
                if self.directory == imports or imports in self.directory.parents:
                    raise ValueError('diagnostic_directory_separation')
            if kind not in ('offline', 'recovery', 'monitor'):
                raise ValueError
            self.path = self.directory / (kind + '-' + self.reference + '.jsonl')
            with self.path.open('x', encoding='utf-8'):
                pass
            self.emit('check', 'ok')
        except Exception as error:
            code = safe_code(error)
            raise DiagnosticError('diagnostics', code if code != 'unexpected_error' else 'diagnostics_unavailable', self.reference) from None

    def emit(self, event, code, *, monitor_operation=None, sqlite_code=None, monitor_source=None):
        try:
            if event not in EVENTS or code not in CODES or self.stage not in STAGES:
                raise ValueError
            record = dict(reference=self.reference, stage=self.stage, code=code, event=event)
            if monitor_operation is not None or sqlite_code is not None or monitor_source is not None:
                # Only this specific SQLite failure can extend the four-field schema.
                # Numeric codes preserve extended errors without serializing exceptions.
                if (self.kind != 'monitor' or self.stage != 'monitor_open_schema' or event != 'failed'
                        or monitor_operation not in MONITOR_OPEN_OPERATIONS
                        or type(sqlite_code) is not int or not 0 <= sqlite_code <= 2147483647):
                    raise ValueError
                record.update(operation=monitor_operation, sqlite_code=sqlite_code,
                    sqlite_primary=sqlite_code & 255)
                if monitor_source is not None:
                    # Tri-state comparisons only: never serialize a path/hash/environment.
                    if (monitor_operation != 'source_connect' or not isinstance(monitor_source, dict)
                            or set(monitor_source) != MONITOR_SOURCE_BINDING_FIELDS
                            or any(v is not None and type(v) is not bool for v in monitor_source.values())):
                        raise ValueError
                    record['source_binding'] = monitor_source
            if self.path.is_symlink() or self.path.resolve().parent != self.directory:
                raise ValueError
            # No paths, exception arguments, times or rows, including monitor details.
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record) + '\n')
                stream.flush()
        except Exception as error:
            code = safe_code(error)
            raise DiagnosticError('diagnostics', code if code != 'unexpected_error' else 'diagnostics_unavailable', self.reference) from None

    def enter(self, stage):
        self.stage = stage
        self.emit('check', 'ok')

    @contextmanager
    def guard(self, stage):
        self.enter(stage)
        try:
            yield
        except Exception as error:
            raise self.fail(error) from None

    def fail(self, error, event='failed'):
        if isinstance(error, DiagnosticError):
            raise error
        code = safe_code(error)
        self.emit(event, code)
        return DiagnosticError(self.stage, code, self.reference)

    def worker_failure(self, error):
        # The supervisor intentionally hides child exceptions. Recover only a
        # validated fixed diagnostic written by our offline task before it exited.
        if safe_code(error) == 'investigation_worker_failed':
            try:
                records = [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]
                for record in reversed(records):
                    if (set(record) == {'reference', 'stage', 'code', 'event'}
                            and record['reference'] == self.reference and record['event'] == 'failed'
                            and record['stage'] in STAGES and record['code'] in CODES):
                        return DiagnosticError(record['stage'], record['code'], self.reference)
            except Exception:
                pass
        return self.fail(error)
