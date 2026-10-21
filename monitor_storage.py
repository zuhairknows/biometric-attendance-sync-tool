"""Canonical-ledger monitoring exception; generic private storage stays strict.

The monitor is a reader, never a writer/owner/recovery author. Identity, effective
rights and filesystem inspection run in a bounded read-only Windows child.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SYSTEM = 'S-1-5-18'
ADMINISTRATORS = 'S-1-5-32-544'
WRITERS = frozenset((SYSTEM, ADMINISTRATORS))
TRUSTED_ANCESTOR_OWNERS = WRITERS | {'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'}  # Windows TrustedInstaller.
READ = 0x120089
DIRECTORY_READ = 0x1200A9
FULL = 0x1F01FF
WRITE = 0xD0156  # Data/append/EA/attributes, create/delete-child, DELETE/DAC/owner.
REPLACE = 0xD0040  # Ancestors may permit creating siblings, never replacement.
MAX_CHILDREN = 256


class PolicyError(Exception):
    """Fixed codes only: never disclose account names, paths or native errors."""


def require(value, code):
    if not value:
        raise PolicyError(code)


def canonical_sid(value):
    require(isinstance(value, str), 'monitor_sid_invalid')
    match = re.fullmatch(r'S-1-5-21-([1-9][0-9]*)-([1-9][0-9]*)-([1-9][0-9]*)-([1-9][0-9]*)', value)
    require(match and all(int(n) <= 0xFFFFFFFF for n in match.groups())
            and int(match.group(4)) >= 1000, 'monitor_sid_invalid')
    return value


def normalize_mask(mask):
    require(type(mask) is int and -(2**31) <= mask <= 0xFFFFFFFF, 'monitor_ace_unsupported')
    mask &= 0xFFFFFFFF
    for generic, mapped in ((0x80000000, READ), (0x40000000, 0x120116),
                            (0x20000000, 0x1200A0), (0x10000000, FULL)):
        if mask & generic:
            mask = (mask & ~generic) | mapped
    return mask


def check_identity(sid, account_type, name, groups, privileges):
    canonical_sid(sid)
    require(account_type == 1 and name and not name.endswith('$'), 'monitor_identity_unverified')
    # Membership is rejected even when an administrator token happens to be filtered.
    privileged = {'S-1-5-32-' + str(rid) for rid in (544, 547, 548, 549, 550, 551, 552)}
    require(not any(g in privileged or re.fullmatch(r'S-1-5-21-(?:[0-9]+-){3}(512|518|519)', g)
                    for g in groups), 'monitor_privileged_identity')
    ordinary = {'SeChangeNotifyPrivilege', 'SeIncreaseWorkingSetPrivilege',
                'SeTimeZonePrivilege', 'SeShutdownPrivilege', 'SeUndockPrivilege'}
    require(set(privileges) <= ordinary, 'monitor_privileged_identity')


def check_descriptor(owner, protected, aces, kind, monitor_sid, effective, writer_effective):
    """Conservative ACE ceiling PLUS group-aware Windows maximum access.

    Deny ACEs cannot excuse an excessive Allow. Unknown/object/callback ACEs
    fail closed rather than relying on unsupported inheritance/claim semantics.
    """
    require(kind in ('directory', 'ledger', 'child', 'config', 'configdir', 'ancestor'), 'monitor_scope_invalid')
    require(owner in (TRUSTED_ANCESTOR_OWNERS if kind == 'ancestor' else WRITERS),
            'monitor_owner_untrusted')
    if kind == 'directory':
        require(protected, 'monitor_directory_unprotected')
    for ace_type, flags, mask, sid in aces:
        require(ace_type in (0, 1) and flags & ~0x1F == 0, 'monitor_ace_unsupported')
        mask = normalize_mask(mask)
        if sid == monitor_sid:
            # No propagation/inherit-only flags, even for a currently harmless Deny.
            require(flags & ~0x10 == 0, 'monitor_ace_propagation')
            if ace_type == 0:
                ceiling = DIRECTORY_READ if kind == 'directory' else READ if kind == 'ledger' else 0
                require(mask & ~ceiling == 0, 'monitor_ace_excessive')
        elif ace_type == 0 and kind in ('directory', 'ledger', 'child', 'config'):
            require(sid in WRITERS, 'monitor_principal_untrusted')
        elif ace_type == 0 and sid not in WRITERS and not flags & 8:
            # S4U is not an interactive logon. Refuse replacement grants even to
            # logon groups absent from that context; Deny ACEs cannot excuse them.
            ceiling = WRITE if kind == 'configdir' else REPLACE
            require(mask & ceiling == 0, 'monitor_parent_or_configuration_untrusted')
    effective = normalize_mask(effective)
    writer_effective = normalize_mask(writer_effective)
    if kind == 'directory':
        require(effective & ~DIRECTORY_READ == 0 and effective & 0x21 == 0x21,
                'monitor_effective_access_invalid')
        require(writer_effective & FULL == FULL, 'monitor_writer_access_invalid')
    elif kind == 'ledger':
        require(effective & ~READ == 0 and effective & 1, 'monitor_effective_access_invalid')
        require(writer_effective & FULL == FULL, 'monitor_writer_access_invalid')
    elif kind in ('child', 'config'):
        require(effective & (WRITE | 1) == 0, 'monitor_effective_access_invalid')
    elif kind == 'configdir':
        require(effective & WRITE == 0, 'monitor_configuration_untrusted')
    else:
        require(effective & REPLACE == 0, 'monitor_parent_replace_access')


def validate(path, sid, config_path, author=None):
    """No ACL mutation, identity impersonation, file writes, SQLite or network client."""
    from durable_startup import StartupError
    try:
        canonical_sid(sid)
        if author is not None:
            canonical_sid(author)
        path, config_path = Path(path), Path(config_path)
        require(path.is_absolute() and config_path.is_absolute(), 'monitor_scope_invalid')
        result = subprocess.run(
            [sys.executable, '-I', '-B', str(Path(__file__).with_name('monitor_storage_windows.py'))],
            input=json.dumps(dict(ledger=str(path), sid=sid, config=str(config_path), author=author)),
            capture_output=True, text=True, timeout=15, check=False,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        body = json.loads(result.stdout)
        require(result.returncode == 0 and body == {'safe': True}, 'monitor_storage_not_private')
    except (PolicyError, OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        # Child stderr/native diagnostics can expose private identity/configuration.
        raise StartupError('monitor_storage_not_private') from None
