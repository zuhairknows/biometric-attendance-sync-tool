"""Step 5C2: accepted-then-timeout, conservative retry and positive confirmation.

Actual pinned durable_sync.run(), synthetic device input and literal-loopback
HTTPS only. No service operations. Every run retains a new private attempt.
"""
import argparse
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import logging
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request


CRASH_HELPER_HASH = '91d5229af15c26d2faa85058df1abcf218878a666ae3fcb7e712f439d225326b'
CURRENT_PHASE = 'preparation'


def helpers():
    path = Path(__file__).with_name('phase1_staging_engine_crash.py')
    if hashlib.sha256(path.read_bytes()).hexdigest() != CRASH_HELPER_HASH:
        raise ValueError('step5c1_helper_hash_mismatch')
    spec = importlib.util.spec_from_file_location('reviewed_step5c1', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.helper()


def wait_until_due(Ledger, ledger):
    # Exercise real retry timers. Never edit next_retry, attempts or cooldown.
    with Ledger(ledger, readonly=True) as db:
        row = db.db.execute("SELECT MAX(next_retry) FROM punches WHERE state='retry'").fetchone()
        deadline = max(row[0] or 0, db.destination_cooldown())
    delay = max(0, deadline - time.time())
    if delay > 2:
        raise ValueError('unexpected_synthetic_retry_delay')
    threading.Event().wait(delay + 0.02)


def run(stage, shared, base):
    global CURRENT_PHASE
    created = base.prepare(stage, Path(__file__).resolve().parents[1])
    attempt = created.with_name(created.name.replace('step5b-', 'step5c2-', 1))
    created.rename(attempt)  # Only the folder just created by this invocation.
    stage = stage.resolve()
    sync, startup, Ledger = shared.frozen_engine(attempt, base)
    server_tls, client_tls = shared.local_tls(stage)
    output = attempt / 'data'
    ledger = output / 'synthetic.sqlite3'
    with (output / 'timeout-provenance.json').open('x', encoding='utf-8') as stream:
        json.dump(dict(revision=base.REVISION, source_sha256=base.HASHES,
            harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            crash_helper_sha256=CRASH_HELPER_HASH, https_helper_sha256=shared.HELPER_HASH), stream, indent=2)
    policy = base.Policy(attempt, stage / 'localhost-cert.pem', stage / 'localhost-key.pem', ledger, shared.acl_script(startup))
    shared.activate_guard(policy)
    boundary = dt.datetime.now().replace(microsecond=0) + dt.timedelta(minutes=1)
    stamps = [boundary + dt.timedelta(seconds=1), boundary + dt.timedelta(seconds=11)]
    payloads = [dict(employee_field_value='STAGE-SYNTHETIC-001', timestamp=str(stamp),
        device_id='STAGE-DEVICE', log_type=None, latitude=None, longitude=None) for stamp in stamps]
    accepted, documents, reads, transport_events = [], [], [], []
    timeout_finished = threading.Event()
    mode = ['empty']
    checks, observations = {}, {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            size = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(size)) if 0 < size <= 4096 else {}
            if self.path != base.METHOD or payload not in payloads:
                return self.reply(400, dict(mock=True))
            # Intentionally NO mock deduplication: every POST creates another
            # document, so a blind retry cannot be hidden by mock idempotency.
            accepted.append(payload)
            name = 'MOCK-CHECKIN-' + str(len(documents) + 1).zfill(3)
            documents.append(dict(name=name, employee='STAGE-EMP',
                time=str(dt.datetime.fromisoformat(payload['timestamp']).replace(microsecond=0)),
                log_type=payload['log_type'] or ''))
            if len(accepted) == 1:
                # Acceptance precedes the real client's socket timeout. Send no
                # response; keep this connection open longer than its 0.5s limit.
                threading.Event().wait(1.25)
                self.close_connection = True
                timeout_finished.set()
                return
            return self.reply(200, dict(message=dict(name=name)))

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            params = urllib.parse.parse_qs(parsed.query)
            if set(params) != {'fields', 'filters', 'limit_page_length'} or params['limit_page_length'] != ['2']:
                return self.reply(400, dict(mock=True))
            fields, filters = json.loads(params['fields'][0]), json.loads(params['filters'][0])
            if path == '/api/resource/Employee':
                if fields != ['name', 'status', 'attendance_device_id'] or filters != [['attendance_device_id', '=', 'STAGE-SYNTHETIC-001']]:
                    return self.reply(400, dict(mock=True))
                reads.append('employee')
                return self.reply(200, dict(data=[dict(name='STAGE-EMP', status='Active', attendance_device_id='STAGE-SYNTHETIC-001')]))
            if path == '/api/resource/Employee Checkin':
                if fields != ['name', 'employee', 'time', 'log_type'] or filters != [
                        ['employee', '=', 'STAGE-EMP'], ['time', '=', str(stamps[0])], ['log_type', '=', '']]:
                    return self.reply(400, dict(mock=True))
                reads.append('checkin_' + mode[0])
                matches = [dict(row) for row in documents if row['time'] == str(stamps[0])]
                if mode[0] == 'empty':
                    matches = []
                elif mode[0] == 'null':
                    matches = [dict(row, log_type=None) for row in matches]
                return self.reply(200, dict(data=matches))
            return self.reply(404, dict(mock=True))

    server = HTTPServer(('127.0.0.1', 0), Handler)
    policy.port = server.server_port
    server.socket = server_tls.wrap_socket(server.socket, server_side=True)
    origin = 'https://127.0.0.1:' + str(policy.port)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), base.NoRedirect(),
                                        urllib.request.HTTPSHandler(context=client_tls))
    guarded_request = base.transport(opener, origin)

    def request(method, url, **kwargs):
        try:
            response = guarded_request(method, url, **kwargs)
        except Exception as error:
            # Record only fixed categories, never exception text, URL or headers.
            real_timeout = isinstance(error, (TimeoutError, socket.timeout)) or (
                isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError))
            transport_events.append(dict(method=method, timeout=real_timeout, phase=CURRENT_PHASE))
            raise
        transport_events.append(dict(method=method, status=response.status_code, phase=CURRENT_PHASE))
        return response

    config = SimpleNamespace(DURABLE_DB_PATH=str(ledger), ERPNEXT_URL=origin, ERPNEXT_VERSION=15,
        ERPNEXT_API_KEY='SYNTHETIC', ERPNEXT_API_SECRET='SYNTHETIC', ERPNEXT_REQUEST_TIMEOUT=0.5,
        DURABLE_BATCH_SIZE=2, DURABLE_REQUESTS_PER_SECOND=20,
        DURABLE_RETRY_BASE_SECONDS=0.1, DURABLE_RETRY_MAX_SECONDS=0.2,
        IMPORT_START_DATE=None, PULL_FREQUENCY=1, shift_type_device_mapping=[],
        DURABLE_CONFIRM_READS_VERIFIED=False,
        devices=[dict(device_id='STAGE-DEVICE', ip='SYNTHETIC-NO-SOCKET', port=4370,
            password=0, punch_direction=None, clear_from_device_on_fetch=False)])
    logger = logging.getLogger('step5c2')
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    polls = []

    def records(*unused, **kwargs):
        polls.append(True)
        return [dict(user_id='STAGE-SYNTHETIC-001', timestamp=stamp, punch=0, status=1, uid=index + 1)
                for index, stamp in enumerate(stamps)]

    engine = SimpleNamespace(config=config, requests=SimpleNamespace(request=request),
        info_logger=logger, error_logger=logger, status=SimpleNamespace(usable=True),
        device_punch_values_IN=[0, 4], device_punch_values_OUT=[1, 5],
        validate_unique_device_ids=lambda devices: None, normalize_device_config=dict,
        get_dump_file_name_and_directory=lambda _: str(output / 'unused-synthetic-dump'),
        get_all_attendance_from_device=records)

    def observe(label):
        with Ledger(ledger, readonly=True) as db:
            db.verify_integrity()
            rows = [dict(row) for row in db.db.execute('SELECT state,error_category,attempt_count,payload,checkin_name FROM punches ORDER BY id')]
            checks[label + '_evidence_unchanged'] = len(rows) == 2 and [json.loads(row['payload']) for row in rows] == payloads
            checks[label + '_no_shift_write'] = db.db.execute('SELECT COUNT(*) FROM shift_state').fetchone()[0] == 0
            observation = dict(rows=[{key: row[key] for key in ('state', 'error_category', 'attempt_count')} for row in rows],
                uncertain=db.diagnostics()['uncertain_submissions'], post_count=len(accepted), read_count=len(reads), integrity='ok')
        with (output / (label + '.json')).open('x', encoding='utf-8') as stream:
            json.dump(observation, stream, indent=2)
        observations[label] = observation
        return rows, observation

    def require_checks():
        if not all(checks.values()):
            raise ValueError('timeout_assertion_failed')

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    failure = None
    try:
        startup.provision(ledger, origin, 15, str(boundary), 'SYNTHETIC LOCALHOST STEP 5C2 ONLY')
        CURRENT_PHASE = 'accepted_timeout'
        sync.run(engine)
        checks['mock_accepted_before_real_timeout'] = accepted == [payloads[0]] and transport_events == [dict(method='POST', timeout=True, phase=CURRENT_PHASE)]
        rows, observation = observe(CURRENT_PHASE)
        checks['ledger_records_uncertain_transport'] = (rows[0]['state'] == 'retry' and rows[0]['error_category'] == 'transport'
            and rows[0]['attempt_count'] == 1 and observation['uncertain'] == 1)
        checks['unrelated_initially_pending'] = rows[1]['state'] == 'pending' and rows[1]['attempt_count'] == 0
        checks['timed_out_mock_handler_finished'] = timeout_finished.wait(3)
        require_checks()

        for label in ('unverified_retry_1', 'unverified_retry_2', 'unverified_retry_3'):
            CURRENT_PHASE = label
            wait_until_due(Ledger, ledger)
            sync.run(engine)
            rows, observation = observe(label)
            checks[label + '_no_blind_post_or_read'] = accepted == payloads and reads == []
            checks[label + '_uncertainty_held'] = rows[0]['state'] == 'retry' and rows[0]['error_category'] == 'confirmation_pending' and observation['uncertain'] == 1
            checks[label + '_unrelated_synced'] = rows[1]['state'] == 'synced' and rows[1]['attempt_count'] == 1
            require_checks()

        # This switch attests ONLY the local synthetic mock contract, not Frappe.
        config.DURABLE_CONFIRM_READS_VERIFIED = True
        config.DURABLE_SERVER_REVIEW_REFERENCE = 'SYNTHETIC MOCK CONTRACT ONLY - NOT PRODUCTION ATTESTATION'
        for read_mode in ('empty', 'null', 'positive'):
            CURRENT_PHASE = read_mode + '_confirmation'
            mode[0] = read_mode
            wait_until_due(Ledger, ledger)
            before_reads = len(reads)
            sync.run(engine)
            rows, observation = observe(CURRENT_PHASE)
            checks[read_mode + '_no_additional_post'] = accepted == payloads
            checks[read_mode + '_exact_confirmation_reads'] = reads[before_reads:] == ['employee', 'checkin_' + read_mode]
            if read_mode == 'positive':
                checks['positive_confirmation_resolves_uncertainty'] = (rows[0]['state'] == 'duplicate'
                    and rows[0]['error_category'] == 'destination_confirmed' and rows[0]['checkin_name'] == 'MOCK-CHECKIN-001'
                    and observation['uncertain'] == 0)
            else:
                checks[read_mode + '_not_positive_evidence'] = (rows[0]['state'] == 'retry'
                    and rows[0]['error_category'] == 'confirmation_pending' and observation['uncertain'] == 1)
            require_checks()

        CURRENT_PHASE = 'completed_cycle'
        previous_events = list(transport_events)
        sync.run(engine)
        rows, observation = observe(CURRENT_PHASE)
        checks['completed_records_not_resubmitted'] = transport_events == previous_events and [row['state'] for row in rows] == ['duplicate', 'synced']
        checks['one_post_per_source_despite_retries'] = accepted == payloads and len(documents) == 2
        checks['no_repoll_during_retry_cycles'] = len(polls) == 1
        require_checks()
    except Exception as error:
        failure = dict(phase=CURRENT_PHASE, category=type(error).__name__)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    checks['mock_stopped'] = not thread.is_alive()
    report = dict(status='PASS' if failure is None and all(checks.values()) else 'FAIL', checks=checks,
        failure=failure, mock_records=len(documents), post_count=len(accepted), confirmation_reads=len(reads),
        attempt=attempt.name, revision=base.REVISION, service_operations=False,
        scope='synthetic_localhost_foreground_timeout_confirmation_not_production_attestation')
    with (output / 'transport-events.json').open('x', encoding='utf-8') as stream:
        json.dump(transport_events, stream, indent=2)
    with (output / 'result.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--stage-root', type=Path, required=True)
    args = parser.parse_args()
    try:
        shared, base = helpers()
        report = run(args.stage_root, shared, base)
        print(json.dumps(report, sort_keys=True))
        return 0 if report['status'] == 'PASS' else 1
    except Exception as error:
        print(json.dumps(dict(status='FAIL', phase=CURRENT_PHASE, category=type(error).__name__,
                             details_suppressed=True, preserve_new_attempt=True)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
