"""Server-side transport tests. Fake Bench tree/DB/kernel; no production execution."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_frappe_attendance_export as existing
from reconciliation_fixtures import dump, sha, START, END
from scripts import export_frappe_attendance_server as server_cli
from scripts import frappe_server_transport as server
from scripts import frappe_export_review as review
from scripts import attendance_reconciliation as reconciliation
from scripts import reconcile_attendance


class FakeHost:
    def __init__(self):
        self.inode = 22
        self.fail_peer = False
        self.fail_private = False

    def runtime(self, bench_root):
        pass

    def socket_identity(self, profile):
        return dict(device=1, inode=self.inode, owner=profile['mysql_uid'])

    def private_directory(self, path):
        if self.fail_private:
            raise reconciliation.ReconciliationError('private_output_owner_or_mode_invalid')
        return Path(path).resolve()

    def peer(self, connection, profile):
        if self.fail_peer:
            raise reconciliation.ReconciliationError('database_socket_peer_not_approved')
        return dict(peer_uid_verified=True, kernel_peer_credentials=True)


class ServerConnection(existing.Connection):
    def __init__(self):
        super().__init__()
        self.server_version = '10.11.10-MariaDB-ubuntu'

    def execute(self, key, args):
        if key == 'server_version':
            self.calls.append((key, args))
            return [dict(server_version=self.server_version)]
        return super().execute(key, args)


class ServerExportTests(unittest.TestCase):
    # Reuse only the synthetic setup, not the old transport's test cases.
    save_review = existing.ExportTests.save_review

    def setUp(self):
        existing.ExportTests.setUp(self)
        self.host = FakeHost()
        self.bench = self.root / 'bench'
        (self.bench / 'sites' / 'synthetic.local').mkdir(parents=True)
        (self.bench / 'env').mkdir()
        self.connection = ServerConnection()
        self.connector = mock.Mock(return_value=self.connection)
        module = self.review['endpoint_path'].rsplit('.', 1)[0]
        files = dict(employee_checkin='apps/hrms/' + module.replace('.', '/') + '.py',
            frappe_database='apps/frappe/frappe/database/database.py',
            frappe_query='apps/frappe/frappe/database/query.py')
        for role, relative in files.items():
            path = self.bench / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((self.evidence / self.review['files'][role]['file']).read_bytes())
        version = self.bench / 'apps/frappe/frappe/__init__.py'
        version.write_text('__version__ = "15.42.0"\n')
        hook_hashes = {}
        for app in ('frappe', 'hrms'):
            path = self.bench / 'apps' / app / app / 'hooks.py'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic deployed hooks; no application imports\n')
            hook_hashes[app] = sha(path)
        dump(self.profile, dict(unix_socket=str(self.root / 'mysql.sock'), database='syntheticdb', user='synthetic', mysql_uid=120))
        self.review['server_internal'] = dict(transport='local_unix_socket', bench_root=str(self.bench.resolve()), site='synthetic.local',
            confirmed_aliases=list(server.SITE_ALIASES), frappe_major=15, runtime_hooks_reviewed=True,
            version_source_sha256=sha(version), hook_file_sha256=hook_hashes,
            internal_profile_sha256=review.fingerprint(json.loads(self.profile.read_text())))
        self.save_review()

    def args(self, live=True):
        return ['--bench-root', str(self.bench), '--site', 'synthetic.local', '--internal-profile', str(self.profile),
                *existing.ExportTests.args(self, live=False)] + (['--live-export', '--operator-authorized'] if live else [])

    def run_server(self, args=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = server_cli.main(self.args() if args is None else args, connector=self.connector,
                password_reader=self.password, host=self.host)
        return result, output.getvalue()

    def test_default_plan_no_db_or_credentials_or_output(self):
        result, output = self.run_server(self.args(False))
        self.assertEqual(result, 0, output)
        self.connector.assert_not_called()
        self.password.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse(json.loads(output)['certification_issued'])

    def test_success_exact_export_contract_and_reconciliation(self):
        result, console = self.run_server()
        self.assertEqual(result, 0, console)
        manifest_path = self.output / 'export-manifest.json'
        contract, mappings, checkins, _, _ = reconciliation.load_exports(manifest_path, sha(manifest_path), START, END, 'Asia/Riyadh')
        self.assertEqual(mappings['001'][0]['attendance_device_id'], '001')
        self.assertEqual(mappings['002'][0]['status'], 'Inactive')
        self.assertEqual([r['device_id'] for r in checkins], ['OTHER_DEVICE', ''])
        self.assertEqual(checkins[1]['time'], '2026-10-15 17:00:00.123456')
        self.assertTrue(contract['transport_evidence']['peer_uid_verified'])
        self.assertFalse(contract['transport_evidence']['frappe_connection_used'])
        self.assertFalse(contract['transport_evidence']['external_database_access_required'])
        self.assertEqual(contract['completeness']['exported_rows'], 2)
        report_dir = self.root / 'reports'
        report_dir.mkdir()
        args = ['--capture-summary', str(self.cap), '--capture-summary-sha256', sha(self.cap),
                '--exports-manifest', str(manifest_path), '--exports-manifest-sha256', sha(manifest_path), '--output', str(report_dir)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(reconcile_attendance.main(args, acl_check=lambda _: None), 0)
        self.assertEqual(self.connection.rollback_count, 1)
        self.assertTrue(self.connection.closed)
        self.assertNotIn('tls', [key for key, _ in self.connection.calls])

    def test_duplicate_and_inactive_mappings_are_not_filtered(self):
        self.connection.employees.append(dict(employee='SYNTH-OTHER', attendance_device_id='001', status='Inactive'))
        self.assertEqual(self.run_server()[0], 0)
        mappings = json.loads((self.output / 'employee-mappings.json').read_text())
        self.assertEqual(len(mappings['lookups'][0]['matches']), 2)

    def test_snapshot_consistency_with_concurrent_writer(self):
        self.connection.concurrent_write = True
        self.assertEqual(self.run_server()[0], 0)
        self.assertEqual(len(self.connection.live_checkins), 3)
        self.assertEqual(sum(k == 'begin' for k, _ in self.connection.calls), 1)
        evidence = json.loads((self.output / 'export-manifest.json').read_text())['completeness']
        self.assertEqual(evidence['independent_count_before'], evidence['independent_count_after'])

    def test_independent_counts_and_incomplete_pages_block(self):
        self.connection.drop_page = True
        self.assertEqual(self.run_server()[0], 1)
        self.assertFalse((self.output / 'export-manifest.json').exists())
        self.assertTrue(self.connection.closed)

    def test_independent_mapping_count_blocks(self):
        self.connection.mapping_delta = 1
        self.assertEqual(self.run_server()[0], 1)

    def test_readonly_state_and_write_privileges_block(self):
        self.connection.state['read_only'] = 0
        self.assertEqual(self.run_server()[0], 1)
        self.connection.state['read_only'] = 1
        self.connection.grants = ['GRANT ALL PRIVILEGES ON `syntheticdb`.* TO `x`@`h`']
        self.assertEqual(self.run_server()[0], 1)

    def test_deployed_source_change_blocks_before_connection(self):
        path = self.bench / 'apps/frappe/frappe/database/database.py'
        path.write_text('# changed after review')
        self.assertEqual(self.run_server()[0], 1)
        self.connector.assert_not_called()

    def test_deployed_hook_change_blocks_before_connection(self):
        (self.bench / 'apps/hrms/hrms/hooks.py').write_text("doc_events={'*': {'validate': 'unknown'}}")
        self.assertEqual(self.run_server()[0], 1)
        self.connector.assert_not_called()

    def test_deployed_source_change_during_export_prevents_certification(self):
        original = self.connection.rollback
        def rollback():
            original()
            (self.bench / 'apps/hrms/hrms/hooks.py').write_text('# deployment changed mid-export')
        self.connection.rollback = rollback
        self.assertEqual(self.run_server()[0], 1)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_wrong_bench_site_or_alias_evidence_blocks(self):
        for field, value in (('site', 'other'), ('confirmed_aliases', ['erp.myfpf.com']), ('runtime_hooks_reviewed', False)):
            with self.subTest(field=field):
                original = self.review['server_internal'][field]
                self.review['server_internal'][field] = value
                self.save_review()
                self.assertEqual(self.run_server()[0], 1)
                self.review['server_internal'][field] = original
        self.connector.assert_not_called()

    def test_server_version_and_frappe_major_are_bounded(self):
        self.connection.server_version = '8.0.36-MySQL'
        self.assertEqual(self.run_server()[0], 1)
        self.connection.server_version = '10.11.10-MariaDB'
        (self.bench / 'apps/frappe/frappe/__init__.py').write_text('__version__ = "16.0.0"')
        self.assertEqual(self.run_server()[0], 1)

    def test_frappe_context_is_rejected_without_touching_its_db(self):
        with mock.patch.dict(sys.modules, frappe=types.SimpleNamespace()):
            self.assertEqual(self.run_server()[0], 1)
        self.connector.assert_not_called()

    def test_live_authorization_and_internal_profile_only(self):
        args = self.args()
        args.remove('--operator-authorized')
        self.assertEqual(self.run_server(args)[0], 1)
        self.assertEqual(self.run_server(self.args() + ['--db-profile', 'PRIVATE'])[0], 1)
        self.connector.assert_not_called()

    def test_private_storage_failure_prevents_connection(self):
        self.host.fail_private = True
        self.assertEqual(self.run_server()[0], 1)
        self.connector.assert_not_called()

    def test_output_under_bench_refused(self):
        self.output = self.bench / 'sites/synthetic.local/private'
        self.output.mkdir()
        self.assertEqual(self.run_server()[0], 1)
        self.connector.assert_not_called()

    def test_invalid_peer_and_socket_replacement_block(self):
        self.host.fail_peer = True
        self.assertEqual(self.run_server()[0], 1)
        self.host.fail_peer = False
        def connect(*args):
            self.host.inode += 1
            return self.connection
        self.connector.side_effect = connect
        self.assertEqual(self.run_server()[0], 1)

    def test_cleanup_failure_never_publishes_manifest(self):
        self.connection.fail = 'rollback'
        self.assertEqual(self.run_server()[0], 1)
        self.assertFalse((self.output / 'export-manifest.json').exists())

    def test_no_mutations_or_personal_console_output(self):
        code, console = self.run_server()
        self.assertEqual(code, 0, console)
        self.assertFalse(hasattr(self.connection, 'commit'))
        for private in ('SYNTH-EMP', 'SYNTH-C-', 'PRIVATE', 'syntheticdb', 'mysql.sock', str(self.bench)):
            self.assertNotIn(private, console)
        self.assertTrue(all(key not in ('insert', 'update', 'delete') for key, _ in self.connection.calls))

    def test_umask_restored_on_failure(self):
        with mock.patch.object(server_cli.os, 'umask', side_effect=[0o022, 0o077]) as umask:
            self.host.fail_private = True
            self.assertEqual(self.run_server()[0], 1)
        self.assertEqual(umask.call_args_list, [mock.call(0o077), mock.call(0o022)])

    def test_original_server_review_null_cannot_authorize_internal_transport(self):
        self.review['server_internal'] = None
        self.save_review()
        self.assertEqual(self.run_server()[0], 1)
        self.connector.assert_not_called()

    def test_hook_override_and_unknown_source_still_block(self):
        hooks = json.loads(self.hooks.read_text())
        hooks['override_doctype_class']['Employee Checkin'] = 'unknown.Controller'
        dump(self.hooks, hooks)
        self.review['files']['effective_hooks']['sha256'] = sha(self.hooks)
        self.save_review()
        self.assertEqual(self.run_server()[0], 1)
        self.connector.assert_not_called()

    def test_private_output_permission_change_after_read_blocks_publication(self):
        original = self.connection.rollback
        def rollback():
            original()
            self.host.fail_private = True
        self.connection.rollback = rollback
        self.assertEqual(self.run_server()[0], 1)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_expired_review_stops_further_snapshot_queries(self):
        snapshot = server.InternalSnapshot(self.connection, 'syntheticdb', self.review, self.review['semantics'], {},
            transport=mock.Mock())
        self.review['expires_at'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat()
        with self.assertRaises(reconciliation.ReconciliationError):
            snapshot.query('mapping', ('001',))
        self.assertEqual(self.connection.calls, [])

    def test_complete_export_process_has_no_frappe_engine_http_or_device_imports(self):
        script = '''
import sys
sys.path.insert(0, 'tests')
import test_frappe_server_export as fixtures
class Guard:
    def find_spec(self, name, *args):
        if name.split('.')[0] in {'frappe','requests','zk','local_config','erpnext_sync','erpnext_sync_win','SMWinservice','sqlite3','sync_ledger','durable_sync','durable_delivery'}:
            raise RuntimeError('forbidden import')
sys.meta_path.insert(0, Guard())
from scripts.export_frappe_attendance_server import main
result = main(sys.argv[1:], connector=lambda profile,password: fixtures.ServerConnection(),
              password_reader=lambda prompt: 'SYNTHETIC', host=fixtures.FakeHost())
assert result == 0
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, *self.args()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_publication_failure_leaves_no_success_manifest(self):
        with mock.patch('scripts.export_frappe_attendance.publish', side_effect=OSError('PRIVATE')):
            code, console = self.run_server()
        self.assertEqual(code, 1)
        self.assertNotIn('PRIVATE', console)
        self.assertFalse((self.output / 'export-manifest.json').exists())


class LinuxGuardTests(unittest.TestCase):
    def test_private_directory_requires_owner_and_exact_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve()
            host = server.LinuxHost()
            for mode, uid in ((0o755, 100), (0o700, 999), (0o770, 100)):
                with self.subTest(mode=mode, uid=uid), mock.patch.object(host, 'require_platform'), \
                        mock.patch.object(os, 'geteuid', return_value=100, create=True), \
                        mock.patch.object(os, 'lstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=uid)):
                    with self.assertRaises(reconciliation.ReconciliationError):
                        host.private_directory(path)
            def metadata(item):
                return types.SimpleNamespace(st_mode=stat.S_IFDIR | (0o700 if Path(item) == path else 0o755), st_uid=100)
            with mock.patch.object(host, 'require_platform'), \
                    mock.patch.object(os, 'geteuid', return_value=100, create=True), \
                    mock.patch.object(os, 'lstat', side_effect=metadata), \
                    mock.patch.object(os, 'access', return_value=True), \
                    mock.patch.object(server.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=1024**3)):
                self.assertEqual(host.private_directory(path), path)

    def test_server_interpreter_must_belong_to_approved_bench(self):
        with tempfile.TemporaryDirectory() as temporary:
            bench = Path(temporary).resolve()
            (bench / 'env').mkdir()
            host = server.LinuxHost()
            with mock.patch.object(host, 'require_platform'), mock.patch.object(sys, 'prefix', str(bench / 'env')):
                host.runtime(bench)
            with mock.patch.object(host, 'require_platform'), mock.patch.object(sys, 'prefix', str(bench)):
                with self.assertRaises(reconciliation.ReconciliationError):
                    host.runtime(bench)

    def test_socket_type_and_owner_require_real_daemon_socket(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'mysql.sock'
            path.write_text('synthetic only; kernel metadata below is mocked')
            profile = dict(unix_socket=str(path), mysql_uid=120)
            host = server.LinuxHost()
            for mode, uid in ((stat.S_IFREG | 0o600, 120), (stat.S_IFSOCK | 0o777, 999)):
                with mock.patch.object(host, 'require_platform'), \
                        mock.patch.object(os, 'lstat', return_value=types.SimpleNamespace(st_mode=mode, st_uid=uid)):
                    with self.assertRaises(reconciliation.ReconciliationError):
                        host.socket_identity(profile)

    def test_ancestor_write_permission_and_symlink_rejected(self):
        path = Path('C:/synthetic/output') if os.name == 'nt' else Path('/synthetic/output')
        for mode, uid in ((stat.S_IFDIR | 0o777, 100), (stat.S_IFLNK | 0o755, 100), (stat.S_IFDIR | 0o755, 999)):
            with self.subTest(mode=mode, uid=uid):
                with self.assertRaises(reconciliation.ReconciliationError):
                    server.controlled_ancestors(path, {0, 100}, lambda _: types.SimpleNamespace(st_mode=mode, st_uid=uid))
        server.controlled_ancestors(path, {0, 100}, lambda _: types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0))

    def test_kernel_peer_requires_unix_socket_and_expected_daemon_uid(self):
        import socket
        sock = mock.Mock(family=1)
        sock.getpeername.return_value = '/run/mysql/mysql.sock'
        sock.getsockopt.return_value = struct.pack('3i', 42, 120, 120)
        host = server.LinuxHost()
        with mock.patch.object(host, 'require_platform'), mock.patch.object(socket, 'AF_UNIX', 1, create=True), \
                mock.patch.object(socket, 'SO_PEERCRED', 17, create=True):
            result = host.peer(types.SimpleNamespace(_sock=sock), dict(unix_socket='/run/mysql/mysql.sock', mysql_uid=120))
            self.assertTrue(result['peer_uid_verified'])
            sock.getsockopt.return_value = struct.pack('3i', 42, 999, 120)
            with self.assertRaises(reconciliation.ReconciliationError):
                host.peer(types.SimpleNamespace(_sock=sock), dict(unix_socket='/run/mysql/mysql.sock', mysql_uid=120))

    def test_internal_driver_never_uses_site_credentials_or_tcp(self):
        factory = mock.Mock()
        module = types.SimpleNamespace(connect=factory, cursors=types.SimpleNamespace(DictCursor=object()))
        profile = dict(unix_socket='/run/mysql/mysql.sock', user='synthetic', database='syntheticdb', mysql_uid=120)
        with mock.patch.dict(sys.modules, pymysql=module), mock.patch('importlib.metadata.version', return_value='1.1.1'):
            server.connect_internal(profile, 'PRIVATE')
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs['unix_socket'], profile['unix_socket'])
        self.assertTrue(kwargs['ssl_disabled'])
        self.assertFalse(kwargs['local_infile'])
        self.assertNotIn('port', kwargs)
        self.assertNotIn('read_default_file', kwargs)


if __name__ == '__main__':
    unittest.main()
