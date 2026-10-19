"""Finite recovery lane owned by the existing service writer. Production is disabled.

There is deliberately no live enable switch, CLI sender, thread or new database.
Only the exact existing SimulationTarget can exercise execution in synthetic tests.
"""
import json
import math
from pathlib import Path
import time

from durable_delivery import FrappeClient, backoff, wait_for_slot
from durable_startup import activation_path, private_storage, validate_activation, worker_lock
from .client import ReadOnlyFrappeClient, VerificationError
from .engine import investigate
from . import recovery
from .recovery_simulation import SimulationTarget
from .service_handoff import Inbox, MAX_ITEMS, identity, untouched, canonical_untouched, require, reference
from .source import verified_source

SCHEMA = (
    'CREATE TABLE sr_requests (id TEXT PRIMARY KEY, hash TEXT NOT NULL, body TEXT NOT NULL, requests_used INTEGER NOT NULL DEFAULT 0)',
    'CREATE TABLE sr_items (request_id TEXT NOT NULL REFERENCES sr_requests(id), punch_id INTEGER NOT NULL REFERENCES punches(id), status TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(request_id,punch_id))',
    'CREATE TABLE sr_intents (identity TEXT PRIMARY KEY, punch_id INTEGER NOT NULL UNIQUE REFERENCES punches(id), second TEXT NOT NULL, employee TEXT NOT NULL, request_id TEXT NOT NULL REFERENCES sr_requests(id), outcome TEXT, checkin_name TEXT)',
    'CREATE INDEX sr_seconds ON sr_intents(second)',
    'CREATE TABLE sr_events (seq INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES sr_requests(id), punch_id INTEGER, status TEXT NOT NULL, reason TEXT NOT NULL, at REAL NOT NULL)',
)


def install(ledger):
    """Optional additive tables; Phase 1 schema/state semantics stay version 1."""
    names = [sql.split()[2] for sql in SCHEMA]
    existing = [ledger.db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone() for name in names]
    if any(existing):
        require(all(existing) and all(''.join(row[0].split()) == ''.join(sql.split())
            for row, sql in zip(existing, SCHEMA)), 'recovery_schema_unverified')
    else:
        with ledger.db:
            ledger.db.execute('BEGIN IMMEDIATE')
            for sql in SCHEMA:
                ledger.db.execute(sql)


class Budget:
    def __init__(self, request, options, *, stopped, clock=time.time, monotonic=time.monotonic, sleep=time.sleep):
        self.transport, self.options, self.stopped = request, options, stopped
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep
        self.end = monotonic() + 120
        self.last = None
        self.last_wall = None
        self.used = 0
        self.deadline = clock() + 120
        self.account = lambda: None
        self.external_pacer = None

    def check(self):
        if self.stopped() or self.monotonic() >= self.end or self.clock() >= self.deadline or self.used >= 128:
            raise VerificationError('recovery_budget_or_cancellation')

    def request(self, method, url, **kwargs):
        self.check()
        require(method in ('GET', 'POST'), 'recovery_http_method_invalid')
        if not wait_for_slot(self.last, self.options['rate'], self.monotonic, self.sleep,
                             lambda: self.stopped() or self.monotonic() >= self.end or self.clock() >= self.deadline):
            raise VerificationError('recovery_budget_or_cancellation')
        if self.external_pacer and not self.external_pacer():
            raise VerificationError('recovery_budget_or_cancellation')
        self.check()
        self.account()  # Count durably before entering transport, including failed reads.
        self.used += 1
        self.last = self.monotonic()
        self.last_wall = self.clock()
        kwargs['timeout'] = min(10, self.end - self.monotonic(), self.deadline - self.clock())
        require(kwargs['timeout'] > 0, 'recovery_budget_or_cancellation')
        response = self.transport(method, url, **kwargs)
        # A late HTTP success is not allowed to become a successful recovery receipt.
        if self.stopped() or self.monotonic() >= self.end or self.clock() >= self.deadline:
            raise VerificationError('recovery_budget_or_cancellation')
        return response


class Coordinator:
    def __init__(self, ledger, config, activation, boundary, options, inbox, target, budget):
        self.ledger, self.config, self.activation = ledger, config, activation
        self.boundary, self.options, self.inbox, self.target, self.budget = boundary, options, inbox, target, budget

    @property
    def last_request_time(self):
        return self.budget.last_wall

    def __call__(self, row, before_request):
        return self.guard(row, before_request)

    def event(self, request_id, punch_id, status, reason):
        self.ledger.db.execute('UPDATE sr_items SET status=?,reason=? WHERE request_id=? AND punch_id=?',
            (status, reason, request_id, punch_id))
        self.ledger.db.execute('INSERT INTO sr_events(request_id,punch_id,status,reason,at) VALUES(?,?,?,?,?)',
            (request_id, punch_id, status, reason, self.budget.clock()))

    def evidence(self, envelope):
        approved, draft = envelope['approved'], envelope['approved']['draft']
        require(draft['schema'] == 'service-recovery-request-v1'
            and recovery.digest(approved) == envelope['approved_sha256']
            and recovery.digest(draft) == approved['draft_sha256'], 'request_approval_binding_invalid')
        require(all(type(envelope[k]) in (int, float) and math.isfinite(envelope[k]) for k in ('dispatched_at', 'deadline'))
            and envelope['dispatched_at'] < envelope['deadline'] <= envelope['dispatched_at'] + 120
            and envelope['dispatched_at'] <= self.budget.clock(), 'request_deadline_invalid')
        require(all(type(v) in (int, float) and math.isfinite(v) for v in
            (draft['created_at'], draft['expires_at'], approved['approved_at']))
            and draft['created_at'] <= approved['approved_at'] <= envelope['dispatched_at']
            and draft['created_at'] < draft['expires_at'] <= draft['created_at'] + 86400,
            'approval_times_invalid')
        # File hashes are over the canonical publication bytes, while these bindings
        # use object digests. Verify the actual approved/draft files as well below.
        recovery.bindings(dict(inputs=draft['input_bindings']))
        metadata, planned = recovery.preparation(**draft['evidence'])
        require(metadata['inputs'] == draft['input_bindings'], 'request_inputs_changed')
        require(self.target.path != self.ledger.path.resolve()
            and all(self.target.path != Path(e['path']).resolve() for e in metadata['inputs'])
            and not self.target.path.is_relative_to(self.inbox.directory.resolve()), 'simulation_input_overlap')
        require(metadata['contract'].get('destination_environment') == 'synthetic'
            and metadata['contract']['site_url'] == self.target.url == self.config.ERPNEXT_URL.rstrip('/')
            and metadata['contract']['verification'].get('read_only_api_verified') is True
            and draft['contract_sha256'] == self.config.HISTORICAL_RECOVERY_CONTRACT_SHA256,
            'service_contract_unverified')
        require(Path(draft['ledger']) == self.ledger.path.resolve()
            and draft['ledger_cookie'] == self.ledger.db.execute('PRAGMA application_id').fetchone()[0]
            and draft['activation_sha256'] == recovery.file_hash(activation_path(self.ledger.path)), 'canonical_ledger_binding_changed')
        for entry in metadata['inputs']:
            self.inbox.storage_check(Path(entry['path']), service_sid=self.inbox.trusted_sid)
        choices = {c['id']: c for c in draft['candidates']}
        ids, held_ids = approved['ids'], approved['held_ids']
        require(ids and len(ids) <= MAX_ITEMS and len(ids) == len(set(ids)) and all(type(i) is int for i in ids)
            and set(ids) <= choices.keys(), 'selection_not_verified')
        require(len(held_ids) == len(set(held_ids)) and all(type(i) is int for i in held_ids)
            and set(held_ids) == {i for i in ids if choices[i]['held']}, 'explicit_selected_hold_authorization_required')
        reference(approved['approval_reference'])
        reference(envelope['execution_reference'])
        if held_ids:
            reference(approved['hold_reference'])
        original = {p['id']: p for p in planned}
        for i in ids:
            require(i in original and choices[i]['identity'] == identity(original[i]['source'])
                    and choices[i]['employee'] == original[i]['employee']
                    and choices[i]['held'] == (original[i]['source']['error_category'] == 'cutover_hold')
                    and untouched(original[i]['source'], held=choices[i]['held']), 'candidate_not_verified')
        return metadata, choices, original

    def compare(self, source, contract, before_request=None):
        self.budget.external_pacer = before_request
        reader = ReadOnlyFrappeClient(self.target.url, self.config.ERPNEXT_API_KEY, self.config.ERPNEXT_API_SECRET,
            self.budget.request, timeout=10, max_requests=128, cancelled=self.budget.stopped)
        stamp, _ = verified_source(source)
        rows, summary = investigate(recovery._OneSource(source), reader, stamp, stamp,
            [source['device_id']], contract, cancelled=self.budget.stopped)
        require(summary['status'] == 'complete' and not summary['blockers'], 'destination_comparison_unverified')
        return rows[0]

    def process(self, request_id):
        envelope = self.inbox.read(request_id, 'request')
        approved, draft = envelope['approved'], envelope['approved']['draft']
        require(draft['id'] == request_id, 'request_id_mismatch')
        require(self.inbox.read(request_id, 'approved') == approved
                and self.inbox.read(request_id, 'draft') == draft, 'request_changed')
        # Authentication comes from the ACL; hashes bind separate review and dispatch.
        require(recovery.file_hash(self.inbox.path(request_id, 'approved')) == envelope['approved_sha256']
            and recovery.file_hash(self.inbox.path(request_id, 'draft')) == approved['draft_sha256'], 'request_changed')
        metadata, choices, originals = self.evidence(envelope)
        digest = recovery.digest(envelope)
        existing = self.ledger.db.execute('SELECT * FROM sr_requests WHERE id=?', (request_id,)).fetchone()
        if existing:
            require(existing['hash'] == digest, 'request_replaced')
        else:
            with self.ledger.db:
                self.ledger.db.execute('INSERT INTO sr_requests(id,hash,body) VALUES(?,?,?)', (request_id, digest, json.dumps(envelope)))
                for i in approved['ids']:
                    self.ledger.db.execute('INSERT INTO sr_items VALUES(?,?,?,?)', (request_id, i, 'Approved', 'explicit_dispatch'))
                    self.event(request_id, i, 'Approved', 'separate_approval_and_dispatch_verified')
                    if i in approved['held_ids']:
                        self.event(request_id, i, 'Approved', 'selected_cutover_exception_authorized')
        self.budget.deadline = min(envelope['deadline'], draft['expires_at'])
        def cancelled():
            if self.inbox.path(request_id, 'cancel').exists():
                receipt = self.inbox.read(request_id, 'cancel')
                require(receipt['request_sha256'] == digest, 'cancellation_binding_invalid')
                return True
            return False
        stop = self.budget.stopped
        self.budget.stopped = lambda: stop() or cancelled()
        def account():
            with self.ledger.db:
                changed = self.ledger.db.execute('UPDATE sr_requests SET requests_used=requests_used+1 WHERE id=? AND requests_used<128',
                    (request_id,)).rowcount
                require(changed == 1, 'recovery_request_limit')
        self.budget.account = account
        for item in list(self.ledger.db.execute('SELECT * FROM sr_items WHERE request_id=? ORDER BY punch_id', (request_id,))):
            if item['status'] not in ('Approved', 'Needs Review'):
                continue
            i = item['punch_id']
            try:
                self.budget.check()
                require(self.ledger.destination_cooldown() <= self.budget.clock(), 'phase1_destination_cooldown')
                recovery.bindings(metadata)
                self.item(request_id, choices[i], originals[i]['source'], metadata['contract'], allow_send=item['status'] == 'Approved')
            except Exception:
                with self.ledger.db:
                    self.event(request_id, i, 'Needs Review', 'verification_budget_cancellation_or_input_blocked')
                # Do not keep scanning or issue requests after an uncertain failure.
                break

    def item(self, request_id, choice, original, contract, *, allow_send):
        i, held = choice['id'], choice['held']
        current = self.ledger.db.execute('SELECT * FROM punches WHERE id=?', (i,)).fetchone()
        require(current is not None and identity(current) == choice['identity'], 'canonical_source_changed')
        prior_source = self.ledger.db.execute('SELECT 1 FROM sr_intents WHERE punch_id=?', (i,)).fetchone()
        if prior_source:
            require(current['state'] in ('delivering', 'retry', 'synced', 'duplicate')
                or (held and current['state'] == 'rejected' and current['error_category'] == 'cutover_hold'),
                'current_quarantine_requires_separate_resolution')
        if not prior_source and not canonical_untouched(self.ledger, current, held=held):
            with self.ledger.db:
                self.event(request_id, i, 'Skipped', 'live_delivery_state_changed')
            return
        stamp, log_type = verified_source(original)
        require(stamp >= self.boundary or held, 'cutover_exception_required')
        result = self.compare(original, contract)
        request = json.loads(self.ledger.db.execute('SELECT body FROM sr_requests WHERE id=?', (request_id,)).fetchone()[0])
        self.evidence(request)
        self.budget.check()
        require(result['employee'] == choice['employee'], 'employee_mapping_changed')
        key = recovery.digest([self.target.url, result['employee'], str(stamp), log_type])
        if result['classification'] == 'already_existing':
            self.budget.check()
            with self.ledger.db:
                self.ledger.db.execute('BEGIN IMMEDIATE')
                current = self.ledger.db.execute('SELECT * FROM punches WHERE id=?', (i,)).fetchone()
                require(identity(current) == choice['identity'], 'canonical_source_changed')
                require(canonical_untouched(self.ledger, current, held=held) or prior_source, 'canonical_source_changed')
                self.ledger.db.execute('INSERT OR IGNORE INTO sr_intents(identity,punch_id,second,employee,request_id,checkin_name) VALUES(?,?,?,?,?,?)',
                    (key, i, str(stamp), result['employee'], request_id, result['checkin_names']))
                self.ledger.db.execute("UPDATE punches SET state='delivering',attempt_count=attempt_count+1,last_attempt=?,lease_until=?,updated_at=? WHERE id=?",
                    (self.budget.clock(), self.budget.clock()+max(180, self.options['lease_seconds']), self.budget.clock(), i))
            leased = dict(self.ledger.db.execute('SELECT * FROM punches WHERE id=?', (i,)).fetchone())
            self.ledger.finish(leased, ('duplicate', 200, 'destination_confirmed', 'Independent destination confirmation', result['checkin_names']), self.budget.clock())
            with self.ledger.db:
                self.event(request_id, i, 'Already Exists', 'independent_destination_confirmation')
            return
        require(result['classification'] == 'potentially_missing', 'candidate_not_verified')
        if not allow_send or prior_source or self.ledger.db.execute('SELECT 1 FROM sr_intents WHERE identity=?', (key,)).fetchone():
            with self.ledger.db:
                self.event(request_id, i, 'Needs Review', 'prior_intent_never_blindly_resubmitted')
            return
        # Any uncertain normal source at this second must be positively resolved,
        # including different raw IDs which may be database-collation equivalents.
        peer_sql = "SELECT * FROM punches WHERE id!=? AND substr(punch_timestamp,1,19)=? AND (attempt_count>0 OR state='delivering' OR EXISTS (SELECT 1 FROM delivery_attempts WHERE punch_id=punches.id)) ORDER BY id"
        peers = list(self.ledger.db.execute(peer_sql, (i, str(stamp))))
        require(len(peers) <= MAX_ITEMS, 'uncertain_peer_limit')
        for peer in peers:
            peer_result = self.compare(dict(peer), contract)
            require(peer_result['classification'] == 'already_existing', 'uncertain_normal_delivery_peer')
        # Recheck all pinned files after remote reads, immediately before claiming.
        self.evidence(request)
        self.budget.check()
        # Revalidate under the same atomic writer transaction which persists intent.
        # A concurrent normal claim wins or loses this transaction, never both sends.
        with self.ledger.db:
            self.ledger.db.execute('BEGIN IMMEDIATE')
            current = self.ledger.db.execute('SELECT * FROM punches WHERE id=?', (i,)).fetchone()
            require(identity(current) == choice['identity'] and canonical_untouched(self.ledger, current, held=held), 'canonical_source_changed')
            require([dict(p) for p in self.ledger.db.execute(peer_sql, (i, str(stamp)))] == [dict(p) for p in peers],
                'normal_delivery_peer_changed')
            require(self.ledger.destination_cooldown() <= self.budget.clock(), 'phase1_destination_cooldown')
            self.ledger.db.execute('INSERT INTO sr_intents(identity,punch_id,second,employee,request_id) VALUES(?,?,?,?,?)',
                (key, i, str(stamp), result['employee'], request_id))
            self.ledger.db.execute("UPDATE punches SET state='delivering',attempt_count=attempt_count+1,last_attempt=?,lease_until=?,updated_at=? WHERE id=?",
                (self.budget.clock(), self.budget.clock()+max(180, self.options['lease_seconds']), self.budget.clock(), i))
            self.event(request_id, i, 'Needs Review', 'submission_intent_recorded_before_post')
        leased = dict(self.ledger.db.execute('SELECT * FROM punches WHERE id=?', (i,)).fetchone())
        self.inbox.check()
        self.evidence(request)
        outcome, guidance = FrappeClient(self.config, self.budget.request).send(json.loads(original['payload']), self.budget.clock())
        with self.ledger.db:
            self.ledger.db.execute('UPDATE sr_intents SET outcome=? WHERE identity=?', (json.dumps(outcome), key))
            self.event(request_id, i, 'Needs Review', 'phase1_transport_outcome_recorded')
        cooldown = 0
        if outcome[0] == 'retry' and outcome[2] not in ('confirmation_pending', 'peer_uncertain'):
            delay = max(backoff(leased['attempt_count'], self.options['base'], self.options['maximum']), guidance)
            if outcome[2] in ('authentication', 'unknown_response'):
                delay = max(delay, self.options['auth_cooldown'])
            cooldown = self.budget.clock() + delay
            self.ledger.pause_destination(cooldown)
        try:
            require(not cooldown, 'phase1_destination_cooldown')
            verified = self.compare(original, contract)
            self.evidence(request)
            self.budget.check()
            confirmed = verified['classification'] == 'already_existing' and verified['employee'] == choice['employee']
        except Exception:
            confirmed = False
        status, reason = 'Needs Review', 'submission_not_independently_verified'
        if confirmed:
            name = verified['checkin_names']
            status = 'Recovered' if outcome[0] == 'synced' and outcome[4] == name else 'Already Exists'
            reason = 'independent_destination_confirmation'
            final = ('synced' if status == 'Recovered' else 'duplicate', outcome[1], 'destination_confirmed', reason, name)
        else:
            # Holds are restored to their original queue class on uncertain outcomes.
            final = (('rejected', outcome[1], 'cutover_hold', 'Authorized historical attempt requires destination review', None) if held
                else ('retry', outcome[1], 'confirmation_pending', 'Recovery intent requires independent destination review', None))
        self.ledger.finish(leased, final, self.budget.clock(), self.budget.clock()+self.options['base'] if final[0]=='retry' else 0, cooldown)
        with self.ledger.db:
            self.event(request_id, i, status, reason)
            if confirmed:
                self.ledger.db.execute('UPDATE sr_intents SET checkin_name=? WHERE identity=?', (name, key))

    def guard(self, row, before_request):
        """Normal delivery overlapping recovery uses verified destination identity first."""
        try:
            intents = self.ledger.db.execute('SELECT * FROM sr_intents WHERE second=? LIMIT 11',
                (row['punch_timestamp'][:19],)).fetchall()
            require(0 < len(intents) <= MAX_ITEMS, 'recovery_overlap_limit')
            # Revalidate the pinned employee of every existing intent. A remapped
            # biometric ID must not acquire a new submission permission implicitly.
            for intent in intents:
                envelope = json.loads(self.ledger.db.execute('SELECT body FROM sr_requests WHERE id=?', (intent['request_id'],)).fetchone()[0])
                metadata, _, originals = self.evidence(envelope)
                prior = self.compare(originals[intent['punch_id']]['source'], metadata['contract'], before_request)
                require(prior['employee'] == intent['employee'] and prior['classification'] in ('already_existing', 'potentially_missing'),
                    'recovery_employee_mapping_changed')
            # The current normal lease is not a historical absence assertion. Only
            # ignore that lease in comparison; Phase 1 still controls its retry rules.
            source = dict(row, state='pending', checkin_name=None)
            result = self.compare(source, metadata['contract'], before_request)
            if result['classification'] == 'already_existing':
                return ('duplicate', 200, 'destination_confirmed', 'Existing destination identity independently verified', result['checkin_names'])
            if result['classification'] == 'potentially_missing':
                stamp, log_type = verified_source(source)
                key = recovery.digest([self.target.url, result['employee'], str(stamp), log_type])
                if not self.ledger.db.execute('SELECT 1 FROM sr_intents WHERE identity=? OR punch_id=?', (key, row['id'])).fetchone():
                    return None  # Verified unrelated normal attendance keeps its existing delivery rules.
        except Exception:
            pass
        return ('retry', 0, 'confirmation_pending', 'Recovery overlap requires independent destination verification', None)


def service_tick(ledger, config, activation, boundary, options, *, stop_requested=lambda: False,
                 target=None, storage_check=private_storage, clock=time.time, monotonic=time.monotonic, sleep=time.sleep):
    """Called only inside the existing writer cycle. A real service cannot enable POST.

    Production execution remains unconditionally unavailable: no config flag can
    substitute for the exact synthetic transport argument, which the service never supplies.
    """
    if type(target) is not SimulationTarget:
        return None
    require(target.config.ERPNEXT_URL == config.ERPNEXT_URL and target.url == config.ERPNEXT_URL.rstrip('/'), 'synthetic_destination_mismatch')
    storage_check(ledger.path)
    with worker_lock(ledger.path):
        require(validate_activation(ledger.path, config) == activation, 'activation_changed')
        inbox = Inbox(config.HISTORICAL_RECOVERY_INBOX, trusted_sid=getattr(config, 'HISTORICAL_REQUEST_AUTHOR_SID', None), storage_check=storage_check)
        install(ledger)
        budget = Budget(target.request, options, stopped=stop_requested, clock=clock, monotonic=monotonic, sleep=sleep)
        coordinator = Coordinator(ledger, config, activation, boundary, options, inbox, target, budget)
        from itertools import islice
        files = list(islice(inbox.directory.glob('*.request.json'), 101))
        require(len(files) <= 100, 'request_inbox_limit')
        # One finite request per service tick; approval files alone are never scanned.
        for path in sorted(files):
            request_id = path.name.split('.')[0]
            try:
                inbox.path(request_id, 'request')
            except VerificationError:
                continue
            rows = ledger.db.execute('SELECT status FROM sr_items WHERE request_id=?', (request_id,)).fetchall()
            if rows and all(r[0] not in ('Approved', 'Needs Review') for r in rows):
                continue
            known = ledger.db.execute('SELECT body,requests_used FROM sr_requests WHERE id=?', (request_id,)).fetchone()
            if known and (not rows or known['requests_used'] >= 128
                    or json.loads(known['body']).get('deadline', 0) <= clock()):
                continue
            try:
                coordinator.process(request_id)
            except Exception:
                # A malformed handoff must not stop unrelated normal attendance.
                # No raw exception, filename, employee or reference enters logs.
                with ledger.db:
                    ledger.db.execute('INSERT OR IGNORE INTO sr_requests(id,hash,body) VALUES(?,?,?)',
                        (request_id, 'rejected', '{}'))
                    coordinator.event(request_id, None, 'Needs Review', 'request_validation_failed')
            break
        # Normal overlap checks have a separate finite budget, with the same transport
        # pacing timestamp. They cannot consume/extend a request's durable allowance.
        previous = coordinator.budget
        coordinator.budget = Budget(target.request, options, stopped=stop_requested, clock=clock, monotonic=monotonic, sleep=sleep)
        coordinator.budget.last, coordinator.budget.last_wall = previous.last, previous.last_wall
        return coordinator


def request_results(ledger, request_id):
    """Private audit read; suitable for a future thin manager adapter, no execution."""
    from collections import Counter
    items = [dict(row) for row in ledger.db.execute('SELECT * FROM sr_items WHERE request_id=? ORDER BY punch_id', (request_id,))]
    events = [dict(row) for row in ledger.db.execute('SELECT * FROM sr_events WHERE request_id=? ORDER BY seq', (request_id,))]
    return dict(request_id=request_id, production_execution_supported=False,
        counts=dict(Counter(row['status'] for row in items)), items=items, events=events)
