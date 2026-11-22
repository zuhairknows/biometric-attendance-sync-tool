"""Read-only Windows inspection for the opt-in canonical ledger policy.

Executed with isolated Python flags. Never import local_config or service code.
S4U group/privilege retrieval is mandatory; there is no SID-only fallback.
"""
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import sys

# -I excludes the script directory; add only this reviewed module's directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from monitor_storage import (ADMINISTRATORS, SYSTEM, FULL, MAX_CHILDREN, PolicyError,
    canonical_sid, check_descriptor, check_identity, normalize_mask, require)


class LUID(c.Structure):
    _fields_ = [('low', w.DWORD), ('high', w.LONG)]


class SIDAttributes(c.Structure):
    _fields_ = [('sid', c.c_void_p), ('attributes', w.DWORD)]


class Groups(c.Structure):
    _fields_ = [('count', w.DWORD), ('items', SIDAttributes * 1)]


class Privilege(c.Structure):
    _fields_ = [('luid', LUID), ('attributes', w.DWORD)]


class Privileges(c.Structure):
    _fields_ = [('count', w.DWORD), ('items', Privilege * 1)]


class Request(c.Structure):
    _fields_ = [('access', w.DWORD), ('self_sid', c.c_void_p), ('types', c.c_void_p),
                ('type_count', w.DWORD), ('arguments', c.c_void_p)]


class Reply(c.Structure):
    _fields_ = [('count', w.DWORD), ('granted', c.POINTER(w.DWORD)),
                ('sacl', c.POINTER(w.DWORD)), ('error', c.POINTER(w.DWORD))]


class Authz:
    def __init__(self, *, sid=None, token=None):
        self.api = c.WinDLL('authz', use_last_error=True)
        signatures = {
            'AuthzInitializeResourceManager': ([w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, w.LPCWSTR, c.POINTER(c.c_void_p)], w.BOOL),
            'AuthzInitializeContextFromSid': ([w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, LUID, c.c_void_p, c.POINTER(c.c_void_p)], w.BOOL),
            'AuthzInitializeContextFromToken': ([w.DWORD, w.HANDLE, c.c_void_p, c.c_void_p, LUID, c.c_void_p, c.POINTER(c.c_void_p)], w.BOOL),
            'AuthzGetInformationFromContext': ([c.c_void_p, c.c_int, w.DWORD, c.POINTER(w.DWORD), c.c_void_p], w.BOOL),
            'AuthzAccessCheck': ([w.DWORD, c.c_void_p, c.POINTER(Request), c.c_void_p, c.c_void_p,
                                  c.c_void_p, w.DWORD, c.POINTER(Reply), c.c_void_p], w.BOOL),
            'AuthzFreeContext': ([c.c_void_p], w.BOOL),
            'AuthzFreeResourceManager': ([c.c_void_p], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.manager, self.context = c.c_void_p(), c.c_void_p()
        try:
            self.ok(self.api.AuthzInitializeResourceManager(1, None, None, None, 'Ledger monitor validation', c.byref(self.manager)))
            if token is not None:
                self.ok(self.api.AuthzInitializeContextFromToken(0, int(token), self.manager, None, LUID(), None, c.byref(self.context)))
            else:
                import win32security as security
                raw = c.create_string_buffer(bytes(security.ConvertStringSidToSid(sid)))
                # REQUIRE_S4U_LOGON | COMPUTE_PRIVILEGES. Group-less fallback is forbidden.
                self.ok(self.api.AuthzInitializeContextFromSid(12, raw, self.manager, None, LUID(), None, c.byref(self.context)))
        except BaseException:
            self.close()
            raise

    @staticmethod
    def ok(result):
        require(bool(result), 'monitor_native_check_failed')

    def close(self):
        if self.context.value:
            self.api.AuthzFreeContext(self.context)
            self.context = c.c_void_p()
        if self.manager.value:
            self.api.AuthzFreeResourceManager(self.manager)
            self.manager = c.c_void_p()

    def information(self, kind):
        size = w.DWORD()
        self.api.AuthzGetInformationFromContext(self.context, kind, 0, c.byref(size), None)
        require(0 < size.value <= 1024 * 1024, 'monitor_identity_unverified')
        buffer = c.create_string_buffer(size.value)
        self.ok(self.api.AuthzGetInformationFromContext(self.context, kind, size, c.byref(size), buffer))
        return buffer

    def identity_details(self):
        import win32security as security
        import pywintypes
        groups = self.information(2)
        count = c.cast(groups, c.POINTER(w.DWORD))[0]
        require(Groups.items.offset + count * c.sizeof(SIDAttributes) <= len(groups), 'monitor_identity_unverified')
        values = []
        for index in range(count):
            item = SIDAttributes.from_buffer(groups, Groups.items.offset + index * c.sizeof(SIDAttributes))
            # pywintypes.SID accepts a SID byte buffer; use native GetLengthSid.
            advapi = c.WinDLL('advapi32', use_last_error=True)
            advapi.GetLengthSid.argtypes, advapi.GetLengthSid.restype = [c.c_void_p], w.DWORD
            size = advapi.GetLengthSid(item.sid)
            require(8 <= size <= 68, 'monitor_identity_unverified')
            values.append(security.ConvertSidToStringSid(pywintypes.SID(c.string_at(item.sid, size))))
        privileges = self.information(4)
        count = c.cast(privileges, c.POINTER(w.DWORD))[0]
        require(Privileges.items.offset + count * c.sizeof(Privilege) <= len(privileges), 'monitor_identity_unverified')
        names = []
        for index in range(count):
            item = Privilege.from_buffer(privileges, Privileges.items.offset + index * c.sizeof(Privilege))
            luid = (item.luid.high << 32) | item.luid.low
            names.append(security.LookupPrivilegeName(None, luid))
        return values, names

    def maximum(self, descriptor):
        raw = c.create_string_buffer(bytes(descriptor))
        granted, error, sacl = w.DWORD(), w.DWORD(), w.DWORD()
        reply = Reply(1, c.pointer(granted), c.pointer(sacl), c.pointer(error))
        request = Request(0x02000000, None, None, 0, None)  # MAXIMUM_ALLOWED
        self.ok(self.api.AuthzAccessCheck(0, self.context, c.byref(request), None, raw, None, 0, c.byref(reply), None))
        require(error.value in (0, 5), 'monitor_native_check_failed')
        require(error.value == 0 or granted.value == 0, 'monitor_native_check_failed')
        return granted.value


def descriptor(path):
    import win32security as security
    sd = security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT, 7)
    dacl = sd.GetSecurityDescriptorDacl()
    require(dacl is not None, 'monitor_null_dacl')
    require(dacl.GetAceCount() <= 256, 'monitor_acl_limit')
    aces, mapped = [], security.ACL()
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        require(len(ace) == 3 and ace[0][0] in (0, 1), 'monitor_ace_unsupported')
        (kind, flags), mask, sid = ace
        mask = normalize_mask(mask)
        aces.append((kind, flags, mask, security.ConvertSidToStringSid(sid)))
        add = mapped.AddAccessAllowedAceEx if kind == 0 else mapped.AddAccessDeniedAceEx
        add(security.ACL_REVISION, flags, mask, sid)
    # Authz does not take GENERIC_MAPPING. Supply a descriptor with mapped masks.
    sd.SetSecurityDescriptorDacl(1, mapped, 0)
    return (sd, security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner()),
            bool(sd.GetSecurityDescriptorControl()[0] & 0x1000), aces)


def inspect(ledger, sid, config, author=None):
    import os
    import win32api
    import win32con
    import win32file
    import win32security as security
    ledger, config = Path(ledger), Path(config)
    require(ledger.is_absolute() and config.is_absolute(), 'monitor_scope_invalid')
    canonical_sid(sid)
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        current = security.ConvertSidToStringSid(security.GetTokenInformation(token, security.TokenUser)[0])
        require(current == SYSTEM, 'monitor_requires_localsystem')
        # Only after establishing LocalSystem may Windows resolve the approved SID.
        native_sid = security.ConvertStringSidToSid(sid)
        require(security.ConvertSidToStringSid(native_sid) == sid, 'monitor_sid_invalid')
        name, domain, account_type = security.LookupAccountSid(None, native_sid)
        root = ledger.parent
        require(ledger.is_file() and config.is_file() and config.suffix.lower() == '.py', 'monitor_scope_invalid')
        require(root != config.parent and root not in config.parents and config.parent not in root.parents,
                'monitor_configuration_untrusted')
        checkout = Path(__file__).resolve().parent
        require(checkout != root and checkout not in root.parents, 'monitor_scope_invalid')
        for key in ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer'):
            if os.environ.get(key):
                cloud = Path(os.environ[key]).resolve()
                require(cloud != root.resolve() and cloud not in root.resolve().parents, 'monitor_scope_invalid')
        require(root != Path(ledger.anchor), 'monitor_scope_invalid')
        for drive in {ledger.anchor, config.anchor}:
            require(win32file.GetDriveType(drive) == 3 and win32api.GetVolumeInformation(drive)[4] == 'NTFS', 'monitor_volume_invalid')
        children = []
        from itertools import islice
        children = list(islice(root.iterdir(), MAX_CHILDREN + 1))
        require(len(children) <= MAX_CHILDREN, 'monitor_child_limit')
        objects = [(root, 'directory'), (config, 'config'), (config.parent, 'configdir')]
        objects += [(p, 'ledger' if p == ledger else 'child') for p in children]
        objects += [(p, 'ancestor') for p in (*root.parents, *config.parent.parents)]
        for path, kind in objects:
            require(not win32api.GetFileAttributes(str(path)) & 0x400, 'monitor_reparse_point')
        monitor, writer = Authz(sid=sid), None
        try:
            groups, privileges = monitor.identity_details()
            check_identity(sid, account_type, name, groups, privileges)
            if author is not None:
                canonical_sid(author)
                # A group/SID-history alias must not silently confer the author role.
                require(author not in (sid, *groups), 'monitor_recovery_role_conflict')
            policy = security.LsaOpenPolicy(None, security.POLICY_LOOKUP_NAMES)
            try:
                import pywintypes
                for principal in (sid, *groups):
                    try:
                        rights = security.LsaEnumerateAccountRights(policy, security.ConvertStringSidToSid(principal))
                    except pywintypes.error as error:
                        if error.winerror != 2:  # No local account-rights assignment.
                            raise
                        rights = ()
                    require('SeServiceLogonRight' not in rights, 'monitor_service_identity')
            finally:
                policy.Close()
            writer = Authz(token=token)
            for path, kind in objects:
                sd, owner, protected, aces = descriptor(path)
                check_descriptor(owner, protected, aces, kind, sid, monitor.maximum(sd), writer.maximum(sd))
        finally:
            monitor.close()
            if writer:
                writer.close()
    finally:
        token.Close()


if __name__ == '__main__':
    try:
        values = json.loads(sys.stdin.read(16385))
        require(set(values) == {'ledger', 'sid', 'config', 'author'}, 'monitor_scope_invalid')
        inspect(values['ledger'], values['sid'], values['config'], values['author'])
        print('{"safe": true}')
    except Exception:
        print('{"safe": false}')
        raise SystemExit(1) from None
