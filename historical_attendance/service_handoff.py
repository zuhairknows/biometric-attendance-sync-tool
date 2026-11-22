"""Private Windows file handoff. Approval is inert until a separate dispatch.

The operating-system ACL is the authentication boundary, not a browser-supplied
username or a new shared secret. Administrators/SYSTEM remain trusted principals.
Only the existing service writes execution state in its existing SQLite ledger.
"""
import json
from pathlib import Path
import re
import time
import uuid

from durable_startup import activation_path, private_storage
from sync_ledger import CAPTURE_FIELDS, Ledger
from scripts.attendance_reconciliation import verified_json
from scripts.reconcile_attendance import publish
from . import recovery

MAX_ITEMS = 10
IMMUTABLE = CAPTURE_FIELDS[:9] + ('id', 'first_captured', 'created_at')


def require(condition, code):
    recovery.require(condition, code)


def identity(row):
    return recovery.digest({key: row[key] for key in IMMUTABLE})


def untouched(row, *, held=False):
    expected = row['state'] == 'rejected' and row['error_category'] == 'cutover_hold' if held else recovery.eligible_source(row)
    return (expected and row['attempt_count'] == 0 and not row['checkin_name'] and row['next_retry'] == 0
            and (held or not row['error_message'])
            and all(row[k] is None for k in ('last_attempt', 'last_http_status', 'lease_until')))


def canonical_untouched(ledger, row, *, held=False):
    return untouched(row, held=held) and not ledger.db.execute(
        'SELECT 1 FROM delivery_attempts WHERE punch_id=? LIMIT 1', (row['id'],)).fetchone()


class Inbox:
    def __init__(self, directory, *, trusted_sid=None, storage_check=private_storage):
        self.directory = Path(directory)
        self.trusted_sid, self.storage_check = trusted_sid, storage_check
        self.check()

    def check(self):
        # On the service side trusted_sid is the explicitly authorized operator SID;
        # on the manager side it is the service account SID. Never taken from a request.
        self.storage_check(self.directory / 'handoff', service_sid=self.trusted_sid)

    def path(self, request_id, kind):
        require(isinstance(request_id, str) and re.fullmatch('[0-9a-f]{32}', request_id), 'request_id_invalid')
        require(kind in ('draft', 'approved', 'request', 'cancel'), 'request_kind_invalid')
        return self.directory / (request_id + '.' + kind + '.json')

    def read(self, request_id, kind):
        self.check()
        path = self.path(request_id, kind)
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024, 'request_file_invalid')
        # Duplicate JSON keys and changes during the read are rejected by the shared reader.
        return verified_json(path, recovery.file_hash(path))[0]

    def write(self, request_id, kind, value):
        self.check()
        path = self.path(request_id, kind)
        require(not path.exists(), 'request_already_published')
        require(len(json.dumps(value, sort_keys=True).encode('utf-8')) <= 1024 * 1024, 'request_file_invalid')
        publish(self.directory, path.name, lambda stream: json.dump(value, stream, sort_keys=True))
        return recovery.file_hash(path)


def reference(value):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 200 and not any(c in value for c in '\r\n'),
            'explicit_review_reference_required')
    return value


def prepare_request(inbox, live_ledger, *, ledger_path, summary_path, summary_sha256,
                    contract_path, contract_sha256, receipt_review, selected_ids=None, now=time.time):
    """No submission and no live-ledger writes; only fully verified Phase 2A evidence."""
    inbox.check()
    arguments = dict(ledger_path=str(ledger_path), summary_path=str(summary_path), summary_sha256=summary_sha256,
        contract_path=str(contract_path), contract_sha256=contract_sha256, receipt_review=reference(receipt_review))
    metadata, items = recovery.preparation(**arguments)
    if selected_ids is not None:
        require(selected_ids and len(selected_ids) <= MAX_ITEMS and len(set(selected_ids)) == len(selected_ids)
            and all(type(i) is int for i in selected_ids), 'selected_approval_invalid')
        items = [item for item in items if item['id'] in selected_ids]
        require({item['id'] for item in items} == set(selected_ids), 'selection_not_verified')
    require(metadata['contract']['verification'].get('read_only_api_verified') is True, 'read_only_api_unverified')
    for entry in metadata['inputs']:
        inbox.storage_check(Path(entry['path']), service_sid=inbox.trusted_sid)
    live = Path(live_ledger)
    inbox.storage_check(live, service_sid=inbox.trusted_sid)
    from .reporting import validate_output
    validate_output(inbox.directory, ledger_path=live,
        input_paths=[Path(entry['path']) for entry in metadata['inputs']],
        acl_check=lambda directory: inbox.storage_check(Path(directory) / 'handoff', service_sid=inbox.trusted_sid))
    require(live.resolve() != Path(metadata['source']).resolve(), 'snapshot_must_be_separate_from_live_ledger')
    with Ledger(live, readonly=True) as ledger, Ledger(Path(metadata['source']), readonly=True) as snapshot:
        cookie = ledger.db.execute('PRAGMA application_id').fetchone()[0]
        require(cookie > 0 and cookie == snapshot.db.execute('PRAGMA application_id').fetchone()[0], 'canonical_ledger_binding_required')
        candidates = []
        for item in items:
            original = item['source']
            held = original['error_category'] == 'cutover_hold'
            if not untouched(original, held=held):
                continue
            current = ledger.db.execute('SELECT * FROM punches WHERE id=? AND identity_key=?',
                (original['id'], original['identity_key'])).fetchone()
            require(current is not None and identity(current) == identity(original)
                    and canonical_untouched(ledger, current, held=held), 'canonical_source_changed')
            candidates.append(dict(id=original['id'], identity=identity(original),
                identity_key=original['identity_key'], employee=item['employee'], held=held))
    require(candidates and len(candidates) <= 100, 'narrow_verified_candidate_scope_required')
    require(selected_ids is None or {c['id'] for c in candidates} == set(selected_ids), 'selection_not_verified')
    request_id = uuid.uuid4().hex
    created = now()
    draft = dict(schema='service-recovery-request-v1', id=request_id, evidence=arguments,
        input_bindings=metadata['inputs'],
        ledger=str(live.resolve()), ledger_cookie=cookie,
        activation_sha256=recovery.file_hash(activation_path(live)),
        contract_sha256=contract_sha256, candidates=candidates,
        expires_at=created + 86400, created_at=created)
    return request_id, inbox.write(request_id, 'draft', draft)


def approve_request(inbox, request_id, draft_sha256, ids, *, approval_reference,
                    held_ids=(), hold_reference='', now=time.time):
    """Separate selected-record and cutover-exception authorization. Still inert."""
    draft = inbox.read(request_id, 'draft')
    require(recovery.file_hash(inbox.path(request_id, 'draft')) == draft_sha256, 'request_changed')
    require(draft['expires_at'] > now() and ids and len(ids) <= MAX_ITEMS
            and len(set(ids)) == len(ids) and all(type(i) is int for i in ids), 'selected_approval_invalid')
    choices = {c['id']: c for c in draft['candidates']}
    require(set(ids) <= choices.keys(), 'selection_not_verified')
    expected_holds = {i for i in ids if choices[i]['held']}
    require(set(held_ids) == expected_holds and len(held_ids) == len(set(held_ids))
            and all(type(i) is int for i in held_ids), 'explicit_selected_hold_authorization_required')
    if expected_holds:
        reference(hold_reference)
    value = dict(draft=draft, draft_sha256=draft_sha256, ids=ids, held_ids=list(held_ids),
        approval_reference=reference(approval_reference), hold_reference=hold_reference,
        approved_at=now())
    return inbox.write(request_id, 'approved', value)


def dispatch_request(inbox, request_id, approved_sha256, *, execution_reference, now=time.time):
    approved = inbox.read(request_id, 'approved')
    require(recovery.file_hash(inbox.path(request_id, 'approved')) == approved_sha256, 'approval_changed')
    require(approved['draft']['expires_at'] > now(), 'request_expired')
    # A request appears in the service's allowlisted inbox only after this second action.
    dispatched = now()
    value = dict(approved=approved, approved_sha256=approved_sha256,
        execution_reference=reference(execution_reference), dispatched_at=dispatched, deadline=dispatched + 120)
    return inbox.write(request_id, 'request', value)


def cancel_request(inbox, request_id, *, cancellation_reference):
    request = inbox.read(request_id, 'request')
    return inbox.write(request_id, 'cancel', dict(request_sha256=recovery.digest(request),
        reference=reference(cancellation_reference)))
