"""Startup-only path allowlist and safe device metadata extraction. Never imports config."""
import ast
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .controller import ManagerError
from scripts.capture_history_safety import check_directory_acl

PATHS = ('monitor_ledger', 'backup', 'contract', 'output', 'workspace', 'simulation', 'normal_lock', 'device_config', 'offline_directory', 'diagnostics_directory',
         'recovery_ledger', 'recovery_inbox', 'recovery_summary')
FIELDS = (*PATHS, 'contract_hash', 'backup_hash', 'site', 'start', 'end', 'offline_evidence', 'offline_evidence_hash',
          'recovery_summary_hash', 'recovery_service_sid')


def local_path(value):
    path = Path(value)
    if not isinstance(value, str) or not path.is_absolute() or str(path).startswith('\\\\'):
        raise ManagerError('approved_absolute_local_path_required')
    return str(path.resolve())


def load_profile(filename, acl_check=check_directory_acl):
    """Browser requests cannot add paths. Only the operator's startup profile can."""
    try:
        path = Path(local_path(filename))
        acl_check(path.parent)
        if path.stat().st_size > 65536:
            raise ValueError
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) - set(FIELDS):
            raise ValueError
        if any(not isinstance(v, str) or len(v) > 4096 for v in data.values()):
            raise ValueError
        if data.get('site'):
            origin = urlsplit(data['site'])
            if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                    or origin.query or origin.fragment or origin.path not in ('', '/')):
                raise ValueError
        values = {k: local_path(v) if k in PATHS and v else v for k, v in data.items()}
        # Existing approved profile storage is the default private diagnostic location.
        # Validation and all writes are deferred until an explicit offline action.
        values.setdefault('diagnostics_directory', str(path.parent))
        return values
    except Exception:
        raise ManagerError('private_startup_profile_invalid') from None


def device_catalog(filename):
    """Whitelist device ID/address/port from literal source; discard passwords and code."""
    if not filename:
        return []
    try:
        path = Path(filename)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        assignments = [node.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'devices' for t in node.targets)]
        if len(assignments) != 1 or not isinstance(assignments[0], (ast.List, ast.Tuple)):
            raise ValueError
        devices, names = [], set()
        for node in assignments[0].elts:
            if not isinstance(node, ast.Dict):
                raise ValueError
            selected = {}
            for key, value in zip(node.keys, node.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    raise ValueError
                if key.value in ('device_id', 'ip', 'port'):
                    if key.value in selected:
                        raise ValueError
                    selected[key.value] = ast.literal_eval(value)
            name = selected['device_id']
            if not isinstance(name, str) or not re.fullmatch(r'FP[12]_DEVICE_\d{2,3}', name) or name in names:
                raise ValueError
            names.add(name)
            address = str(ipaddress.ip_address(selected['ip'])) if selected.get('ip') else 'Not supplied'
            port = selected.get('port')
            if port is not None and (type(port) is not int or not 1 <= port <= 65535):
                raise ValueError
            devices.append(dict(device=name, floor=name[:3], address=address, port=port or 'Not supplied'))
        return devices
    except Exception:
        raise ManagerError('device_configuration_not_a_verified_literal') from None
