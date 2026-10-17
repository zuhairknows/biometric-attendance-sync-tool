"""Persistent synthetic ERPNext fixture. No sockets, requests library or live mode."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.parse import unquote

from .client import VerificationError


class SimulationTarget:
    def __init__(self, path, *, max_requests=128):
        self.path = Path(path).resolve(strict=True)
        self.data = json.loads(self.path.read_text(encoding='utf-8'))
        self.url = self.data['site_url']
        if self.data.get('synthetic_only') is not True or not self.url.startswith('https://') or not self.url.endswith('.example.test'):
            raise VerificationError('synthetic_destination_required')
        self.calls = []
        self.max_requests = max_requests
        self.config = SimpleNamespace(ERPNEXT_URL=self.url, ERPNEXT_VERSION=15,
            ERPNEXT_API_KEY='SYNTHETIC', ERPNEXT_API_SECRET='SYNTHETIC', ERPNEXT_REQUEST_TIMEOUT=10)

    def _save(self):
        # Fixtures persist remote commits before a simulated timeout/crash is raised.
        fd, temporary = tempfile.mkstemp(prefix='.recovery-simulation-', suffix='.part', dir=self.path.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(self.data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(self.path)

    def request(self, method, url, **options):
        if len(self.calls) >= self.max_requests:
            raise VerificationError('simulation_request_limit_exceeded')
        if not url.startswith(self.url + '/'):
            raise VerificationError('simulation_origin_mismatch')
        self.calls.append(method)
        if method == 'POST':
            if not url.endswith('/hrms.hr.doctype.employee_checkin.employee_checkin.add_log_based_on_employee_field'):
                raise VerificationError('unexpected_submission_endpoint')
            action = self.data.pop('next_send', 'ok')
            self._save()
            if action == 'timeout_before':
                raise TimeoutError
            if action == 'reject':
                return response(417, {'exception': 'Synthetic business rejection'})
            payload = options['json']
            matches = [e for e in self.data['employees'] if e['attendance_device_id'].casefold().rstrip() == payload['employee_field_value'].casefold().rstrip()]
            if len(matches) != 1 or matches[0]['status'] != 'Active':
                return response(417, {'exception': 'Synthetic ambiguous or inactive mapping'})
            key = (matches[0]['name'], payload['timestamp'], payload['log_type'] or '')
            if any((r['employee'], r['time'], r['log_type']) == key for r in self.data['checkins']):
                return response(417, {'exc_type': 'ValidationError', 'exception': 'This employee already has a log with the same timestamp'})
            name = 'SYNTHETIC_RECOVERED_' + str(len(self.data['checkins']) + 1)
            self.data['checkins'].append(dict(name=name, employee=key[0], time=key[1], log_type=key[2],
                device_id=payload['device_id'], modified='2026-01-01 00:00:00'))
            self._save()
            if action == 'crash_after':
                raise KeyboardInterrupt
            if action == 'timeout_after':
                raise TimeoutError
            return response(200, {'message': {'name': name}})
        if method != 'GET':
            raise VerificationError('unexpected_simulation_method')
        params = options['params']
        is_count = url.endswith('/api/method/frappe.client.get_count')
        doctype = params['doctype'] if is_count else unquote(url.rsplit('/', 1)[1])
        filters = json.loads(params['filters'])
        if doctype == 'Employee':
            user = filters[0][2]
            rows = [dict(e) for e in self.data['employees'] if e['attendance_device_id'].casefold().rstrip() == user.casefold().rstrip()]
        elif doctype == 'Employee Checkin':
            lower, upper = (dt.datetime.fromisoformat(f[2]) for f in filters)
            rows = [dict(r) for r in self.data['checkins'] if lower <= dt.datetime.fromisoformat(r['time']) <= upper]
        else:
            raise VerificationError('unexpected_simulation_doctype')
        if is_count:
            return response(200, {'message': len(rows)})
        rows.sort(key=lambda r: r['name'].casefold())
        offset, size = params['limit_start'], params['limit_page_length']
        return response(200, {'data': rows[offset:offset + size]})


def response(status, body):
    return SimpleNamespace(status_code=status, content=json.dumps(body).encode(), json=lambda: body, headers={})
