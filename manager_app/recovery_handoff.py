"""Thin file handoff/audit adapter. Never creates a transport or writable ledger."""
import time
import re
from pathlib import Path
from durable_startup import activation_path

from sync_ledger import Ledger
from historical_attendance import recovery, recovery_preview, service_handoff as handoff
from historical_attendance.service_recovery import install, request_results
from historical_attendance.diagnostics import Trace, DiagnosticError, STAGES
from historical_attendance.reporting import validate_output
from scripts.attendance_reconciliation import verified_json
from .controller import ManagerError


class RecoveryDiagnostic(DiagnosticError):
    @property
    def message(self):
        message = f'Recovery diagnostic {self.reference}: {self.stage} / {self.code}. {STAGES[self.stage]}'
        if self.stage in ('diagnostics', 'recovery_handoff'):
            message += ' Refresh the request before repeating an action; its handoff may already be published. Do not reset ledger states.'
        return message


class Workflow:
    def __init__(self, controller, values):
        self.controller, self.values = controller, values

    def run(self, stage, operation):
        trace = None
        initial = 'profile'
        try:
            v = self.values
            required = ('backup', 'backup_hash', 'contract', 'contract_hash', 'recovery_ledger',
                        'recovery_summary', 'recovery_summary_hash', 'recovery_inbox', 'output')
            handoff.require(all(v.get(k) for k in required), 'recovery_profile_incomplete')
            self.controller._paths(*(v[k] for k in required if k not in ('backup_hash', 'contract_hash', 'recovery_summary_hash')))
            # Validate separation before creating a diagnostic file, including direct calls.
            initial = 'diagnostics'
            validate_output(v.get('diagnostics_directory') or v['output'], ledger_path=v['backup'],
                input_paths=[v['contract'], v['recovery_summary'], v['recovery_ledger']], acl_check=self.controller.acl_check)
            trace = Trace(v, self.controller.acl_check, kind='recovery')
            trace.enter(stage)
            if self.controller.cancelled.is_set():
                raise ValueError('investigation_cancelled')
            inbox = handoff.Inbox(v['recovery_inbox'], trusted_sid=v.get('recovery_service_sid') or None,
                                 storage_check=self.controller.storage_check)
            result = operation(inbox)
            trace.emit('complete', 'ok')
            return result
        except Exception as error:
            if trace:
                try:
                    failure = trace.fail(error)
                except DiagnosticError as logging_error:
                    failure = logging_error
                raise ManagerError('service_workflow_blocked',
                    RecoveryDiagnostic(failure.stage, failure.code, failure.reference)) from None
            if isinstance(error, DiagnosticError):
                raise ManagerError('service_workflow_blocked', RecoveryDiagnostic(error.stage, error.code, error.reference)) from None
            raise ManagerError('service_diagnostics_unavailable' if initial == 'diagnostics' else 'service_profile_incomplete') from None

    def gate(self):
        v = self.values
        contract = verified_json(v['contract'], v['contract_hash'])[0]
        handoff.require(contract.get('destination_environment') == 'synthetic'
            and contract['site_url'].endswith('.example.test'), 'recovery_production_disabled')
        handoff.require(contract.get('verification', {}).get('read_only_api_verified') is True, 'read_only_api_unverified')

    def load(self, inbox, identity):
        v = self.values
        draft = inbox.read(identity, 'draft')
        handoff.require(draft['id'] == identity and draft['schema'] == 'service-recovery-request-v1'
            and draft['ledger'] == str(Path(v['recovery_ledger']).resolve())
            and draft['evidence']['ledger_path'] == v['backup']
            and draft['evidence']['summary_path'] == v['recovery_summary']
            and draft['evidence']['summary_sha256'] == v['recovery_summary_hash']
            and draft['contract_sha256'] == v['contract_hash']
            and draft['input_bindings'][0]['sha256'] == v['backup_hash'], 'recovery_handoff_changed')
        handoff.require(draft['activation_sha256'] == recovery.file_hash(activation_path(Path(v['recovery_ledger']))),
                        'canonical_ledger_binding_changed')
        approved = inbox.read(identity, 'approved') if inbox.path(identity, 'approved').exists() else None
        request = inbox.read(identity, 'request') if inbox.path(identity, 'request').exists() else None
        if approved:
            handoff.require(approved['draft'] == draft and approved['draft_sha256'] == recovery.file_hash(inbox.path(identity, 'draft')),
                'recovery_handoff_changed')
        if request:
            handoff.require(approved and request['approved'] == approved
                and request['approved_sha256'] == recovery.file_hash(inbox.path(identity, 'approved')), 'recovery_handoff_changed')
        return draft, approved, request

    def prepare(self, preview, ids, receipt):
        def operation(inbox):
            self.gate()
            handoff.require(preview is not None, 'recovery_preview_required')
            handoff.reference(receipt)
            v = self.values
            recovery_preview.verify_export(preview, backup=v['backup'], backup_hash=v['backup_hash'],
                directory=v['output'], acl_check=self.controller.acl_check)
            rows = {r['id']: r for r in preview['rows']}
            handoff.require(ids and len(ids) <= handoff.MAX_ITEMS and len(ids) == len(set(ids))
                and all(i in rows and rows[i]['classification'] in
                    ('Candidate for destination verification', 'Historical cutover hold') for i in ids), 'selection_not_verified')
            # Repeated browser submissions reuse the same immutable draft. They do
            # not create another dispatch or extend an existing execution deadline.
            for path in self.catalog(inbox):
                old = inbox.read(path, 'draft')
                if (old['evidence']['summary_sha256'] != v['recovery_summary_hash']
                        or old['input_bindings'][0]['sha256'] != v['backup_hash']):
                    continue
                draft, _, _ = self.load(inbox, path)
                if ({c['id'] for c in draft['candidates']} == set(ids) and draft['expires_at'] > time.time()):
                    return path
            identity, _ = handoff.prepare_request(inbox, Path(v['recovery_ledger']), ledger_path=v['backup'],
                summary_path=v['recovery_summary'], summary_sha256=v['recovery_summary_hash'],
                contract_path=v['contract'], contract_sha256=v['contract_hash'], receipt_review=receipt, selected_ids=ids)
            return identity
        return self.run('recovery_evidence', operation)

    def approve(self, identity, fingerprint, ids, held_ids, reference, hold_reference):
        def operation(inbox):
            self.gate()
            draft, approved, request = self.load(inbox, identity)
            handoff.require(fingerprint == recovery.file_hash(inbox.path(identity, 'draft')), 'request_changed')
            recovery.bindings(dict(inputs=draft['input_bindings']))
            if approved:
                handoff.require(set(ids) == set(approved['ids']) and set(held_ids) == set(approved['held_ids'])
                    and reference == approved['approval_reference'] and hold_reference == approved['hold_reference'], 'approval_changed')
            else:
                handoff.approve_request(inbox, identity, fingerprint, ids, approval_reference=reference,
                    held_ids=held_ids, hold_reference=hold_reference)
            return identity
        return self.run('recovery_handoff', operation)

    def dispatch(self, identity, fingerprint, reference):
        def operation(inbox):
            self.gate()
            draft, approved, request = self.load(inbox, identity)
            handoff.require(approved is not None, 'recovery_evidence_required')
            handoff.require(fingerprint == recovery.file_hash(inbox.path(identity, 'approved')), 'approval_changed')
            recovery.bindings(dict(inputs=draft['input_bindings']))
            if request:
                handoff.require(request['execution_reference'] == reference, 'request_changed')
            else:
                handoff.dispatch_request(inbox, identity, fingerprint, execution_reference=reference)
            return identity
        return self.run('recovery_handoff', operation)

    def cancel(self, identity, reference):
        def operation(inbox):
            self.gate()
            _, _, request = self.load(inbox, identity)
            handoff.require(request is not None, 'recovery_request_not_found')
            if inbox.path(identity, 'cancel').exists():
                handoff.require(inbox.read(identity, 'cancel')['request_sha256'] == recovery.digest(request), 'request_changed')
            else:
                handoff.cancel_request(inbox, identity, cancellation_reference=reference)
            return identity
        return self.run('recovery_handoff', operation)

    def catalog(self, inbox):
        from itertools import islice
        paths = list(islice(inbox.directory.glob('*.draft.json'), 101))
        handoff.require(len(paths) <= 100, 'recovery_scope_changed')
        handoff.require(all(re.fullmatch(r'[0-9a-f]{32}\.draft\.json', p.name) for p in paths), 'recovery_handoff_changed')
        return sorted(p.name.split('.')[0] for p in paths)

    def listing(self):
        def operation(inbox):
            return [dict(id=i, status='Dispatched' if inbox.path(i, 'request').exists()
                else 'Approved' if inbox.path(i, 'approved').exists() else 'Pending Approval') for i in self.catalog(inbox)]
        return self.run('recovery_status', operation)

    def details(self, identity):
        def operation(inbox):
            draft, approved, request = self.load(inbox, identity)
            cancelled = inbox.path(identity, 'cancel').exists()
            if cancelled:
                handoff.require(request and inbox.read(identity, 'cancel')['request_sha256'] == recovery.digest(request), 'request_changed')
            statuses, events = {}, []
            v = self.values
            self.controller.storage_check(Path(v['recovery_ledger']), service_sid=v.get('recovery_service_sid') or None)
            with Ledger(Path(v['recovery_ledger']), readonly=True) as ledger:
                ledger.db.execute('PRAGMA query_only=ON')
                ledger.db.execute('BEGIN')
                handoff.require(ledger.db.execute('PRAGMA application_id').fetchone()[0] == draft['ledger_cookie'], 'canonical_ledger_binding_changed')
                for candidate in draft['candidates']:
                    current = ledger.db.execute('SELECT * FROM punches WHERE id=?', (candidate['id'],)).fetchone()
                    handoff.require(current is not None and handoff.identity(current) == candidate['identity'], 'canonical_source_changed')
                if ledger.db.execute("SELECT 1 FROM sqlite_master WHERE name='sr_requests'").fetchone():
                    install(ledger)  # Existing schema verification only; query_only forbids writes.
                    stored = ledger.db.execute('SELECT hash,body FROM sr_requests WHERE id=?', (identity,)).fetchone()
                    if stored:
                        rejected = stored['hash'] == 'rejected' and stored['body'] == '{}'
                        handoff.require(request and (rejected or stored['hash'] == recovery.digest(request)), 'recovery_handoff_changed')
                        data = request_results(ledger, identity)
                        statuses = {r['punch_id']: r for r in data['items']}
                        if rejected:
                            statuses = {i: dict(status='Needs Review', reason='request_validation_failed') for i in approved['ids']}
                        events = [dict(status=e['status'] if e['status'] in ('Approved', 'Needs Review', 'Recovered',
                            'Already Exists', 'Skipped', 'Failed') else 'Needs Review', reason=self.reason(e['reason']))
                            for e in data['events'][-100:]]
            rows = []
            for candidate in draft['candidates']:
                i = candidate['id']
                item = statuses.get(i)
                status = item['status'] if item else ('Queued for service' if request and i in approved['ids']
                    else 'Approved' if approved and i in approved['ids'] else 'Skipped' if approved else 'Pending Approval')
                if request and time.time() >= request['deadline'] and status in ('Approved', 'Queued for service'):
                    status = 'Needs Review'
                handoff.require(status in ('Pending Approval', 'Approved', 'Queued for service', 'Already Exists', 'Recovered',
                    'Skipped', 'Needs Review', 'Failed'), 'recovery_ledger_unavailable')
                rows.append(dict(id=i, held=candidate['held'], status=status,
                    reason=self.reason(item['reason']) if item else 'Waiting for explicit operator action or service verification.'))
            contract = verified_json(v['contract'], v['contract_hash'])[0]
            return dict(id=identity, rows=rows, events=events, cancelled=cancelled,
                draft_hash=recovery.file_hash(inbox.path(identity, 'draft')),
                approved_hash=recovery.file_hash(inbox.path(identity, 'approved')) if approved else '',
                approved=approved is not None, dispatched=request is not None,
                can_operate=(contract.get('destination_environment') == 'synthetic' and contract['site_url'].endswith('.example.test')
                    and contract.get('verification', {}).get('read_only_api_verified') is True),
                expired=time.time() >= (request['deadline'] if request else draft['expires_at']))
        return self.run('recovery_status', operation)

    @staticmethod
    def reason(code):
        return {
            'separate_approval_and_dispatch_verified': 'Separate approval and dispatch verified.',
            'selected_cutover_exception_authorized': 'This selected cutover exception was explicitly authorized.',
            'submission_intent_recorded_before_post': 'Durable submission intent recorded; an uncertain attempt cannot be resubmitted.',
            'independent_destination_confirmation': 'Destination identity independently confirmed.',
            'live_delivery_state_changed': 'Canonical delivery state changed; record skipped.',
            'prior_intent_never_blindly_resubmitted': 'Prior intent retained; no blind resubmission.',
            'phase1_transport_outcome_recorded': 'Transport response recorded; destination proof is still required.',
            'submission_not_independently_verified': 'Destination not independently verified. Review required.',
            'request_validation_failed': 'Request verification failed; review protected inputs and bindings.',
        }.get(code, 'Review required. See the protected service audit; no automatic retry is authorized.')
