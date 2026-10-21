"""Monitoring exception regressions; synthetic descriptors, never live ACL edits."""
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import durable_startup as startup
import durable_sync
import monitor_storage as policy

SID = 'S-1-5-21-111-222-333-1001'
OTHER = 'S-1-5-21-111-222-333-1002'
SYSTEM, ADMINS = policy.SYSTEM, policy.ADMINISTRATORS


def ace(sid=SID, mask=policy.READ, flags=0, kind=0):
    return kind, flags, mask, sid


class PolicyTests(unittest.TestCase):
    def check(self, *, kind='ledger', aces=None, owner=ADMINS, protected=False,
              effective=policy.READ, writer=policy.FULL):
        policy.check_descriptor(owner, protected, aces if aces is not None else [
            ace(SYSTEM, policy.FULL, 0x10), ace(ADMINS, policy.FULL, 0x10), ace()],
            kind, SID, effective, writer)

    def test_production_directory_and_ledger_shapes(self):
        self.check()
        self.check(kind='directory', protected=True, effective=policy.DIRECTORY_READ,
                   aces=[ace(SYSTEM, policy.FULL), ace(ADMINS, policy.FULL),
                         ace(mask=policy.DIRECTORY_READ)])
        self.check(aces=[ace(flags=0x10)])  # Safe inherited READ has the same ceiling.

    def test_no_excessive_direct_inherited_split_or_generic_allow(self):
        for extra in (2, 4, 0x10, 0x40, 0x100, 0x10000, 0x40000, 0x80000,
                      0x40000000, 0x10000000, 0x20000000):
            for flags in (0, 0x10):
                with self.subTest(extra=extra, flags=flags), self.assertRaises(policy.PolicyError):
                    self.check(aces=[ace(), ace(mask=extra, flags=flags)])
        # A Deny does not make an excessive Allow acceptable.
        with self.assertRaises(policy.PolicyError):
            self.check(aces=[ace(mask=2, kind=1), ace(mask=policy.FULL)])
        self.check(aces=[ace(mask=0x80000000)])  # GENERIC_READ maps to exact READ.

    def test_directory_creation_and_delete_child_are_not_traversal(self):
        for extra in (2, 4, 0x40, 0x10000, 0x40000):
            with self.subTest(extra=extra), self.assertRaises(policy.PolicyError):
                self.check(kind='directory', protected=True, effective=policy.DIRECTORY_READ,
                           aces=[ace(mask=policy.DIRECTORY_READ | extra)])

    def test_propagation_inherit_only_and_unsupported_aces_refused(self):
        for flags in (1, 2, 4, 8, 3, 0x11, 0x12, 0x80):
            with self.subTest(flags=flags), self.assertRaises(policy.PolicyError):
                self.check(aces=[ace(flags=flags)])
        for kind in (2, 5, 9, 17):
            with self.assertRaises(policy.PolicyError):
                self.check(aces=[ace(kind=kind)])
        with self.assertRaises(policy.PolicyError):
            self.check(aces=[ace(mask=0x100000000)])

    def test_unknown_principals_and_monitor_owner_fail(self):
        with self.assertRaises(policy.PolicyError):
            self.check(aces=[ace(OTHER)])
        with self.assertRaises(policy.PolicyError):
            self.check(owner=SID)
        with self.assertRaises(policy.PolicyError):
            self.check(kind='directory', protected=False, effective=policy.DIRECTORY_READ)

    def test_other_children_activation_and_sidecars_get_no_read_exception(self):
        for kind in ('child', 'config'):
            with self.subTest(kind=kind), self.assertRaises(policy.PolicyError):
                self.check(kind=kind, aces=[ace()])
            with self.assertRaises(policy.PolicyError):
                self.check(kind=kind, aces=[], effective=1)
            self.check(kind=kind, aces=[ace(SYSTEM, policy.FULL)], effective=0)

    def test_group_effective_access_and_localsystem_rights(self):
        for effective in (policy.FULL, policy.READ | 2, 0):
            with self.subTest(effective=effective), self.assertRaises(policy.PolicyError):
                self.check(effective=effective)
        with self.assertRaises(policy.PolicyError):
            self.check(writer=policy.READ)
        with self.assertRaises(policy.PolicyError):
            self.check(kind='ancestor', aces=[], effective=0x40)
        with self.assertRaises(policy.PolicyError):
            self.check(kind='configdir', aces=[], effective=2)
        self.check(kind='ancestor', aces=[], effective=4)  # Creating a sibling is not replacement.

    def test_configuration_and_ancestor_replacement_cannot_hide_in_logon_groups(self):
        for kind, rights in (('configdir', 2), ('ancestor', 0x40), ('ancestor', 0x40000)):
            with self.subTest(kind=kind), self.assertRaises(policy.PolicyError):
                self.check(kind=kind, aces=[ace('S-1-5-4', rights)], effective=0)
        self.check(kind='ancestor', aces=[ace('S-1-5-4', 4)], effective=4)
        with self.assertRaises(policy.PolicyError):
            self.check(kind='ancestor', owner=OTHER, aces=[], effective=0)

    def test_sid_shape_rejects_services_privileged_rids_and_noncanonical_aliases(self):
        for sid in (None, False, '', SYSTEM, ADMINS, 'S-1-5-19', 'S-1-5-20',
                    'S-1-5-80-1-2-3-4-5', 'S-1-5-21-1-2-3-500',
                    'S-1-5-21-01-2-3-1001', 'S-1-5-21-1-2-4294967296-1001',
                    SID + '\n', SID.lower(), 'S-1-5-21-1-2-3-1001;evil'):
            with self.subTest(sid=sid), self.assertRaises(policy.PolicyError):
                policy.canonical_sid(sid)
        self.assertEqual(policy.canonical_sid(SID), SID)

    def test_user_resolution_groups_and_privileges(self):
        policy.check_identity(SID, 1, 'synthetic-monitor', ['S-1-5-32-545'], ['SeChangeNotifyPrivilege'])
        for account_type, name in ((2, 'group'), (9, 'machine$'), (1, 'msa$'), (8, 'unknown')):
            with self.assertRaises(policy.PolicyError):
                policy.check_identity(SID, account_type, name, [], [])
        for group in (ADMINS, 'S-1-5-32-551', 'S-1-5-21-1-2-3-512'):
            with self.assertRaises(policy.PolicyError):
                policy.check_identity(SID, 1, 'user', [group], [])
        for privilege in ('SeBackupPrivilege', 'SeRestorePrivilege', 'SeTakeOwnershipPrivilege',
                          'SeDebugPrivilege', 'SeImpersonatePrivilege', 'SeTcbPrivilege'):
            with self.assertRaises(policy.PolicyError):
                policy.check_identity(SID, 1, 'user', [], [privilege])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='monitor-policy-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ledger = self.root / 'state' / 'sync.sqlite3'
        self.config = types.ModuleType('local_config')
        self.config.__file__ = str(self.root / 'code' / 'local_config.py')
        self.config.DURABLE_DB_PATH = str(self.ledger)
        self.config.DURABLE_MONITOR_READONLY_SID = SID
        self.checker = mock.Mock()

    def test_unset_policy_retains_strict_callback(self):
        del self.config.DURABLE_MONITOR_READONLY_SID
        startup.canonical_storage(self.ledger, self.config, storage_check=self.checker)
        self.checker.assert_called_once_with(self.ledger)

    def test_only_service_configuration_binds_canonical_ledger(self):
        startup.canonical_storage(self.ledger, self.config, storage_check=self.checker)
        self.checker.assert_called_once_with(self.ledger, monitor_sid=SID, config_path=Path(self.config.__file__), monitor_author_sid=None)
        for invalid in (types.SimpleNamespace(**vars(self.config)), {}):
            with self.assertRaises(startup.StartupError):
                startup.canonical_storage(self.ledger, invalid, storage_check=self.checker) if not isinstance(invalid, dict) else startup.private_storage(self.ledger, monitor_sid=SID)
        with self.assertRaises(startup.StartupError):
            startup.canonical_storage(self.root / 'inbox' / 'handoff', self.config, storage_check=self.checker)

    def test_role_collision_invalid_and_noncanonical_settings_fail(self):
        for sid, author in ((SID, SID), (False, None), ('', None), (OTHER, 'S-1-5-21-0111-222-333-1002')):
            self.config.DURABLE_MONITOR_READONLY_SID = sid
            self.config.HISTORICAL_REQUEST_AUTHOR_SID = author
            with self.subTest(sid=sid, author=author), self.assertRaises(startup.StartupError):
                startup.canonical_storage(self.ledger, self.config, storage_check=self.checker)
        self.checker.assert_not_called()

    def test_exception_cannot_reuse_unrestricted_service_sid(self):
        with self.assertRaises(startup.StartupError):
            startup.private_storage(self.ledger, service_sid=SID, monitor_sid=SID,
                                    config_path=Path(self.config.__file__))

    def test_native_failure_timeout_unknown_sid_or_incomplete_receipt_fails_closed(self):
        for result in (types.SimpleNamespace(returncode=1, stdout='{"safe":false}'),
                       types.SimpleNamespace(returncode=0, stdout='{}'),
                       types.SimpleNamespace(returncode=0, stdout='PRIVATE_USER SECRET_PATH'),
                       types.SimpleNamespace(returncode=0, stdout='{"safe":true,"extra":true}')):
            with mock.patch('monitor_storage.subprocess.run', return_value=result), self.assertRaises(startup.StartupError):
                policy.validate(self.ledger, SID, self.config.__file__)
        for error in (OSError('PRIVATE'), subprocess.TimeoutExpired('PRIVATE', 15)):
            with mock.patch('monitor_storage.subprocess.run', side_effect=error), self.assertRaises(startup.StartupError):
                policy.validate(self.ledger, SID, self.config.__file__)
        good = types.SimpleNamespace(returncode=0, stdout='{"safe":true}')
        with mock.patch('monitor_storage.subprocess.run', return_value=good) as child:
            policy.validate(self.ledger, SID, self.config.__file__)
            self.assertEqual(child.call_args.kwargs['timeout'], 15)
            self.assertIn('-I', child.call_args.args[0])
            self.assertNotIn(SID, ' '.join(child.call_args.args[0]))

    def test_bad_policy_stops_before_writable_ledger_or_device_access(self):
        self.config.HISTORICAL_REQUEST_AUTHOR_SID = SID
        engine = types.SimpleNamespace(config=self.config)
        with mock.patch('durable_sync.Ledger') as ledger, mock.patch('durable_sync.worker_lock') as lock:
            with self.assertRaises(startup.StartupError):
                durable_sync.run(engine)
            ledger.assert_not_called()
            lock.assert_not_called()

    def test_recovery_disabled_never_opens_monitor_or_inbox_paths(self):
        from historical_attendance.service_recovery import service_tick
        self.config.HISTORICAL_RECOVERY_ENABLED = False
        with mock.patch('historical_attendance.service_recovery.canonical_storage', side_effect=AssertionError):
            self.assertIsNone(service_tick(None, self.config, None, None, {}))
        self.assertNotIn('DURABLE_MONITOR_READONLY_SID', __import__('manager_app.profile', fromlist=['FIELDS']).FIELDS)


@unittest.skipUnless(sys.platform == 'win32', 'Windows native descriptor/Authz API required')
class NativeDescriptorTests(unittest.TestCase):
    def test_windows_mapping_and_real_current_token_authz_without_acl_mutation(self):
        import win32api
        import win32con
        import win32security as security
        from monitor_storage_windows import Authz
        token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        context = Authz(token=token)
        try:
            groups, privileges = context.identity_details()
            self.assertTrue(groups)
            self.assertIsInstance(privileges, list)
            for mask in (policy.READ, policy.DIRECTORY_READ, 2, policy.FULL):
                sd = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                    'O:SYG:SYD:P(A;;0x' + format(mask, 'x') + ';;;WD)', 1)
                self.assertEqual(context.maximum(sd), mask)
            self.assertEqual(security.MapGenericMask(0x80000000, (policy.READ, 0x120116, 0x1200A0, policy.FULL)),
                             policy.normalize_mask(0x80000000))
        finally:
            context.close()
            token.Close()

    def test_non_system_inspection_fails_before_account_resolution(self):
        import win32api
        import win32con
        import win32security as security
        from monitor_storage_windows import inspect
        token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            current = security.ConvertSidToStringSid(security.GetTokenInformation(token, security.TokenUser)[0])
        finally:
            token.Close()
        if current == SYSTEM:
            self.skipTest('This negative test requires a non-System development token')
        with mock.patch('win32security.LookupAccountSid', side_effect=AssertionError('No domain SID lookup allowed')):
            with self.assertRaisesRegex(policy.PolicyError, 'requires_localsystem'):
                inspect(Path.cwd() / 'synthetic.sqlite3', SID, Path.cwd() / 'synthetic-config.py')

@unittest.skipUnless(sys.platform == 'win32', 'Windows adapter required')
class NativeInspectionAdapterTests(unittest.TestCase):
    """Fake System identity/API responses only; never impersonate or query a domain."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='monitor-native-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.ledger = self.root / 'state' / 'sync.sqlite3'
        self.config = self.root / 'code' / 'local_config.py'
        self.ledger.parent.mkdir()
        self.config.parent.mkdir()
        self.ledger.write_bytes(b'SYNTHETIC')
        self.config.write_bytes(b'# SYNTHETIC; never imported')
        self.marker = self.ledger.parent / 'activation.json'
        self.marker.write_bytes(b'SYNTHETIC')
        import win32security as security
        stack = self.enterContext(contextlib.ExitStack())
        self.token = mock.Mock()
        stack.enter_context(mock.patch('win32security.OpenProcessToken', return_value=self.token))
        stack.enter_context(mock.patch('win32security.GetTokenInformation',
            return_value=(security.ConvertStringSidToSid(SYSTEM), 0)))
        self.lookup = stack.enter_context(mock.patch('win32security.LookupAccountSid',
            return_value=('synthetic', 'SYNTHETIC', 1)))
        self.drive = stack.enter_context(mock.patch('win32file.GetDriveType', return_value=3))
        self.volume = stack.enter_context(mock.patch('win32api.GetVolumeInformation',
            return_value=('', 0, 0, 0, 'NTFS')))
        self.attributes = stack.enter_context(mock.patch('win32api.GetFileAttributes', return_value=0))
        self.authz = stack.enter_context(mock.patch('monitor_storage_windows.Authz'))
        self.authz.return_value.identity_details.return_value = ([], [])
        self.authz.return_value.maximum.return_value = 0
        self.lsa = stack.enter_context(mock.patch('win32security.LsaOpenPolicy'))
        self.rights = stack.enter_context(mock.patch('win32security.LsaEnumerateAccountRights', return_value=()))
        stack.enter_context(mock.patch('monitor_storage_windows.descriptor',
            return_value=(object(), ADMINS, True, [])))
        self.check = stack.enter_context(mock.patch('monitor_storage_windows.check_descriptor'))

    def inspect(self):
        from monitor_storage_windows import inspect
        inspect(self.ledger, SID, self.config)

    def test_all_immediate_children_and_configuration_are_inspected_and_handles_closed(self):
        self.inspect()
        kinds = [c.args[3] for c in self.check.call_args_list]
        self.assertEqual(kinds.count('ledger'), 1)
        self.assertEqual(kinds.count('child'), 1)
        self.assertIn('config', kinds)
        self.assertIn('configdir', kinds)
        self.assertIn('ancestor', kinds)
        self.token.Close.assert_called_once()
        self.lsa.return_value.Close.assert_called_once()

    def test_unknown_group_privileged_and_service_accounts_fail_closed(self):
        self.lookup.side_effect = OSError('synthetic unknown SID')
        with self.assertRaises(OSError):
            self.inspect()
        self.lookup.side_effect = None
        for account_type, groups, privileges in ((2, [], []), (1, [ADMINS], []),
                                                  (1, [], ['SeBackupPrivilege'])):
            self.lookup.return_value = ('synthetic', 'SYNTHETIC', account_type)
            self.authz.return_value.identity_details.return_value = (groups, privileges)
            with self.assertRaises(policy.PolicyError):
                self.inspect()
        self.authz.return_value.identity_details.return_value = ([], [])
        self.rights.return_value = ('SeServiceLogonRight',)
        with self.assertRaisesRegex(policy.PolicyError, 'service_identity'):
            self.inspect()
        self.check.assert_not_called()

    def test_local_ntfs_and_reparse_restrictions_precede_authz(self):
        self.drive.return_value = 4
        with self.assertRaisesRegex(policy.PolicyError, 'volume'):
            self.inspect()
        self.drive.return_value = 3
        self.volume.return_value = ('', 0, 0, 0, 'FAT32')
        with self.assertRaisesRegex(policy.PolicyError, 'volume'):
            self.inspect()
        self.volume.return_value = ('', 0, 0, 0, 'NTFS')
        self.attributes.return_value = 0x400
        with self.assertRaisesRegex(policy.PolicyError, 'reparse'):
            self.inspect()
        self.authz.assert_not_called()

    def test_cloud_colocation_and_child_limit_fail_closed(self):
        with mock.patch.dict('os.environ', {'OneDrive': str(self.root)}):
            with self.assertRaises(policy.PolicyError):
                self.inspect()
        from monitor_storage_windows import inspect
        with self.assertRaises(policy.PolicyError):
            inspect(self.ledger, SID, self.marker)
        with mock.patch.object(Path, 'iterdir', return_value=iter([self.marker] * 257)):
            with self.assertRaisesRegex(policy.PolicyError, 'child_limit'):
                self.inspect()
        self.authz.assert_not_called()

    def test_native_s4u_or_lsa_failure_is_not_silently_downgraded(self):
        self.authz.side_effect = policy.PolicyError('synthetic S4U unavailable')
        with self.assertRaises(policy.PolicyError):
            self.inspect()
        self.authz.side_effect = None
        self.lsa.side_effect = OSError('synthetic LSA unavailable')
        with self.assertRaises(OSError):
            self.inspect()
        self.check.assert_not_called()

    def test_recovery_author_group_or_sid_history_membership_is_refused(self):
        from monitor_storage_windows import inspect
        self.authz.return_value.identity_details.return_value = ([OTHER], [])
        with self.assertRaisesRegex(policy.PolicyError, 'recovery_role_conflict'):
            inspect(self.ledger, SID, self.config, OTHER)
        self.rights.assert_not_called()
        self.check.assert_not_called()
