"""Standalone, bounded GET verification. Run using trusted Python -I -S -B.

No integration imports, configuration execution, redirects, proxies, or writes.
Administrator controls are private inputs, never printed. No insertion API exists.
"""
import ast
import datetime as dt
import http.client
import json
from pathlib import Path
import ssl
import sys
import warnings
from urllib.parse import urlencode


HOST = 'erp.myfpf.com'
MAX_BYTES = 1024 * 1024
EMPLOYEE_FIELDS = ['name', 'status', 'attendance_device_id']
CHECKIN_FIELDS = ['name', 'employee', 'time', 'log_type']


def require(condition):
    if not condition:
        raise ValueError()  # Never include input, response, or exception text.


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def read_bytes(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES)
    return data


def passive(node):
    # Same fail-closed syntax policy as credential_literal_preflight.py.
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(passive(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and passive(key) and passive(value)
                   for key, value in zip(node.keys, node.values))
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub))
            and isinstance(node.operand, ast.Constant))


def credentials(source):
    wanted = ('ERPNEXT_API_KEY', 'ERPNEXT_API_SECRET', 'ERPNEXT_URL')
    found = {}
    # Parser warnings can quote source fragments; never let them reach stderr.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        tree = ast.parse(source.decode('utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        require(isinstance(node, ast.Assign) and passive(node.value)
                and all(isinstance(target, ast.Name) for target in node.targets))
        for target in node.targets:
            if target.id in wanted:
                require(target.id not in found and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str) and node.value.value)
                found[target.id] = node.value.value
    require(set(found) == set(wanted))
    require(found['ERPNEXT_URL'] in ('https://' + HOST, 'https://' + HOST + '/'))
    for field in wanted[:2]:
        require(all(33 <= ord(char) <= 126 and char != ':' for char in found[field]))
    return 'token ' + found[wanted[0]] + ':' + found[wanted[1]]


def get_json(path, authorization):
    # Direct connection: no environment proxy/netrc, redirects, or URL credentials.
    require(path.startswith(('/api/resource/Employee?', '/api/resource/Employee%20Checkin?',
                             '/api/method/frappe.auth.get_logged_user')))
    connection = http.client.HTTPSConnection(HOST, port=443, timeout=30,
                                             context=ssl.create_default_context())
    try:
        connection.request('GET', path, headers={'Authorization': authorization,
                                                'Accept': 'application/json'})
        response = connection.getresponse()
        require(response.status == 200)  # In particular, never follow a 3xx.
        data = response.read(MAX_BYTES + 1)
        require(len(data) <= MAX_BYTES)
        result = json.loads(data, object_pairs_hook=unique_object)
        require(isinstance(result, dict))
        return result
    finally:
        connection.close()


def stamp(value):
    require(isinstance(value, str))
    parsed = dt.datetime.fromisoformat(value)
    require(parsed.tzinfo is None)
    return parsed


def validate_controls(controls):
    require(controls['schema'] == 'phase1-get-controls-v1')
    for key in ('expected_user', 'administrator_reference', 'approved_at'):
        require(isinstance(controls[key], str) and bool(controls[key].strip()))
    approved = dt.datetime.fromisoformat(controls['approved_at'])
    require(approved.tzinfo is not None
            and dt.timedelta(0) <= dt.datetime.now(dt.timezone.utc) - approved <= dt.timedelta(hours=24))
    # These statements must come from an independent administrator, not this tool.
    require(controls['authoritative_full_sets'] is True
            and controls['permission_scope_reviewed'] is True
            and controls['all_device_scope_reviewed'] is True)
    for kind, fields in (('employees', EMPLOYEE_FIELDS), ('checkins', CHECKIN_FIELDS)):
        cases = controls[kind]
        require(isinstance(cases, list) and 1 <= len(cases) <= 20)
        for case in cases:
            rows = case['expected_rows']
            require(isinstance(rows, list) and len(rows) <= 2)
            require(type(case['authoritative_count']) is int
                    and case['authoritative_count'] == len(rows))
            require(len({row['name'] for row in rows}) == len(rows))
            for row in rows:
                require(set(row) == set(fields))
                require(isinstance(row['name'], str) and 0 < len(row['name']) <= 140)
                if kind == 'employees':
                    require(row['attendance_device_id'] == case['employee_field_value']
                            and isinstance(row['status'], str))
                else:
                    require(row['employee'] == case['employee']
                            and stamp(row['time']) == stamp(case['timestamp']).replace(microsecond=0)
                            and row['log_type'] in ('', None))
            if kind == 'employees':
                require(isinstance(case['employee_field_value'], str)
                        and bool(case['employee_field_value']))
            else:
                require(isinstance(case['employee'], str) and bool(case['employee']))
                stamp(case['timestamp'])
    # At least one existing blank-log-type Checkin must be positively confirmed
    # through the same unique active employee mapping the engine would use.
    require(any(len(c['expected_rows']) == 1 and c['expected_rows'][0]['log_type'] == ''
                and any(len(e['expected_rows']) == 1
                        and e['expected_rows'][0]['name'] == c['employee']
                        and e['expected_rows'][0]['status'] == 'Active'
                        for e in controls['employees']) for c in controls['checkins']))


def compare_rows(actual, expected, fields):
    require(isinstance(actual, list) and len(actual) == len(expected))
    for row in actual:
        require(isinstance(row, dict) and set(row) == set(fields))
    def normalized(row):
        row = dict(row)
        if 'time' in row:
            row['time'] = str(stamp(row['time']))
        return row
    # Compare complete small sets, not first matches; preserve NULL versus blank.
    require(sorted((normalized(row) for row in actual), key=lambda row: row['name'])
            == sorted((normalized(row) for row in expected), key=lambda row: row['name']))


def verify(controls, authorization, get=get_json):
    validate_controls(controls)  # No network until all controls are approved/valid.
    require(get('/api/method/frappe.auth.get_logged_user', authorization).get('message')
            == controls['expected_user'])
    for kind, fields, path in (
            ('employees', EMPLOYEE_FIELDS, '/api/resource/Employee'),
            ('checkins', CHECKIN_FIELDS, '/api/resource/Employee%20Checkin')):
        for case in controls[kind]:
            if kind == 'employees':
                filters = [['attendance_device_id', '=', case['employee_field_value']]]
            else:
                filters = [['employee', '=', case['employee']],
                           ['time', '=', str(stamp(case['timestamp']).replace(microsecond=0))],
                           ['log_type', '=', '']]
            query = urlencode(dict(fields=json.dumps(fields), filters=json.dumps(filters),
                                   limit_page_length=2))
            rows = get(path + '?' + query, authorization).get('data')
            compare_rows(rows, case['expected_rows'], fields)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        require(len(argv) == 2 and Path(argv[0]).name.lower() == 'local_config.py')
        source = read_bytes(argv[0])
        authorization = credentials(source)
        controls_source = read_bytes(argv[1])
        controls = json.loads(controls_source, object_pairs_hook=unique_object)
        verify(controls, authorization)
        # Detect an on-disk edit during verification without copying or hashing secrets.
        require(read_bytes(argv[0]) == source and read_bytes(argv[1]) == controls_source)
    except Exception:
        print('GET_VERIFICATION: FAIL_OR_UNVERIFIED')
        return 1
    print('AUTHENTICATED_IDENTITY: PASS')
    print('EMPLOYEE_CONTROL_READS: PASS')
    print('CHECKIN_BLANK_LOG_CONTROL_READS: PASS')
    print('ADMINISTRATOR_CONTROL_SET_COMPLETENESS: PASS')
    print('LOADED_SERVICE_CREDENTIALS: UNVERIFIED')
    print('EFFECTIVE_SERVER_HOOKS: UNVERIFIED')
    return 0


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
