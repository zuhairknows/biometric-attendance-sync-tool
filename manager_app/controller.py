"""UI-independent adapters around existing diagnostics, Phase 2A CLI and Phase 2B APIs."""
import contextlib
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import threading

from sync_ledger import Ledger
from durable_startup import private_storage
from historical_attendance import recovery
from historical_attendance.bounded import run_bounded
from historical_attendance.recovery_store import RecoveryStore
from historical_attendance.recovery_simulation import SimulationTarget
from historical_attendance import recovery_preview
from scripts import investigate_attendance as investigation_cli
from scripts.attendance_reconciliation import child_file
from scripts.capture_history_safety import service_state, check_directory_acl


class ManagerError(Exception):
    """Only fixed, employee/credential-free diagnostics cross the UI boundary."""
    def __init__(self, code, diagnostic=None):
        super().__init__(code)
        self.diagnostic = diagnostic


# Only these fixed messages may cross the job boundary. Never display exception text
# or trust arbitrary CLI receipts, which can contain private input in integrations.
INVESTIGATION_FAILURES = {
    'offline_comparison_blocked': 'Offline comparison could not verify approved inputs, private file permissions or report output. Review the offline profile and evidence/hash; no accepted result is available.',
    'investigation_profile_incomplete': 'The private startup profile is missing investigation settings. Supply backup, contract, approved contract_hash, protected output and HTTPS site; select a start and end time.',
    'investigation_prerequisites_missing': 'Confirm the offline-backup acknowledgement, selected devices and API credentials; the manager must not be closing.',
    'absolute_approved_path_required': 'Approved input and output paths must be absolute and within the permitted location.',
    'investigation_contract_unverified': 'The contract could not be read or its approved SHA256 did not match. Review the private profile and independently approved contract.',
    'investigation_limits_invalid': 'Investigation request or time limits are invalid. Use the supported manager defaults.',
    'investigation_production_ack_required': 'Production compatibility requires the production acknowledgement and a contract declaring the production destination. Review both; do not change unknown verification claims.',
    'investigation_endpoint_safety_unverified': 'The production contract does not verify read-only API endpoint safety. Stop until independent endpoint review establishes safety; do not automatically approve this claim.',
    'investigation_source_unavailable': 'The approved offline SQLite backup could not be located or accessed. Check the private profile and backup access; do not use the live writable ledger.',
    'investigation_output_unverified': 'Protected report output validation failed. Check the existing local directory, private ACLs, write access, free space and separation from inputs, checkout and production state.',
    'investigation_execution_failed': 'The investigation could not complete execution or publish protected reports. Review the approved source snapshot and output prerequisites. No verified result is available.',
    'investigation_deadline_exceeded': 'The investigation exceeded its 120-second deadline. Treat any report artifacts as incomplete; do not raise limits or retry automatically.',
    'investigation_cancelled': 'The investigation was cancelled. Any report artifacts are incomplete evidence.',
    'investigation_incomplete': 'The investigation was interrupted. Any report artifacts are incomplete evidence.',
    'investigation_worker_failed': 'The isolated investigation worker failed. Source integrity/schema, execution or report publication could not be established; any report artifacts are incomplete.',
    'completed_report_unverified': 'The completion receipt and protected CSV/JSON reports could not be verified together. Do not use any artifacts as completed evidence.',
    'investigation_blocked_or_cancelled': 'The investigation failed without a recognized safe diagnostic. Review approved inputs and permissions; no verified result is available.',
}
INVESTIGATION_FAILURES.update(recovery_preview.MESSAGES)
INVESTIGATION_FAILURES['service_profile_incomplete'] = 'Recovery handoff blocked: supply the approved snapshot/hash, Phase 2A summary/hash, contract/hash, canonical recovery ledger and private inbox in the startup profile. No approval or dispatch was accepted.'
INVESTIGATION_FAILURES['service_diagnostics_unavailable'] = 'Recovery handoff blocked before private diagnostics could be created. Check the approved diagnostics/output directory, private ACLs and separation from all inputs. No public or temporary log fallback was used.'


def utc_time(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='seconds') if value else 'Not recorded'


def _offline_output(values, acl_check, diagnostic):
    from historical_attendance.offline import import_root, approved_file
    from historical_attendance.reporting import validate_output
    diagnostic.enter('import_directory')
    root = import_root(values['offline_directory'], acl_check)
    diagnostic.enter('source_path')
    source = approved_file(root, values['offline_snapshot'], acl_check)
    diagnostic.enter('evidence_path')
    evidence = approved_file(root, values['offline_evidence'], acl_check)
    diagnostic.enter('output')
    return validate_output(values['output'], ledger_path=source, input_paths=[evidence], acl_check=acl_check)


def _offline_task(**options):
    from historical_attendance.offline import compare
    from historical_attendance.diagnostics import DiagnosticError
    diagnostic = options['diagnostic']
    try:
        return [], compare(**options)
    except Exception as error:
        try:
            failure = error if isinstance(error, DiagnosticError) else diagnostic.fail(error)
        except DiagnosticError as log_error:
            failure = log_error
        # Only fixed diagnostics cross the child boundary, including when the
        # log itself becomes unavailable. This is never a successful report.
        return [], dict(diagnostic_failure=dict(stage=failure.stage, code=failure.code, reference=failure.reference))


def _preview_task(**options):
    try:
        return [], recovery_preview.preview(**options)
    except recovery_preview.PreviewError as error:
        return [], dict(preview_failure=str(error))


class Settings:
    # Only explicit app preferences. Never serialize widgets, credentials or report data.
    FIELDS = ('monitor_ledger', 'backup', 'contract', 'contract_hash', 'output', 'workspace',
              'simulation', 'normal_lock', 'start', 'end')

    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ['LOCALAPPDATA']) / 'MYFPF' / 'BiometricSyncManager' / 'settings.json'

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return {k: data[k] for k in self.FIELDS if isinstance(data.get(k), str)}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def save(self, values):
        data = {k: values[k] for k in self.FIELDS if isinstance(values.get(k), str)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.settings-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            Path(name).replace(self.path)
        finally:
            Path(name).unlink(missing_ok=True)


class Controller:
    def __init__(self, *, service_query=service_state, acl_check=check_directory_acl, cli=investigation_cli.main, sandbox_root=None,
                 storage_check=private_storage):
        self.service_query = service_query
        self.acl_check = acl_check
        self.cli = cli
        self.storage_check = storage_check
        self.cancelled = threading.Event()
        self.last_investigation = None
        self.sandbox_root = Path(sandbox_root).resolve() if sandbox_root else None

    def _paths(self, *values):
        for value in values:
            path = Path(value)
            if not path.is_absolute() or (self.sandbox_root and not path.resolve().is_relative_to(self.sandbox_root)):
                raise ManagerError('absolute_approved_path_required')

    def close(self):
        # Cancels only this app's bounded work. Never issues a service-control call.
        self.cancelled.set()

    def service_workflow(self, values):
        from .recovery_handoff import Workflow
        return Workflow(self, values)

    def preview_recovery(self, values, devices):
        try:
            self._paths(values['backup'], values['output'])
            source = recovery_preview.snapshot(values['backup'], values.get('backup_hash', ''), self.acl_check)
            directory = recovery_preview.output(values['output'], source, self.acl_check)
            options = dict(backup=str(source), backup_hash=values.get('backup_hash', ''),
                directory=str(directory), start=values['start'], end=values['end'],
                devices=devices, acl_check=self.acl_check)
            result = run_bounded(_preview_task, options, seconds=120, directory=directory,
                                 cancelled=self.cancelled.is_set)[1]
            if 'preview_failure' in result:
                raise recovery_preview.PreviewError(result['preview_failure'])
            return result
        except Exception as error:
            code = str(error)
            raise ManagerError(code if code in recovery_preview.MESSAGES else 'preview_failed') from None

    def dashboard(self, ledger_path):
        data = dict(service='Unavailable', integrity='Unavailable', total=0, pending=0, retry=0,
                    failed=0, holds=0, states={}, last_delivery='Not recorded', devices=[], errors=[],
                    refreshed=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
        try:
            data['service'] = self.service_query()
        except Exception:
            data['errors'].append('Service status unavailable; access or installation could not be verified.')
        try:
            self._paths(ledger_path)
            with Ledger(Path(ledger_path), readonly=True) as ledger:
                ledger.db.execute('PRAGMA query_only=ON')
                ledger.db.execute('BEGIN')
                ledger.verify_integrity()
                diagnostics = ledger.diagnostics()
                data.update(integrity='OK', total=diagnostics['total'], pending=diagnostics['states']['pending'],
                    retry=diagnostics['states']['retry'], failed=diagnostics['quarantined'],
                    holds=diagnostics['held_historical'], states=diagnostics['states'],
                    last_delivery=utc_time(diagnostics['last_successful_delivery']))
                failures = {r[0]: r[1] for r in ledger.db.execute("SELECT device_id,COUNT(*) FROM punches WHERE state IN ('rejected','unmatched_employee','invalid_record') GROUP BY device_id")}
                ids = {r[0] for r in ledger.db.execute('SELECT DISTINCT device_id FROM punches')}
                states = {r['device_id']: dict(r) for r in ledger.db.execute('SELECT * FROM device_state')}
                for device in sorted(ids | states.keys()):
                    state = states.get(device, {})
                    data['devices'].append(dict(device=device,
                        fetch='OK' if state.get('fetch_ok') else 'Failed / unknown',
                        last_fetch=state.get('last_successful_fetch') or 'Not recorded',
                        backlog=diagnostics['pending_per_device'].get(device, 0),
                        failed=failures.get(device, 0),
                        last_delivery=utc_time(state.get('last_successful_delivery'))))
        except Exception:
            data['errors'].append('Ledger unavailable or integrity/schema check failed. No state was changed.')
        return data

    def investigate(self, values, devices, key, secret, production_ack, backup_ack):
        self.last_investigation = None  # A failed run must not leave stale results exportable.
        if not backup_ack or not devices or not key or not secret or self.cancelled.is_set():
            raise ManagerError('investigation_prerequisites_missing')
        if any(not isinstance(values.get(name), str) or not values[name] for name in
               ('backup', 'contract', 'contract_hash', 'output', 'site', 'start', 'end')):
            raise ManagerError('investigation_profile_incomplete')
        self._paths(values['backup'], values['contract'], values['output'])
        directory = Path(values['output'])
        before = set(directory.glob('investigation_summary_*.json'))
        args = ['--ledger-copy', values['backup'], '--start', values['start'], '--end', values['end'],
            '--site-url', values['site'], '--contract', values['contract'], '--contract-sha256', values['contract_hash'],
            '--output', str(directory), '--max-get-requests', '32', '--deadline-seconds', '120', '--request-timeout-seconds', '10']
        for device in devices:
            args.extend(['--device', device])
        if production_ack:
            args.extend(['--production-read-only', '--acknowledge-production', values['site'].rstrip('/')])
        # Use the CLI's exact acknowledgement, contract, privacy and bounded-worker gates.
        # Environment credentials exist only for this explicitly initiated action.
        old = {k: os.environ.get(k) for k in ('INVESTIGATION_API_KEY', 'INVESTIGATION_API_SECRET')}
        try:
            os.environ.update(INVESTIGATION_API_KEY=key, INVESTIGATION_API_SECRET=secret)
            receipt = io.StringIO()
            runner = lambda task, options, **limits: run_bounded(task, options, **limits, cancelled=self.cancelled.is_set)
            with contextlib.redirect_stdout(receipt):
                code = self.cli(args, acl_check=self.acl_check, runner=runner)
        except Exception:
            raise ManagerError('investigation_blocked_or_cancelled') from None
        finally:
            for name, original in old.items():
                if original is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = original
        if self.cancelled.is_set():
            raise ManagerError('investigation_cancelled')
        if code not in (0, 2):
            # Decode only a known fixed category; never forward raw child output.
            try:
                category = json.loads(receipt.getvalue()).get('error_category')
                if isinstance(category, str) and category in INVESTIGATION_FAILURES:
                    raise ManagerError(category)
            except (ValueError, TypeError, AttributeError):
                pass
            raise ManagerError('investigation_blocked_or_cancelled')
        try:
            terminal = json.loads(receipt.getvalue())
            created = set(directory.glob('investigation_summary_*.json')) - before
            if len(created) != 1:
                raise ValueError
            summary_path = created.pop()
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            if terminal['status'] != summary['status'] or terminal['records'] != summary['totals']['records']:
                raise ValueError
            detail = child_file(summary_path.parent.resolve(), summary['reports'][0]['file'])
            if recovery.file_hash(detail) != summary['reports'][0]['sha256']:
                raise ValueError
            with detail.open(encoding='utf-8', newline='') as stream:
                rows = list(csv.DictReader(stream))
            if len(rows) != summary['totals']['records']:
                raise ValueError
        except Exception:
            raise ManagerError('completed_report_unverified') from None
        self.last_investigation = dict(summary=summary, rows=rows, json_path=str(summary_path), csv_path=str(detail))
        return self.last_investigation

    def batches(self, workspace):
        try:
            self._paths(workspace)
            store = RecoveryStore(workspace, acl_check=self.acl_check, readonly=True)
            try:
                rows = [dict(r) for r in store.db.execute('SELECT id,fingerprint,metadata FROM batches ORDER BY rowid DESC')]
                for row in rows:
                    row['created'] = json.loads(row['metadata'])['created']
                    if not isinstance(row['created'], str):
                        raise ValueError
                return rows
            finally:
                store.close()
        except Exception:
            raise ManagerError('existing_recovery_workspace_unavailable') from None

    def offline_diagnostics(self, values):
        from historical_attendance.diagnostics import Trace, DiagnosticError
        try:
            return Trace(values, self.acl_check)
        except DiagnosticError as error:
            raise ManagerError('offline_comparison_blocked', error) from None

    def compare_offline(self, values, snapshot, employees, checkins, devices, diagnostic=None):
        from historical_attendance.diagnostics import DiagnosticError
        diagnostic = diagnostic or self.offline_diagnostics(values)
        try:
            diagnostic.enter('profile')
            self._paths(values['offline_directory'], values['output'])
            if self.cancelled.is_set():
                raise investigation_cli.VerificationError('investigation_cancelled')
            options = dict(directory=values['offline_directory'],
                evidence_name=values['offline_evidence'], evidence_hash=values['offline_evidence_hash'],
                snapshot=snapshot, employees=employees, checkins=checkins,
                start=investigation_cli.local_time(values['start']), end=investigation_cli.local_time(values['end']),
                devices=devices, output=values['output'], acl_check=self.acl_check, diagnostic=diagnostic)
            directory = _offline_output(dict(values, offline_snapshot=snapshot), self.acl_check, diagnostic)
            diagnostic.enter('worker')
            # Reuse the bounded isolated-process supervisor; this task contains no
            # network transport and does not change the API investigation contract.
            result = run_bounded(_offline_task, options, seconds=120, directory=directory,
                                 cancelled=self.cancelled.is_set)[1]
            if 'diagnostic_failure' in result:
                failure = result['diagnostic_failure']
                if failure.get('reference') != diagnostic.reference:
                    raise investigation_cli.VerificationError('investigation_worker_failed')
                raise DiagnosticError(failure.get('stage'), failure.get('code'), diagnostic.reference)
            diagnostic.emit('complete', 'result_complete' if result['summary']['status'] == 'complete' else 'result_unverified')
            return result
        except Exception as error:
            try:
                failure = error if isinstance(error, DiagnosticError) else diagnostic.worker_failure(error)
            except DiagnosticError as log_error:
                failure = log_error
            raise ManagerError('offline_comparison_blocked', failure) from None

    def batch(self, workspace, batch_id):
        try:
            self._paths(workspace)
            store = RecoveryStore(workspace, acl_check=self.acl_check, readonly=True)
            try:
                data = recovery.results(store, batch_id)
                data['metadata'] = json.loads(store.batch(batch_id)['metadata'])
                return data
            finally:
                store.close()
        except Exception:
            raise ManagerError('recovery_batch_unavailable') from None

    def approve(self, workspace, batch_id, fingerprint, ids, reference):
        try:
            self._paths(workspace)
            if self.cancelled.is_set():
                raise ValueError
            store = RecoveryStore(workspace, acl_check=self.acl_check)
            try:
                recovery.approve(store, batch_id, fingerprint, ids, reference)
            finally:
                store.close()
            return self.batch(workspace, batch_id)
        except Exception:
            raise ManagerError('selected_approval_blocked') from None

    def simulate(self, workspace, batch_id, fingerprint, scenario, normal_lock):
        try:
            self._paths(workspace, scenario, normal_lock)
            if self.cancelled.is_set():
                raise ValueError
            store = RecoveryStore(workspace, acl_check=self.acl_check)
            try:
                recovery.execute(store, batch_id, fingerprint, SimulationTarget(scenario), Path(normal_lock),
                                 stop_requested=self.cancelled.is_set)
            finally:
                store.close()
            return self.batch(workspace, batch_id)
        except Exception:
            raise ManagerError('simulation_blocked_review_batch_or_writer_lock') from None

    def export_recovery(self, workspace, batch_id, output):
        try:
            self._paths(workspace, output)
            store = RecoveryStore(workspace, acl_check=self.acl_check, readonly=True)
            try:
                return recovery.export_results(store, batch_id, output, acl_check=self.acl_check)
            finally:
                store.close()
        except Exception:
            raise ManagerError('protected_recovery_export_failed') from None
