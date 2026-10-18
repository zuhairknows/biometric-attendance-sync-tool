"""Server-rendered local interface. Routes validate presentation inputs, not punch rules."""
import datetime as dt
import hmac
import json
from pathlib import Path
import re
import secrets

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from historical_attendance.engine import CATEGORIES
from historical_attendance.diagnostics import DiagnosticError
from historical_attendance.reporting import validate_output
from historical_attendance.recovery import file_hash
from scripts.attendance_reconciliation import local_time
from .controller import ManagerError
from .profile import device_catalog
from .state import State

ORIGIN = 'http://127.0.0.1:8765'
MONITOR_ENDPOINTS = frozenset({'dashboard_page', 'devices_page', 'refresh', 'job_page', 'job_status', 'static', 'favicon'})
INVESTIGATION_ENDPOINTS = MONITOR_ENDPOINTS | {'investigation_page', 'investigate', 'investigation_export',
    'offline_page', 'offline_run', 'offline_export'}


def create_app(controller, values, *, demo=False, monitor_only=False, investigation_only=False):
    if monitor_only and investigation_only:
        raise ValueError('startup_modes_are_mutually_exclusive')
    allowed_endpoints = MONITOR_ENDPOINTS if monitor_only else INVESTIGATION_ENDPOINTS if investigation_only else None
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_hex(32), DEBUG=False, TESTING=False,
        TRUSTED_HOSTS=['127.0.0.1'], SESSION_COOKIE_NAME='biometric_manager', SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict', MAX_CONTENT_LENGTH=32768, MAX_FORM_MEMORY_SIZE=32768,
        MAX_FORM_PARTS=150, PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=8))
    app.logger.disabled = True
    try:
        devices = device_catalog(values.get('device_config'))
        device_warning = '' if devices else 'Configured devices unavailable. Supply an approved device configuration in the startup profile.'
    except ManagerError:
        devices, device_warning = [], 'Device configuration could not be verified. No configuration code was executed.'
    state = State(controller, values, devices)
    app.extensions['manager'] = state

    @app.before_request
    def protect():
        # No proxy trust or alternative hostnames: prevents Host/DNS-rebinding pivots.
        if (request.host != '127.0.0.1:8765' or request.remote_addr != '127.0.0.1'
                or request.headers.get('Forwarded') or any(h.lower().startswith('x-forwarded-') for h in request.headers.keys())):
            abort(400)
        if request.method not in ('GET', 'HEAD', 'POST'):
            abort(405)
        # Default deny: adding a future route cannot silently expand a restricted mode.
        # Run before parsing forms/CSRF or invoking any route/business operation.
        if allowed_endpoints is not None and request.endpoint not in allowed_endpoints:
            abort(403)
        if request.method == 'POST':
            token = session.get('csrf', '')
            supplied = request.form.get('csrf', '')
            if (not token or not hmac.compare_digest(token, supplied)
                    or request.headers.get('Origin', ORIGIN) != ORIGIN
                    or request.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none')):
                abort(403)

    @app.after_request
    def headers(response):
        # no-referrer makes native form POSTs send Origin: null, failing our strict
        # Origin check. same-origin preserves local form origins without cross-origin referrers.
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()'})
        return response

    @app.before_request
    def offline_diagnostics():
        # protect() runs first: forbidden modes, Host and CSRF failures never
        # invoke offline logic or create diagnostic files.
        if request.endpoint in ('offline_page', 'offline_run', 'offline_export'):
            g.offline_trace = controller.offline_diagnostics(state.values)
            g.offline_trace.enter('request')

    @app.errorhandler(Exception)
    def safe_error(error):
        status = error.code if isinstance(error, HTTPException) else 400 if isinstance(error, (ManagerError, ValueError, DiagnosticError)) else 500
        if request.environ.get('HTTP_HOST') != '127.0.0.1:8765':
            # Host validation can fail before Flask creates a URL adapter.
            return 'Request rejected.', status
        diagnostic = error if isinstance(error, DiagnosticError) else getattr(error, 'diagnostic', None)
        trace = getattr(g, 'offline_trace', None)
        if trace and not isinstance(diagnostic, DiagnosticError):
            try:
                diagnostic = trace.fail(ValueError('request_rejected') if isinstance(error, HTTPException) else error)
            except DiagnosticError as log_error:
                diagnostic = log_error
        return render_template('error.html', code=status,
            diagnostic=diagnostic.message if isinstance(diagnostic, DiagnosticError) else None), status

    @app.context_processor
    def context():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
            session.permanent = True
        return dict(csrf=session['csrf'], demo=demo, monitor_only=monitor_only,
            investigation_only=investigation_only, allowed_endpoints=allowed_endpoints, devices=state.devices,
            device_warning=device_warning, active=request.endpoint, job=state.job,
            profile=state.values, dashboard=state.dashboard)

    def fields(allowed):
        if set(request.form) - (set(allowed) | {'csrf'}):
            raise ManagerError('unexpected_form_fields')
        for name in set(request.form) - {'devices', 'ids'}:
            if len(request.form.getlist(name)) != 1:
                raise ManagerError('duplicate_form_fields')

    def launch(label, task, accept, destination):
        identity = state.start(label, task, accept, destination)
        return redirect(url_for('job_page', identity=identity), code=303)

    def paginate(rows):
        try:
            page = int(request.args.get('page', '1'))
            size = int(request.args.get('size', '50'))
            if page < 1 or size not in (25, 50, 100):
                raise ValueError
            query = request.args.get('q', '')
            if len(query) > 100:
                raise ValueError
        except ValueError:
            abort(400)
        if query:
            rows = [r for r in rows if query.casefold() in ' '.join(str(v) for v in r.values()).casefold()]
        count = len(rows)
        pages = max(1, (count + size - 1) // size)
        page = min(page, pages)
        def link(number):
            args = request.args.to_dict()
            args['page'] = number
            return url_for(request.endpoint, **(request.view_args or {}), **args)
        return dict(rows=rows[(page-1)*size:page*size], count=count, page=page, pages=pages,
                    size=size, previous=link(max(1, page-1)), next=link(min(pages, page+1)))

    def batch_data(identity):
        if not re.fullmatch('[0-9a-f]{32}', identity):
            abort(404)
        if state.closed or not state.lock.acquire(blocking=False):
            abort(409)
        try:
            return controller.batch(state.values.get('workspace', ''), identity)
        finally:
            state.lock.release()

    def synthetic(data):
        # Presentation adds a synthetic-only approval gate; backend still checks all pins.
        if data['metadata']['contract'].get('destination_environment') != 'synthetic':
            raise ManagerError('synthetic_batches_only')

    @app.get('/')
    def dashboard_page():
        return render_template('dashboard.html')

    @app.get('/favicon.ico')
    def favicon():
        return '', 204  # No icon file or business operation is needed.

    @app.post('/refresh')
    def refresh():
        fields({'destination'})
        destination = request.form.get('destination', 'dashboard_page')
        if destination not in ('dashboard_page', 'devices_page'):
            abort(400)
        return launch('Read-only diagnostics', lambda: controller.dashboard(state.values.get('monitor_ledger', '')),
            lambda result: setattr(state, 'dashboard', result), url_for(destination))

    @app.get('/devices')
    def devices_page():
        observations = {row['device']: row for row in (state.dashboard or {}).get('devices', [])}
        catalog = [dict(d, observation=observations.get(d['device'])) for d in state.devices]
        unconfigured = sorted(set(observations) - {d['device'] for d in catalog})
        return render_template('devices.html', catalog=catalog, unconfigured=unconfigured)

    @app.get('/investigation')
    def investigation_page():
        result = state.investigation
        classification = request.args.get('classification', '')
        floor = request.args.get('floor', '')
        if classification and classification not in CATEGORIES or floor not in ('', 'FP1', 'FP2'):
            abort(400)
        # Filter searchable presentation fields only; identity values are hidden by default.
        show_ids = request.args.get('identifiers') == '1'
        keys = ['id', 'device_id', 'punch_timestamp', 'classification', 'reason']
        if show_ids:
            keys += ['employee', 'user_id']
        rows = [{k: row.get(k, '') for k in keys} for row in (result or {}).get('rows', [])
                if (not classification or row['classification'] == classification)
                and (not floor or row['device_id'].startswith(floor + '_'))]
        return render_template('investigation.html', result=result, categories=CATEGORIES,
            table=paginate(rows), show_ids=show_ids)

    @app.post('/investigation/run')
    def investigate():
        fields({'start', 'end', 'floor', 'devices', 'api_key', 'api_secret', 'backup_ack', 'production_ack'})
        start, end = (request.form.get(k, '').replace('T', ' ') for k in ('start', 'end'))
        start, end = (v + ':00' if len(v) == 16 else v for v in (start, end))
        if local_time(start) > local_time(end):
            abort(400)
        selected = request.form.getlist('devices')
        floor = request.form.get('floor', '')
        allowed = {d['device'] for d in state.devices if not floor or d['floor'] == floor}
        if (floor not in ('', 'FP1', 'FP2') or not selected or len(selected) != len(set(selected)) or not set(selected) <= allowed):
            abort(400)
        key, secret = request.form.get('api_key', ''), request.form.get('api_secret', '')
        if not key or not secret or max(len(key), len(secret)) > 256 or any(c in key + secret for c in '\r\n'):
            abort(400)
        values = dict(state.values, start=start, end=end)
        production, backup = request.form.get('production_ack') == '1', request.form.get('backup_ack') == '1'
        if not backup:
            abort(400)
        def accept(result):
            state.investigation = result
            state.values.update(start=start, end=end)
        # Clearing old results and starting must be serialized with every other action.
        def task():
            state.investigation = None
            return controller.investigate(values, selected, key, secret, production, backup)
        return launch('Historical investigation and protected export', task, accept, url_for('investigation_page'))

    @app.post('/investigation/export')
    def investigation_export():
        fields(set())
        result = state.investigation
        if not result:
            abort(409)
        def verify():
            validate_output(state.values['output'], ledger_path=state.values['backup'],
                input_paths=[state.values['contract']], acl_check=controller.acl_check)
            if file_hash(Path(result['csv_path'])) != result['summary']['reports'][0]['sha256']:
                raise ValueError
            if json.loads(Path(result['json_path']).read_text(encoding='utf-8')) != result['summary']:
                raise ValueError
        return launch('Verify protected CSV/JSON export', verify, lambda _: None, url_for('investigation_page'))

    @app.get('/investigation/offline')
    def offline_page():
        from historical_attendance.offline import choices
        diagnostic = g.offline_trace
        try:
            diagnostic.enter('import_directory')
            catalog = choices(state.values.get('offline_directory', ''), controller.acl_check)
            warning = ''
            diagnostic.emit('passed', 'ok')
        except Exception as error:
            catalog = dict(snapshots=[], csvs=[])
            warning = diagnostic.fail(error).message
        result = state.offline
        keys = ('id', 'device_id', 'punch_timestamp', 'classification', 'reason')
        classification = request.args.get('classification', '')
        if classification and classification not in CATEGORIES:
            abort(400)
        rows = [{k: r.get(k, '') for k in keys} for r in (result or {}).get('rows', [])
                if not classification or r['classification'] == classification]
        return render_template('offline.html', result=result, catalog=catalog, warning=warning,
                               categories=CATEGORIES, table=paginate(rows))

    @app.post('/investigation/offline/run')
    def offline_run():
        from historical_attendance.offline import choices
        diagnostic = g.offline_trace
        fields({'start', 'end', 'floor', 'devices', 'snapshot', 'employees', 'checkins', 'backup_ack'})
        start, end = (request.form.get(k, '').replace('T', ' ') for k in ('start', 'end'))
        start, end = (v + ':00' if len(v) == 16 else v for v in (start, end))
        selected, floor = request.form.getlist('devices'), request.form.get('floor', '')
        allowed = {d['device'] for d in state.devices if not floor or d['floor'] == floor}
        if (local_time(start) > local_time(end) or floor not in ('', 'FP1', 'FP2')
                or not selected or len(selected) != len(set(selected)) or not set(selected) <= allowed
                or request.form.get('backup_ack') != '1'):
            abort(400)
        diagnostic.enter('import_directory')
        catalog = choices(state.values.get('offline_directory', ''), controller.acl_check)
        diagnostic.enter('request')
        snapshot, employees, checkins = (request.form.get(k, '') for k in ('snapshot', 'employees', 'checkins'))
        if (snapshot not in catalog['snapshots'] or employees not in catalog['csvs']
                or checkins not in catalog['csvs'] or employees == checkins):
            abort(400)
        values = dict(state.values, start=start, end=end)
        def task():
            state.offline = None
            return controller.compare_offline(values, snapshot, employees, checkins, selected, diagnostic=diagnostic)
        return launch('Offline comparison and protected export', task,
            lambda result: setattr(state, 'offline', result), url_for('offline_page'))

    @app.post('/investigation/offline/export')
    def offline_export():
        diagnostic = g.offline_trace
        fields(set())
        result = state.offline
        if not result:
            abort(409)
        def verify():
            # Publication already happened atomically via the shared protected
            # exporter. This action verifies those exact files, never downloads them.
            from historical_attendance.offline import import_root, approved_file, digest
            with diagnostic.guard('import_directory'):
                root = import_root(state.values['offline_directory'], controller.acl_check)
                diagnostic.enter('evidence_path')
                manifest = approved_file(root, state.values['offline_evidence'], controller.acl_check)
                diagnostic.enter('evidence_hash')
                if digest(manifest) != result['summary']['evidence_file_sha256']:
                    raise ValueError('offline_inputs_changed')
                evidence = json.loads(manifest.read_text(encoding='utf-8-sig'))
                diagnostic.enter('input_recheck')
                inputs = [approved_file(root, entry['file'], controller.acl_check) for entry in evidence['files'].values()]
                if any(digest(path) != entry['sha256'] for path, entry in zip(inputs, evidence['files'].values())):
                    raise ValueError('offline_inputs_changed')
                diagnostic.enter('output')
                validate_output(state.values['output'], ledger_path=inputs[0], input_paths=[manifest, *inputs[1:]], acl_check=controller.acl_check)
                diagnostic.enter('export_verify')
                if (file_hash(Path(result['csv_path'])) != result['summary']['reports'][0]['sha256']
                        or json.loads(Path(result['json_path']).read_text(encoding='utf-8')) != result['summary']):
                    raise ValueError('completed_report_unverified')
                diagnostic.emit('complete', 'result_complete' if result['summary']['status'] == 'complete' else 'result_unverified')
        return launch('Verify offline protected CSV/JSON', verify, lambda _: None, url_for('offline_page'))

    @app.get('/recovery')
    def recovery_page():
        return render_template('recovery.html', table=paginate(state.batches))

    @app.post('/recovery/refresh')
    def refresh_batches():
        fields(set())
        def accept(rows):
            state.batches = [{k: r[k] for k in ('id', 'fingerprint', 'created')} for r in rows]
        return launch('Read existing recovery batches', lambda: controller.batches(state.values.get('workspace', '')),
                      accept, url_for('recovery_page'))

    @app.get('/recovery/<identity>')
    def batch_page(identity):
        data = batch_data(identity)
        rows = []
        show_ids = request.args.get('identifiers') == '1'
        for item in data['items']:
            source = json.loads(item['source'])
            row = dict(id=item['id'], device=source['device_id'], time=source['punch_timestamp'], status=item['status'], reason=item['reason'])
            if show_ids:
                row['employee'] = item['employee']
            rows.append(row)
        can_simulate = data['metadata']['contract'].get('destination_environment') == 'synthetic'
        # Bound audit presentation independently of selected-record pagination.
        return render_template('batch.html', batch=data, table=paginate(rows), show_ids=show_ids,
            events=data['events'][-100:], can_simulate=can_simulate)

    @app.post('/recovery/<identity>/<action>')
    def batch_action(identity, action):
        if action not in ('approve', 'simulate', 'export'):
            abort(404)
        fields({'fingerprint', 'ids', 'reference', 'confirmed'})
        data = batch_data(identity)
        values = state.values
        destination = url_for('batch_page', identity=identity)
        if action == 'export':
            return launch('Protected recovery audit export',
                lambda: controller.export_recovery(values.get('workspace', ''), identity, values.get('output', '')),
                lambda _: None, destination)
        synthetic(data)
        fingerprint = request.form.get('fingerprint', '')
        if request.form.get('confirmed') != '1' or fingerprint != data['fingerprint']:
            abort(400)
        if action == 'approve':
            raw = request.form.getlist('ids')
            reference = request.form.get('reference', '')
            ids = [int(value) for value in raw]
            pending = {r['id'] for r in data['items'] if r['status'] == 'Pending Approval'}
            if not ids or len(ids) > 100 or len(set(ids)) != len(ids) or not set(ids) <= pending or not reference.strip() or len(reference) > 120:
                abort(400)
            task = lambda: controller.approve(values['workspace'], identity, fingerprint, ids, reference)
        else:
            task = lambda: controller.simulate(values['workspace'], identity, fingerprint, values.get('simulation', ''), values.get('normal_lock', ''))
        return launch('Synthetic ' + action, task, lambda _: None, destination)

    @app.get('/jobs/<identity>')
    def job_page(identity):
        if not state.job or identity != state.job['id']:
            abort(404)
        return render_template('job.html', current_job=state.job)

    @app.get('/jobs/<identity>/status')
    def job_status(identity):
        if not state.job or identity != state.job['id']:
            abort(404)
        result = dict(status=state.job['status'])
        if state.job['status'] == 'blocked':
            result['error'] = state.job['error']
        return jsonify(result)

    return app
