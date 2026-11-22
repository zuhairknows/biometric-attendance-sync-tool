"""Step 5C1: actual process crashes around a pinned synthetic ledger transaction.

Run only on the development PC. No service operations or device access. Reuses
the reviewed Step 5B HTTPS guard without changing its source or earlier evidence.
"""
import argparse
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import logging
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
from types import SimpleNamespace
import urllib.request


HELPER_HASH = '308f48abf741c0b2cb37cb7b75567c43a6c7df1b573ba48e4428021c85685483'
PHASES = ('provision', 'before_commit', 'after_commit', 'restart')
EXPECTED_EXITS = dict(provision=0, before_commit=71, after_commit=72, restart=0)
CURRENT_PHASE = 'preparation'


def helper():
    path = Path(__file__).with_name('phase1_staging_engine_https.py')
    if hashlib.sha256(path.read_bytes()).hexdigest() != HELPER_HASH:
        raise ValueError('step5b_helper_hash_mismatch')
    spec = importlib.util.spec_from_file_location('reviewed_step5b', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frozen_engine(attempt, base):
    for name, digest in base.HASHES.items():
        if hashlib.sha256((attempt / 'code' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('pinned_source_hash_mismatch')
    sys.path.insert(0, str(attempt / 'code'))
    import durable_sync
    import durable_startup
    from sync_ledger import Ledger
    return durable_sync, durable_startup, Ledger


def acl_script(startup):
    return next(v for v in startup.private_storage.__code__.co_consts
                if isinstance(v, str) and '$p=$env:FPF_LEDGER_DIRECTORY' in v)


def local_tls(stage):
    cert, key = stage / 'localhost-cert.pem', stage / 'localhost-key.pem'
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cert, key)
    client = ssl.create_default_context(cafile=str(cert))
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    return server, client


def activate_guard(policy):
    # Preserve the engine's real ACL verifier; only its exact command is allowed.
    os.environ['PATH'] = policy.powershell_directory
    os.chdir(policy.attempt / 'data')
    sys.addaudithook(policy.audit)


def crash_after_capture(frame, event, arg):
    if event == 'return' and frame.f_code is CAPTURE_CODE:
        # Hard termination: no Python finally blocks, SQLite close or lock release.
        os._exit(72)


def worker(args, base):
    attempt, stage = args.attempt.resolve(strict=True), args.stage_root.resolve(strict=True)
    if attempt.parent != stage or not attempt.name.startswith('step5c1-'):
        raise ValueError('invalid_worker_attempt')
    sync, startup, _ = frozen_engine(attempt, base)
    settings = json.loads((attempt / 'data' / 'scenario.json').read_text())
    origin = settings['origin']
    parsed = base.urllib.parse.urlsplit(origin)
    if parsed.scheme != 'https' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.netloc != '127.0.0.1:' + str(parsed.port):
        raise ValueError('invalid_mock_origin')
    _, client_tls = local_tls(stage)
    ledger = attempt / 'data' / 'synthetic.sqlite3'
    policy = base.Policy(attempt, stage / 'localhost-cert.pem', stage / 'localhost-key.pem', ledger, acl_script(startup))
    policy.port = parsed.port
    activate_guard(policy)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), base.NoRedirect(),
                                        urllib.request.HTTPSHandler(context=client_tls))
    request = base.transport(opener, origin)
    boundary = dt.datetime.fromisoformat(settings['boundary'])
    stamp = dt.datetime.fromisoformat(settings['stamp'])
    config = SimpleNamespace(DURABLE_DB_PATH=str(ledger), ERPNEXT_URL=origin, ERPNEXT_VERSION=15,
        ERPNEXT_API_KEY='SYNTHETIC', ERPNEXT_API_SECRET='SYNTHETIC', ERPNEXT_REQUEST_TIMEOUT=3,
        # Fast synthetic scan retry after the failed fetch; restart deliberately
        # uses a one-minute interval to prove delivery without another scan.
        IMPORT_START_DATE=None, PULL_FREQUENCY=1 if args.phase == 'restart' else 1 / 600,
        shift_type_device_mapping=[],
        devices=[dict(device_id='STAGE-DEVICE', ip='SYNTHETIC-NO-SOCKET', port=4370,
                      password=0, punch_direction=None, clear_from_device_on_fetch=False)])
    logger = logging.getLogger('step5c1')
    logger.addHandler(logging.NullHandler())
    logger.propagate = False

    def records(*unused, **kwargs):
        with (attempt / 'data' / (args.phase + '-fetch.json')).open('x', encoding='utf-8') as stream:
            json.dump(dict(synthetic_fetch=True), stream)
        if args.phase == 'restart':
            raise AssertionError('Restart must drain the persisted queue without repolling')
        yield dict(user_id='STAGE-SYNTHETIC-001', timestamp=stamp, punch=0, status=1, uid=1)
        if args.phase == 'before_commit':
            # The real Ledger.ingest has inserted the first row but its with-db
            # transaction has not committed. Stop at the next generator advance.
            os._exit(71)

    engine = SimpleNamespace(config=config, requests=SimpleNamespace(request=request),
        info_logger=logger, error_logger=logger, status=SimpleNamespace(usable=True),
        device_punch_values_IN=[0, 4], device_punch_values_OUT=[1, 5],
        validate_unique_device_ids=lambda devices: None, normalize_device_config=dict,
        get_dump_file_name_and_directory=lambda _: str(attempt / 'data' / 'unused-synthetic-dump'),
        get_all_attendance_from_device=records)
    if args.phase == 'provision':
        startup.provision(ledger, origin, 15, str(boundary), 'SYNTHETIC LOCALHOST STEP 5C1 ONLY')
    else:
        if args.phase == 'after_commit':
            global CAPTURE_CODE
            CAPTURE_CODE = sync.capture.__code__
            sys.setprofile(crash_after_capture)
        sync.run(engine)
        sys.setprofile(None)
    return 0


def inspect_ledger(Ledger, ledger, output, label):
    # Retain raw crash artifacts BEFORE SQLite legitimately recovers its journal.
    for path in (ledger, Path(str(ledger) + '-journal')):
        if path.exists():
            with (output / (label + '-' + path.name)).open('xb') as stream:
                stream.write(path.read_bytes())
    with Ledger(ledger) as db:
        db.verify_integrity()
        row = db.db.execute('SELECT COUNT(*) FROM punches').fetchone()
        states = dict(db.db.execute('SELECT state,COUNT(*) FROM punches GROUP BY state').fetchall())
        device = db.db.execute('SELECT fetch_ok FROM device_state WHERE device_id=?', ('STAGE-DEVICE',)).fetchone()
        watermark = db.safe_watermark(['STAGE-DEVICE'])
        observations = dict(count=row[0], states=states, fetch_ok=device[0] if device else None,
                            watermark=str(watermark) if watermark else None,
                            shift_rows=db.db.execute('SELECT COUNT(*) FROM shift_state').fetchone()[0], integrity='ok')
    with (output / (label + '.json')).open('x', encoding='utf-8') as stream:
        json.dump(observations, stream, indent=2)
    return observations


def run(stage, base):
    global CURRENT_PHASE
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise ValueError('isolated_python_required')
    # prepare() touches only a new attempt; rename only that newly created folder.
    created = base.prepare(stage, Path(__file__).resolve().parents[1])
    attempt = created.with_name(created.name.replace('step5b-', 'step5c1-', 1))
    created.rename(attempt)
    stage = stage.resolve()
    _, startup, Ledger = frozen_engine(attempt, base)
    server_tls, _ = local_tls(stage)
    ledger = attempt / 'data' / 'synthetic.sqlite3'
    output = attempt / 'data'
    script = Path(__file__).resolve()
    # Freeze the harness and helper too; children never depend on mutable repo code.
    script_bytes, helper_bytes = script.read_bytes(), Path(base.__file__).read_bytes()
    (attempt / 'code' / script.name).write_bytes(script_bytes)
    (attempt / 'code' / Path(base.__file__).name).write_bytes(helper_bytes)
    script = attempt / 'code' / script.name
    commands = {phase: [sys.executable, '-I', '-S', '-B', str(script), '--stage-root', str(stage),
                       '--attempt', str(attempt), '--phase', phase] for phase in PHASES}

    class ParentPolicy(base.Policy):
        def audit(self, event, args):
            if event == 'subprocess.Popen':
                executable, argv, cwd, env = args
                if (executable in (None, sys.executable) and cwd == str(output)
                        and any(argv in (cmd, subprocess.list2cmdline(cmd)) for cmd in commands.values())
                        and isinstance(env, dict) and env == child_environment):
                    return  # Only the frozen, isolated worker's four fixed commands.
            super().audit(event, args)

    policy = ParentPolicy(attempt, stage / 'localhost-cert.pem', stage / 'localhost-key.pem', ledger, acl_script(startup))
    activate_guard(policy)
    child_environment = dict(os.environ)
    accepted = []
    boundary = dt.datetime.now().replace(microsecond=0) + dt.timedelta(minutes=1)
    stamp = boundary + dt.timedelta(seconds=1)
    expected = dict(employee_field_value='STAGE-SYNTHETIC-001', timestamp=str(stamp),
                    device_id='STAGE-DEVICE', log_type=None, latitude=None, longitude=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            size = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(size)) if 0 < size <= 4096 else {}
            if self.path != base.METHOD or payload != expected:
                self.send_error(400)
                return
            accepted.append(payload)
            raw = b'{"message":{"name":"MOCK-CHECKIN-001"}}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = HTTPServer(('127.0.0.1', 0), Handler)
    policy.port = server.server_port
    server.socket = server_tls.wrap_socket(server.socket, server_side=True)
    origin = 'https://127.0.0.1:' + str(policy.port)
    with (output / 'scenario.json').open('x', encoding='utf-8') as stream:
        json.dump(dict(origin=origin, boundary=str(boundary), stamp=str(stamp)), stream)
    with (output / 'crash-provenance.json').open('x', encoding='utf-8') as stream:
        json.dump(dict(revision=base.REVISION, source_sha256=base.HASHES,
            crash_harness_sha256=hashlib.sha256(script_bytes).hexdigest(), helper_sha256=HELPER_HASH), stream, indent=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checks, exits, failure = {}, {}, None
    try:
        for phase in PHASES:
            CURRENT_PHASE = phase
            if phase == 'after_commit':
                # Ensure the 100ms synthetic retry interval has elapsed even on
                # very fast machines. No database timers are edited.
                threading.Event().wait(0.11)
            with (output / (phase + '-stdout.txt')).open('xb') as stdout, (output / (phase + '-stderr.txt')).open('xb') as stderr:
                process = subprocess.Popen(commands[phase], cwd=str(output), env=child_environment, stdout=stdout, stderr=stderr)
                try:
                    exits[phase] = process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    # Kill only our own exact worker handle; never find/kill by name.
                    process.kill()
                    process.wait(timeout=10)
                    raise ValueError('worker_timeout') from None
            checks[phase + '_exit_code'] = exits[phase] == EXPECTED_EXITS[phase]
            checks[phase + '_quiet_logs'] = all((output / (phase + suffix)).stat().st_size == 0 for suffix in ('-stdout.txt', '-stderr.txt'))
            if not checks[phase + '_exit_code'] or not checks[phase + '_quiet_logs']:
                raise ValueError('unexpected_worker_completion')
            observation = inspect_ledger(Ledger, ledger, output, phase)
            if phase == 'before_commit':
                checks['uncommitted_punch_rolled_back'] = observation['count'] == 0
                checks['rollback_journal_retained'] = (output / 'before_commit-synthetic.sqlite3-journal').is_file()
                checks['failed_scan_blocks_watermark'] = observation['fetch_ok'] == 0 and observation['watermark'] is None
                checks['no_post_before_commit'] = len(accepted) == 0
            elif phase == 'after_commit':
                checks['committed_pending_survived_crash'] = observation['states'] == {'pending': 1} and observation['fetch_ok'] == 1
                checks['pending_watermark_bounded'] = observation['watermark'] is not None and dt.datetime.fromisoformat(observation['watermark']) <= stamp - dt.timedelta(seconds=1)
                checks['no_post_after_capture_crash'] = len(accepted) == 0
            elif phase == 'restart':
                checks['restart_delivered_committed_punch'] = observation['states'] == {'synced': 1}
                checks['exactly_one_synthetic_submission'] = accepted == [expected]
                checks['restart_without_device_polling'] = observation['fetch_ok'] == 1 and not (output / 'restart-fetch.json').exists()
            checks[phase + '_integrity_and_no_shift_write'] = observation['integrity'] == 'ok' and observation['shift_rows'] == 0
            if not all(checks.values()):
                raise ValueError('crash_assertion_failed')
    except Exception as error:
        # Keep useful aggregate failure evidence, without exception strings or argv.
        failure = dict(phase=CURRENT_PHASE, category=type(error).__name__)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    checks['mock_stopped'] = not thread.is_alive()
    report = dict(status='PASS' if failure is None and all(checks.values()) else 'FAIL', checks=checks, worker_exit_codes=exits,
        mock_records=len(accepted), attempt=attempt.name, revision=base.REVISION,
        service_operations=False, failure=failure,
        scope='foreground_process_crash_and_persistence_not_timeout_or_power_loss')
    with (output / 'result.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--stage-root', type=Path, required=True)
    parser.add_argument('--attempt', type=Path)
    parser.add_argument('--phase', choices=PHASES)
    args = parser.parse_args()
    try:
        base = helper()
        if args.phase or args.attempt:
            if not (args.phase and args.attempt and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
                raise ValueError('invalid_worker_arguments')
            return worker(args, base)
        report = run(args.stage_root, base)
        print(json.dumps(report, sort_keys=True))
        return 0 if report['status'] == 'PASS' else 1
    except Exception as error:
        # Do not emit exception text, command lines, paths or configuration.
        if not args.phase:
            print(json.dumps(dict(status='FAIL', phase=CURRENT_PHASE, category=type(error).__name__,
                                  details_suppressed=True, preserve_new_attempt=True)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
