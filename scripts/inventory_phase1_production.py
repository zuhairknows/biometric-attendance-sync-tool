"""Sanitized local inventory only. Use the service's Python with -I -S -B.

Never import integration/configuration/installed packages, open SQLite databases,
read attendance contents, change files, operate services, or contact a network.
"""
import ast
import datetime as dt
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import warnings


MAX_BYTES = 1024 * 1024
PACKAGES = ('requests', 'pickledb', 'pyzk', 'PyQt5', 'pywin32')
PATCH_BLOCK = '''                # SpeedFace-V5L uses 49-byte attendance records.
                if record_size == 49:
                    attendance_data = attendance_data[49:]
                else:
                    attendance_data = attendance_data[40:]'''


def require(condition):
    if not condition:
        raise ValueError()


def local(path):
    """Refuse UNC/reparse paths before reading through them."""
    path = Path(os.path.abspath(path))
    require(re.match(r'^[A-Za-z]:[\\/]', str(path)) is not None)
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return path


def read(path, maximum=MAX_BYTES):
    with local(path).open('rb') as stream:
        value = stream.read(maximum + 1)
    require(len(value) <= maximum)
    return value.decode('utf-8-sig')


def passive(node):
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(passive(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and passive(key) and passive(value)
                   for key, value in zip(node.keys, node.values))
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub))
            and isinstance(node.operand, ast.Constant))


def assignments(source):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')  # Parser warnings can quote source fragments.
        tree = ast.parse(source)
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        require(isinstance(node, ast.Assign) and passive(node.value))
        for target in node.targets:
            require(isinstance(target, ast.Name) and target.id not in result)
            result[target.id] = node.value
    return result


def fields(node):
    require(isinstance(node, ast.Dict))
    result = {}
    for key, value in zip(node.keys, node.values):
        require(isinstance(key, ast.Constant) and isinstance(key.value, str)
                and key.value not in result)
        result[key.value] = value
    return result


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value))
    return value


def inventory_config(root):
    nodes = assignments(read(root / 'local_config.py'))
    # Evaluate only requested operational fields, never credential/device IP fields.
    def value(name, default=None):
        return ast.literal_eval(nodes[name]) if name in nodes else default
    boundary = value('IMPORT_START_DATE')
    require(isinstance(boundary, str) and re.fullmatch(r'[0-9]{8}', boundary))
    dt.datetime.strptime(boundary, '%Y%m%d')
    interval = value('PULL_FREQUENCY')
    timeout = value('ERPNEXT_REQUEST_TIMEOUT', value('REQUEST_TIMEOUT', 30))
    for number in (interval, timeout):
        require(type(number) in (int, float) and 0 < number < 86400)
    devices_node = nodes['devices']
    require(isinstance(devices_node, (ast.List, ast.Tuple)) and 0 < len(devices_node.elts) <= 100)
    devices = []
    for node in devices_node.elts:
        item = fields(node)
        device_id = identifier(ast.literal_eval(item['device_id']))
        direction = ast.literal_eval(item['punch_direction']) if 'punch_direction' in item else None
        deletion = ast.literal_eval(item['clear_from_device_on_fetch']) if 'clear_from_device_on_fetch' in item else False
        require(direction is None or type(direction) is str and direction in ('IN', 'OUT', 'AUTO'))
        require(type(deletion) is bool)
        devices.append(dict(device_id=device_id, punch_direction=direction,
                            clear_from_device_on_fetch=deletion,
                            direction_defaulted='punch_direction' not in item,
                            deletion_defaulted='clear_from_device_on_fetch' not in item))
    ids = [device['device_id'] for device in devices]
    mappings = []
    mapping_node = nodes.get('shift_type_device_mapping', ast.List(elts=[]))
    require(isinstance(mapping_node, (ast.List, ast.Tuple)) and len(mapping_node.elts) <= 100)
    for index, node in enumerate(mapping_node.elts, 1):
        item = fields(node)
        related = ast.literal_eval(item['related_device_id'])
        shifts = ast.literal_eval(item['shift_type_name'])
        require(isinstance(related, (list, tuple)) and len(related) <= 100)
        related = [identifier(device_id) for device_id in related]
        shifts = [shifts] if isinstance(shifts, str) else shifts
        require(isinstance(shifts, (list, tuple)) and all(isinstance(s, str) and s for s in shifts))
        mappings.append(dict(mapping_index=index, shift_type_count=len(shifts),
                             device_ids=related, valid=bool(related) and bool(shifts)
                             and set(related) <= set(ids)))
    log_setting = value('LOGS_DIRECTORY')
    require(isinstance(log_setting, str) and log_setting)
    if Path(log_setting).is_absolute():
        logs = [dict(status='RESOLVED_ABSOLUTE_LITERAL', path=str(local(log_setting)))]
    else:
        # Neither the service script nor SMWinservice establishes a working directory.
        # These are candidates only, not proof of the running process's cwd.
        require(not Path(log_setting).drive and '..' not in Path(log_setting).parts)
        logs = [dict(status='UNVERIFIED_RELATIVE_CANDIDATE', path=str(local(base / log_setting)))
                for base in (root, Path(os.environ['SystemRoot']) / 'System32')]
    ledger_default = str(Path(os.environ['PROGRAMDATA']) / 'ERPNextBiometricPushService' / 'sync.sqlite3')
    ledger = value('DURABLE_DB_PATH', ledger_default)
    require(isinstance(ledger, str) and Path(ledger).is_absolute())
    enabled = value('DURABLE_SYNC_ENABLED', False)
    require(type(enabled) is bool)
    return dict(status='LITERAL_ON_DISK_CANDIDATE', import_start_date=boundary,
                polling_minutes=interval, request_timeout_seconds=timeout,
                request_timeout_defaulted='ERPNEXT_REQUEST_TIMEOUT' not in nodes and 'REQUEST_TIMEOUT' not in nodes,
                devices=devices, unique_device_ids=len(ids) == len(set(ids)),
                all_device_deletion_disabled=all(not device['clear_from_device_on_fetch'] for device in devices),
                shift_mappings=mappings, shift_names_withheld=True, log_directories=logs,
                durable_enabled=enabled, ledger_path=str(local(ledger)),
                ledger_path_defaulted='DURABLE_DB_PATH' not in nodes)


def file_info(path):
    try:
        path = local(path)
        info = path.stat()
        require(stat.S_ISREG(info.st_mode))
        return dict(exists=True, bytes=info.st_size)
    except FileNotFoundError:
        return dict(exists=False, bytes=0)
    except Exception:
        return dict(status='UNVERIFIED')


def logs_inventory(directory):
    try:
        path = local(directory)
        result = {key: dict(count=0, bytes=0) for key in
                  ('retry_dumps', 'attendance_logs', 'general_logs', 'status_checkpoints', 'other_files')}
        if not path.exists():
            return dict(exists=False)
        for index, child in enumerate(path.iterdir()):
            require(index < 10000)
            info = child.lstat()
            require(not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
            if not stat.S_ISREG(info.st_mode):
                continue
            name = child.name
            category = ('retry_dumps' if name.endswith('_last_fetch_dump.json') else
                        'status_checkpoints' if name.startswith('status.json') else
                        'attendance_logs' if name.startswith(('attendance_success_log_', 'attendance_failed_log_')) else
                        'general_logs' if name.startswith(('logs.log', 'error.log')) else 'other_files')
            result[category]['count'] += 1
            result[category]['bytes'] += info.st_size
        return dict(exists=True, status_json=file_info(path / 'status.json'), categories=result,
                    live_counts_not_atomic=True, contents_not_read=True)
    except Exception:
        return dict(status='UNVERIFIED')


def dependencies():
    executable = local(sys.executable)
    environment = executable.parent.parent if executable.parent.name.lower() == 'scripts' else executable.parent
    sites = [environment / 'Lib' / 'site-packages']
    cfg = local(environment / 'pyvenv.cfg')
    if cfg.exists():
        settings = read(cfg).lower().splitlines()
        if any(re.fullmatch(r'\s*include-system-site-packages\s*=\s*true\s*', line) for line in settings):
            sites.append(Path(sys.base_prefix) / 'Lib' / 'site-packages')
    sites = list(dict.fromkeys(local(site) for site in sites))
    found = {name.lower(): [] for name in PACKAGES}
    # Inspect local metadata explicitly, including reparse checks before reads.
    # A .pth file is never processed and third-party package code is never imported.
    for site in sites:
        if not site.exists():
            continue
        for entry in site.iterdir():
            if entry.suffix.lower() not in ('.dist-info', '.egg-info'):
                continue
            entry = local(entry)
            local(entry / 'METADATA')
            local(entry / 'PKG-INFO')
            distribution = metadata.Distribution.at(entry)
            name = distribution.metadata.get('Name', '').lower()
            if name in found:
                require(re.fullmatch(r'[A-Za-z0-9.+!_-]{1,80}', distribution.version))
                found[name].append(distribution.version)
    packages = {name: dict(status='PRESENT' if len(found[name.lower()]) == 1 else
                           'MISSING' if not found[name.lower()] else 'AMBIGUOUS',
                           versions=found[name.lower()]) for name in PACKAGES}
    bases = []
    for site in sites:
        base = local(site / 'zk' / 'base.py')
        if base.is_file():
            bases.append(base)
    patch = 'UNVERIFIED'
    if len(bases) == 1 and found['pyzk'] == ['0.9']:
        source = read(bases[0], maximum=4 * MAX_BYTES).replace('\r\n', '\n')
        patch = 'EXPECTED_PATCH_PATTERN_PRESENT' if PATCH_BLOCK in source else 'EXPECTED_PATCH_PATTERN_NOT_FOUND'
    return dict(packages=packages, pyzk_patch=patch,
                pth_files_present=any(list(site.glob('*.pth')) for site in sites),
                metadata_only=True, runtime_import_resolution='UNVERIFIED')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        require(sys.platform == 'win32' and len(argv) == 2 and argv[0] == '--root')
        root = local(argv[1])
        config = inventory_config(root)
        for item in config['log_directories']:
            item['inventory'] = logs_inventory(item['path'])
        ledger = Path(config['ledger_path'])
        config['ledger_files'] = {suffix or 'database': file_info(str(ledger) + suffix)
                                  for suffix in ('', '.activation.json', '-journal', '-wal', '-shm')}
        result = dict(configuration=config)
    except Exception:
        result = dict(configuration=dict(status='UNVERIFIED_DYNAMIC_INVALID_OR_UNREADABLE'))
    try:
        result['dependencies'] = dependencies()
    except Exception:
        result['dependencies'] = dict(status='UNVERIFIED')
    result['python_version'] = '.'.join(map(str, sys.version_info[:3]))
    result['sqlite_version'] = sqlite3.sqlite_version
    result['scope'] = 'ON_DISK_INVENTORY_ONLY_NO_SERVICE_MEMORY_OR_NETWORK'
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 1 if result['configuration'].get('status', '').startswith('UNVERIFIED') else 0


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
