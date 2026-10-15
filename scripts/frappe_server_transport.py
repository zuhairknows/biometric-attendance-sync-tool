"""Linux server-local transport: dedicated SELECT-only account, never frappe.db."""
import ast
import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import sys

from scripts.attendance_reconciliation import require
from scripts.frappe_export_database import Snapshot
from scripts.frappe_export_review import fingerprint, read_json

DRIVERS = ('1.1.1', '1.1.2', '1.2.3')
MARIADB_SERIES = ((10, 6), (10, 11), (11, 4))
SITE_ALIASES = ('erp.myfpf.com', 'myfpf.v.frappe.cloud')


def file_hash(path):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= 4 * 1024 * 1024, 'deployed_file_size_or_type_invalid')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def controlled_ancestors(path, owners, stat_fn=None):
    """No symlink or group/world-writable directory can redirect the trusted path."""
    stat_fn = os.lstat if stat_fn is None else stat_fn
    for directory in Path(path).parents:
        info = stat_fn(directory)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in owners and not info.st_mode & 0o022,
                'server_path_has_untrusted_ancestor')


class LinuxHost:
    def require_platform(self):
        require(sys.platform == 'linux' and hasattr(os, 'geteuid'), 'server_export_requires_linux')

    def runtime(self, bench_root):
        self.require_platform()
        require(Path(sys.prefix).resolve() == (bench_root / 'env').resolve(strict=True), 'use_approved_bench_python_environment')

    def private_directory(self, path):
        self.require_platform()
        directory = Path(path)
        require(directory.is_absolute() and directory == directory.resolve(strict=True), 'private_output_must_be_canonical')
        info = os.lstat(directory)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o700, 'private_output_owner_or_mode_invalid')
        controlled_ancestors(directory, {0, os.geteuid()})
        require(os.access(directory, os.R_OK | os.W_OK) and shutil.disk_usage(directory).free >= 256 * 1024 * 1024,
                'private_output_access_or_space_invalid')
        return directory

    def socket_identity(self, profile):
        self.require_platform()
        path = Path(profile['unix_socket'])
        require(path.is_absolute() and path == path.resolve(strict=True), 'socket_path_must_be_canonical')
        info = os.lstat(path)
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == profile['mysql_uid'], 'socket_owner_or_type_invalid')
        controlled_ancestors(path, {0, profile['mysql_uid']})
        return dict(device=info.st_dev, inode=info.st_ino, owner=info.st_uid)

    def peer(self, connection, profile):
        # Kernel peer credentials authenticate the local daemon without TCP/TLS or SQL claims.
        import socket
        import struct
        self.require_platform()
        sock = connection._sock
        require(sock.family == socket.AF_UNIX and sock.getpeername() == profile['unix_socket'], 'connection_not_approved_unix_socket')
        pid, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        require(pid > 0 and uid == profile['mysql_uid'], 'database_socket_peer_not_approved')
        return dict(peer_uid_verified=True, kernel_peer_credentials=True)


def connect_internal(profile, password):
    """Existing Bench dependency only. Never read site_config/db_password or call Frappe."""
    from importlib.metadata import version
    require(version('PyMySQL') in DRIVERS, 'server_database_driver_not_reviewed')
    import pymysql
    return pymysql.connect(unix_socket=profile['unix_socket'], host='localhost',
        user=profile['user'], password=password, database=profile['database'],
        charset='utf8mb4', autocommit=True, connect_timeout=10, read_timeout=60,
        write_timeout=10, local_infile=False, ssl_disabled=True,
        cursorclass=pymysql.cursors.DictCursor)


class InternalSnapshot(Snapshot):
    def __init__(self, *args, transport, **kwargs):
        super().__init__(*args, **kwargs)
        self.transport = transport

    def query(self, key, parameters=()):
        require(dt.datetime.now(dt.timezone.utc) <= dt.datetime.fromisoformat(self.review['expires_at']),
                'server_review_expired_during_snapshot')
        return super().query(key, parameters)

    def verify_transport(self):
        self.transport.verify_connection(self.connection)
        version = self.one('server_version')['server_version']
        match = re.fullmatch(r'(?:5\.5\.5-)?(\d+)\.(\d+)\.\d+[-+A-Za-z0-9.~]*MariaDB[-+A-Za-z0-9.~]*', version)
        require(match is not None and tuple(map(int, match.groups())) in MARIADB_SERIES, 'server_mariadb_series_unsupported')
        self.transport.evidence['mariadb_series'] = '.'.join(match.groups())


class ServerTransport:
    """Approved server context plus local storage/connection proof; no public endpoint."""
    def __init__(self, bench_root, site, *, host=None):
        self.bench_root = Path(bench_root).resolve(strict=True)
        require(isinstance(site, str) and re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]*', site), 'server_site_name_invalid')
        self.site, self.host = site, host or LinuxHost()
        self.evidence = {}

    def load_profile(self, path, review, hooks):
        profile = read_json(path)
        require(set(profile) == {'unix_socket', 'database', 'user', 'mysql_uid'}, 'internal_profile_keys_invalid')
        require(isinstance(profile['unix_socket'], str) and Path(profile['unix_socket']).is_absolute()
                and isinstance(profile['database'], str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', profile['database'])
                and isinstance(profile['user'], str) and profile['user']
                and type(profile['mysql_uid']) is int and profile['mysql_uid'] > 0, 'internal_profile_values_invalid')
        server = review.get('server_internal')
        require(isinstance(server, dict) and server.get('transport') == 'local_unix_socket'
                and server.get('internal_profile_sha256') == fingerprint(profile), 'server_internal_review_required')
        require(server.get('bench_root') == str(self.bench_root) and server.get('site') == self.site
                and sorted(server.get('confirmed_aliases', [])) == sorted(SITE_ALIASES)
                and server.get('frappe_major') in (14, 15)
                and server.get('runtime_hooks_reviewed') is True, 'server_context_not_approved')
        self.review, self.server, self.hooks, self.profile = review, server, hooks, profile
        self.host.runtime(self.bench_root)
        self.socket_proof = self.host.socket_identity(profile)
        self.validate_runtime()
        self.evidence = dict(transport='server_internal_unix_socket', external_database_access_required=False,
            frappe_connection_used=False, private_posix_storage=True,
            deployed_source_files_verified=True, deployed_hook_files_verified=True,
            context_sha256=fingerprint(server), internal_profile_sha256=fingerprint(profile),
            socket_identity_sha256=fingerprint(self.socket_proof))
        return profile

    def deployed_file(self, relative):
        require(isinstance(relative, str) and not Path(relative).is_absolute() and '..' not in Path(relative).parts,
                'deployed_path_invalid')
        path = (self.bench_root / relative).resolve(strict=True)
        require(self.bench_root in path.parents, 'deployed_file_escapes_bench')
        return path

    def validate_runtime(self):
        # Source files are inspected as data; importing apps could execute writes on a site account.
        require((self.bench_root / 'sites' / self.site).is_dir(), 'approved_bench_site_not_found')
        endpoint_module = self.review['endpoint_path'].rsplit('.', 1)[0]
        app = endpoint_module.split('.')[0]
        relative_files = dict(employee_checkin='apps/' + app + '/' + endpoint_module.replace('.', '/') + '.py',
                              frappe_database='apps/frappe/frappe/database/database.py',
                              frappe_query='apps/frappe/frappe/database/query.py')
        for role, relative in relative_files.items():
            require(file_hash(self.deployed_file(relative)) == self.review['files'][role]['sha256'], 'actual_deployed_source_changed')
        version_path = self.deployed_file('apps/frappe/frappe/__init__.py')
        require(file_hash(version_path) == self.server.get('version_source_sha256'), 'frappe_version_source_changed')
        tree = ast.parse(version_path.read_text(encoding='utf-8-sig'))
        versions = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)]
        require(len(versions) == 1 and isinstance(versions[0], str)
                and re.fullmatch(str(self.server['frappe_major']) + r'\.\d+\.\d+(?:[-+A-Za-z0-9.]*)', versions[0]), 'frappe_major_not_approved')
        hooks = self.server.get('hook_file_sha256')
        require(isinstance(hooks, dict) and set(hooks) == set(self.hooks['installed_apps']), 'deployed_hook_inventory_incomplete')
        for app, expected in hooks.items():
            require(isinstance(app, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', app), 'deployed_app_name_invalid')
            require(file_hash(self.deployed_file('apps/' + app + '/' + app + '/hooks.py')) == expected, 'deployed_hook_file_changed')
        require(self.host.socket_identity(self.profile) == self.socket_proof, 'database_socket_changed')

    def preflight_output(self, path):
        directory = self.host.private_directory(path)
        forbidden = (self.bench_root, Path(__file__).resolve().parents[1])
        require(directory != Path(directory.anchor) and not any(directory == root or root in directory.parents for root in forbidden),
                'server_output_must_be_outside_bench_and_tools')
        return directory

    def verify_connection(self, connection):
        require(self.host.socket_identity(self.profile) == self.socket_proof, 'database_socket_changed')
        self.evidence.update(self.host.peer(connection, self.profile))

    def snapshot(self, *args):
        return InternalSnapshot(*args, transport=self)
