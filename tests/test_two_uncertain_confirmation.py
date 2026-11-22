"""Synthetic fixed-batch confirmation, without network or production evidence."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from durable_confirmation import (APPROVED, BOUNDARY, COOKIE, DESTINATION,
                                   ConfirmationLedger, reconcile)
from punch_normalization import normalize


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'synthetic.sqlite3'
        self.config = SimpleNamespace(ERPNEXT_URL='https://erp.myfpf.com', ERPNEXT_VERSION=15,
                                      DURABLE_CONFIRM_READS_VERIFIED=False)
        with ConfirmationLedger(self.path) as ledger:
            ledger.db.execute('PRAGMA application_id=' + str(COOKIE))
            for index, record_id in enumerate(APPROVED):
                device = dict(device_id='SYNTHETIC', punch_direction=None)
                punch = dict(user_id='SYNTHETIC-' + str(index), uid=index,
                             timestamp=dt.datetime(2026, 10, 17, 9, 0, index), punch=0, status=1)
                ledger.ingest([normalize(punch, device)], 'SYNTHETIC')
                with ledger.db:
                    ledger.db.execute("UPDATE punches SET id=?,state='retry',error_category='confirmation_pending',"
                                      'attempt_count=1 WHERE user_id=?', (record_id, punch['user_id']))
        self.marker = Path(str(self.path) + '.activation.json')
        self.marker.write_text(json.dumps(dict(schema='durable-activation-v1', ledger_cookie=COOKIE,
            destination_sha256=DESTINATION, cutover_time=BOUNDARY)), encoding='utf-8')
        self.calls = []

    def get(self, doctype, fields, filters):
        self.calls.append((doctype, fields, filters))
        # No device filter: detect duplicate identities across every device.
        self.assertFalse(any(f[0] == 'device_id' for f in filters))
        if doctype == 'Employee':
            self.index = int(filters[0][2].split('-')[-1])
            return [dict(name='SYNTHETIC-EMP', status='Active', attendance_device_id=filters[0][2])]
        return [dict(name=list(APPROVED.values())[self.index], employee='SYNTHETIC-EMP',
                     time=filters[1][2], log_type='', device_id='SYNTHETIC')]

    def test_success_and_idempotency(self):
        self.assertEqual(set(reconcile(self.path, self.config, self.get, True).values()), {'CONFIRMED'})
        self.assertEqual(set(reconcile(self.path, self.config, self.get, True).values()), {'ALREADY_CONFIRMED'})
        with ConfirmationLedger(self.path, readonly=True) as ledger:
            self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM delivery_attempts').fetchone()[0], 2)
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM punches WHERE state='duplicate'").fetchone()[0], 2)
        self.assertFalse(self.config.DURABLE_CONFIRM_READS_VERIFIED)

    def test_preview_no_changes(self):
        before = self.path.read_bytes()
        reconcile(self.path, self.config, self.get)
        self.assertEqual(self.path.read_bytes(), before)

    def test_mismatches_and_duplicate_sets(self):
        for field, value in [('name', 'WRONG'), ('employee', 'WRONG'), ('time', '2026-10-17 09:20:00'),
                             ('log_type', None), ('device_id', 'OTHER')]:
            with self.subTest(field=field):
                def wrong(*args):
                    rows = self.get(*args)
                    if args[0] == 'Employee Checkin':
                        rows[0][field] = value
                    return rows
                with self.assertRaises(ValueError):
                    reconcile(self.path, self.config, wrong, True)
        def duplicates(*args):
            rows = self.get(*args)
            return rows * 2
        with self.assertRaises(ValueError):
            reconcile(self.path, self.config, duplicates, True)

    def test_concurrent_identity_state_and_lease_changes(self):
        record_id = next(iter(APPROVED))
        for column, value in [('payload', '{}'), ('state', 'delivering'), ('lease_until', 99999999999),
                              ('attempt_count', 20), ('identity_key', 'CHANGED')]:
            with self.subTest(column=column):
                with ConfirmationLedger(self.path) as ledger:
                    original = ledger.db.execute('SELECT * FROM punches WHERE id=?', (record_id,)).fetchone()[column]
                def racing(*args):
                    rows = self.get(*args)
                    if args[0] == 'Employee Checkin':
                        with ConfirmationLedger(self.path) as other, other.db:
                            other.db.execute('UPDATE punches SET ' + column + '=? WHERE id=?', (value, record_id))
                    return rows
                with self.assertRaises(ValueError):
                    reconcile(self.path, self.config, racing, True)
                with ConfirmationLedger(self.path) as ledger, ledger.db:
                    ledger.db.execute('UPDATE punches SET ' + column + '=? WHERE id=?', (original, record_id))

    def test_invalid_binding_no_get(self):
        self.marker.write_text('{}')
        with self.assertRaises(Exception):
            reconcile(self.path, self.config, self.get, True)
        self.assertEqual(self.calls, [])

    def test_transport_only_get(self):
        # Exercise the actual standalone transport, replacing only the TLS socket.
        from unittest import mock
        spec = importlib.util.spec_from_file_location('get_transport',
            Path(__file__).resolve().parents[1] / 'scripts' / 'verify_integration_get_only.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        connection = mock.Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'{"data":[]}'
        with mock.patch.object(module.http.client, 'HTTPSConnection', return_value=connection):
            module.get_json('/api/resource/Employee?', 'SYNTHETIC')
            self.assertEqual(connection.request.call_args.args[0], 'GET')
            with self.assertRaises(ValueError):
                module.get_json('/api/method/hrms.insert', 'SYNTHETIC')
        for filename in ('durable_confirmation.py', 'scripts/confirm_two_uncertain.py'):
            source = (Path(__file__).resolve().parents[1] / filename).read_text()
            self.assertNotIn('deliver_batch(', source)
            self.assertNotIn('.send(', source)

    def test_standalone_with_pinned_runtime_and_mock_https(self):
        import subprocess
        import sys
        from unittest import mock
        from urllib.parse import parse_qs, urlsplit
        root = Path(__file__).resolve().parents[1]
        runtime = Path(self.temp.name) / 'runtime'
        runtime.mkdir()
        for name in ('sync_ledger.py', 'durable_startup.py'):
            (runtime / name).write_bytes(subprocess.check_output(
                ['git', 'show', '4256cd6201b17dcda3f9d078552acf1d3c58b3f0:' + name], cwd=root))
        bundle = Path(self.temp.name) / 'tools'
        bundle.mkdir()
        for source in ('scripts/confirm_two_uncertain.py', 'durable_confirmation.py',
                       'scripts/verify_integration_get_only.py'):
            (bundle / Path(source).name).write_bytes((root / source).read_bytes())
        config = Path(self.temp.name) / 'local_config.py'
        config.write_text("ERPNEXT_URL='https://erp.myfpf.com'\nERPNEXT_VERSION=15\n"
                          "ERPNEXT_API_KEY='SYNTHETIC'\nERPNEXT_API_SECRET='SYNTHETIC'\n"
                          'DURABLE_CONFIRM_READS_VERIFIED=False\n')
        spec = importlib.util.spec_from_file_location('confirmation_cli', bundle / 'confirm_two_uncertain.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        connection = mock.Mock()
        response = connection.getresponse.return_value
        response.status = 200
        def request(method, path, **kwargs):
            self.assertEqual(method, 'GET')
            if path.startswith('/api/method/frappe.auth.get_logged_user'):
                data = dict(message='attendance.sync@myfpf.com')
            else:
                query = parse_qs(urlsplit(path).query)
                filters = json.loads(query['filters'][0])
                fields = json.loads(query['fields'][0])
                doctype = 'Employee Checkin' if '%20Checkin' in path else 'Employee'
                data = dict(data=self.get(doctype, fields, filters))
            response.read.return_value = json.dumps(data).encode()
        connection.request.side_effect = request
        # Only ACL inspection is mocked; all activation, pinned imports, GET identity
        # confirmation and audited ledger transitions execute on synthetic fixtures.
        def load_spec(name, path):
            result = original_spec(name, path)
            original_exec = result.loader.exec_module
            def execute(module):
                original_exec(module)
                if name == 'durable_startup':
                    module.private_storage = lambda path: None
            result.loader.exec_module = execute
            return result
        original_spec = importlib.util.spec_from_file_location
        with mock.patch.dict(sys.modules), mock.patch('http.client.HTTPSConnection', return_value=connection), \
                mock.patch.object(importlib.util, 'spec_from_file_location', side_effect=load_spec):
            self.assertEqual(cli.main(['--runtime-root', str(runtime), '--config', str(config),
                                      '--db', str(self.path), '--apply-approved-two']), 0)


if __name__ == '__main__':
    unittest.main()
