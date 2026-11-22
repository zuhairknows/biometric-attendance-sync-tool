"""No network: synthetic controls and a fake HTTPS transport only."""
import ast
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import unittest
import warnings
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SPEC = importlib.util.spec_from_file_location(
    'get_only', Path(__file__).resolve().parents[1] / 'scripts' / 'verify_integration_get_only.py')
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def controls():
    return {
        'schema': 'phase1-get-controls-v1', 'expected_user': 'synthetic-integration',
        'administrator_reference': 'synthetic-review',
        'approved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'authoritative_full_sets': True, 'permission_scope_reviewed': True,
        'all_device_scope_reviewed': True,
        'employees': [{'employee_field_value': '001', 'authoritative_count': 1,
                       'expected_rows': [{'name': 'SYN-EMP', 'status': 'Active',
                                          'attendance_device_id': '001'}]}],
        'checkins': [{'employee': 'SYN-EMP', 'timestamp': '2026-10-14 12:00:00.987654',
                      'authoritative_count': 1,
                      'expected_rows': [{'name': 'SYN-CHECK', 'employee': 'SYN-EMP',
                                         'time': '2026-10-14 12:00:00', 'log_type': ''}]}]}


def responses(data):
    def get(path, authorization):
        if path == '/api/method/frappe.auth.get_logged_user':
            return {'message': data['expected_user']}
        kind = 'employees' if path.startswith('/api/resource/Employee?') else 'checkins'
        return {'data': copy.deepcopy(data[kind][0]['expected_rows'])}
    return get


class GetOnlyTests(unittest.TestCase):
    def test_exact_engine_fields_filters_and_identity(self):
        data = controls()
        calls = []
        def get(path, auth):
            calls.append(path)
            return responses(data)(path, auth)
        helper.verify(data, 'synthetic', get=get)
        self.assertEqual(len(calls), 3)
        employee = parse_qs(urlsplit(calls[1]).query)
        checkin = parse_qs(urlsplit(calls[2]).query)
        self.assertEqual(json.loads(employee['fields'][0]), helper.EMPLOYEE_FIELDS)
        self.assertEqual(json.loads(employee['filters'][0]), [['attendance_device_id', '=', '001']])
        self.assertEqual(json.loads(checkin['fields'][0]), helper.CHECKIN_FIELDS)
        self.assertEqual(json.loads(checkin['filters'][0]), [
            ['employee', '=', 'SYN-EMP'], ['time', '=', '2026-10-14 12:00:00'], ['log_type', '=', '']])
        self.assertEqual(checkin['limit_page_length'], ['2'])

    def test_identity_mismatch_blocks_resource_reads(self):
        calls = []
        def get(path, auth):
            calls.append(path)
            return {'message': 'wrong-account'}
        with self.assertRaises(ValueError):
            helper.verify(controls(), 'synthetic', get=get)
        self.assertEqual(len(calls), 1)

    def test_filtered_missing_or_extra_rows_fail(self):
        data = controls()
        for rows in ([], data['employees'][0]['expected_rows'] * 2,
                     [{'name': 'SYN-EMP', 'status': 'Active'}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                helper.compare_rows(rows, data['employees'][0]['expected_rows'], helper.EMPLOYEE_FIELDS)

    def test_null_is_not_blank_and_microseconds_are_not_discarded_from_responses(self):
        expected = controls()['checkins'][0]['expected_rows']
        for field, value in (('log_type', None), ('time', '2026-10-14 12:00:00.123')):
            actual = copy.deepcopy(expected)
            actual[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                helper.compare_rows(actual, expected, helper.CHECKIN_FIELDS)

    def test_missing_approval_or_stale_controls_block_network(self):
        for key in ('authoritative_full_sets', 'permission_scope_reviewed', 'all_device_scope_reviewed',
                    'approved_at', 'expected_user'):
            data = controls()
            data[key] = False if isinstance(data[key], bool) else ''
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                helper.verify(data, 'synthetic', get=lambda *args: self.fail('Network called'))
        data = controls()
        data['approved_at'] = '2020-01-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            helper.verify(data, 'synthetic', get=lambda *args: self.fail('Network called'))

    def test_unsupported_counts_and_no_existing_blank_control_fail(self):
        for change in ('count', 'rows', 'null', 'inactive', 'aware'):
            data = controls()
            if change == 'count':
                data['employees'][0]['authoritative_count'] = 2
            elif change == 'rows':
                data['employees'][0]['expected_rows'] *= 3
            elif change == 'null':
                data['checkins'][0]['expected_rows'][0]['log_type'] = None
            elif change == 'inactive':
                data['employees'][0]['expected_rows'][0]['status'] = 'Inactive'
            else:
                data['checkins'][0]['timestamp'] += '+00:00'
            with self.subTest(change=change), self.assertRaises(ValueError):
                helper.validate_controls(data)

    def test_ambiguous_mapping_compares_full_set(self):
        expected = [{'name': 'A', 'status': 'Active', 'attendance_device_id': '001'},
                    {'name': 'B', 'status': 'Inactive', 'attendance_device_id': '001'}]
        helper.compare_rows(list(reversed(expected)), expected, helper.EMPLOYEE_FIELDS)
        with self.assertRaises(ValueError):
            helper.compare_rows(expected[:1], expected, helper.EMPLOYEE_FIELDS)

    def test_ast_credentials_and_approved_host_only(self):
        source = b"ERPNEXT_API_KEY='synthetic-key'\nERPNEXT_API_SECRET='synthetic-secret'\nERPNEXT_URL='https://erp.myfpf.com'"
        self.assertEqual(helper.credentials(source), 'token synthetic-key:synthetic-secret')
        for extra in (b"\nERPNEXT_API_KEY='override'", b'\nimport os', b"\nexec('pass')"):
            with self.assertRaises(ValueError):
                helper.credentials(source + extra)
        for host in (b'http://erp.myfpf.com', b'https://evil.test', b'https://erp.myfpf.com.evil.test',
                     b'https://user:secret@erp.myfpf.com'):
            with self.assertRaises(ValueError):
                helper.credentials(source.replace(b'https://erp.myfpf.com', host))

    def test_transport_only_get_tls_and_no_redirect(self):
        class Response:
            status = 200
            def read(self, maximum):
                return b'{"message":"synthetic"}'
        class Connection:
            def __init__(self, host, **kwargs):
                self.host, self.options, self.calls, self.closed = host, kwargs, [], False
            def request(self, *args, **kwargs):
                self.calls.append((args, kwargs))
            def getresponse(self):
                return response
            def close(self):
                self.closed = True
        response = Response()
        connection = Connection(helper.HOST)
        with patch.object(helper.http.client, 'HTTPSConnection', return_value=connection) as factory:
            helper.get_json('/api/method/frappe.auth.get_logged_user', 'synthetic')
            self.assertEqual(factory.call_args.args, ('erp.myfpf.com',))
            self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
            self.assertEqual(factory.call_args.kwargs['context'].verify_mode, helper.ssl.CERT_REQUIRED)
            self.assertEqual(connection.calls[0][0][0], 'GET')
            response.status = 302
            with self.assertRaises(ValueError):
                helper.get_json('/api/method/frappe.auth.get_logged_user', 'synthetic')
            self.assertEqual(len(connection.calls), 2)
            self.assertTrue(connection.closed)
            response.status = 403
            with self.assertRaises(ValueError):
                helper.get_json('/api/method/frappe.auth.get_logged_user', 'synthetic')

    def test_errors_never_print_details(self):
        output = io.StringIO()
        with patch.object(helper, 'read_bytes', side_effect=RuntimeError('synthetic-secret')):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(helper.main(['local_config.py', 'controls.json']), 1)
        self.assertEqual(output.getvalue(), 'GET_VERIFICATION: FAIL_OR_UNVERIFIED\n')
        self.assertEqual(errors.getvalue(), '')

    def test_parser_warnings_cannot_expose_source(self):
        source = (b"ERPNEXT_API_KEY='synthetic\\qkey'\nERPNEXT_API_SECRET='synthetic-secret'\n"
                  b"ERPNEXT_URL='https://erp.myfpf.com'")
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter('always')
            helper.credentials(source)
        self.assertEqual(emitted, [])

    def test_no_write_open_or_integration_imports(self):
        tree = ast.parse(Path(helper.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == 'open':
                    self.assertEqual(node.args[0].value, 'rb')
                if node.func.attr == 'request':
                    self.assertEqual(node.args[0].value, 'GET')
            if isinstance(node, ast.Import):
                self.assertTrue(all(item.name in ('ast', 'datetime', 'http.client', 'json', 'ssl', 'sys', 'warnings')
                                    for item in node.names))
            if isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, ('pathlib', 'urllib.parse'))


if __name__ == '__main__':
    unittest.main()
