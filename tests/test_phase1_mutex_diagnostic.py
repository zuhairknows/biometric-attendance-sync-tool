"""Only the native error diagnostic; every Windows API call is mocked."""
import contextlib
import ctypes
from ctypes import wintypes
import io
import json
import types
import unittest
from unittest import mock

from scripts import phase1_staging_scm_host as host


@unittest.skipUnless(hasattr(ctypes, 'WinDLL'), 'Windows ctypes required')
class MutexDiagnosticTests(unittest.TestCase):
    def test_native_error_is_captured_before_close_and_printed_without_ledger_access(self):
        for handle, error in ((None, 5), (0x100000001, 183), (0x100000002, 0)):
            with self.subTest(handle=handle, error=error):
                calls = []
                api = types.SimpleNamespace()

                def create(attributes, initial_owner, name):
                    self.assertEqual(api.CreateMutexW.argtypes, (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR))
                    self.assertIs(api.CreateMutexW.restype, wintypes.HANDLE)
                    self.assertIsNone(attributes)
                    self.assertIs(initial_owner, False)
                    self.assertEqual(name, r'Global\ERPNextBiometricPushService-DurableWorker')
                    calls.append('CreateMutexW')
                    ctypes.set_last_error(error)
                    return handle

                get_error = ctypes.get_last_error

                def capture_error():
                    calls.append('GetLastError')
                    return get_error()

                def close(value):
                    self.assertEqual(value, handle)  # Retain the full 64-bit HANDLE.
                    self.assertEqual(api.CloseHandle.argtypes, (wintypes.HANDLE,))
                    self.assertIs(api.CloseHandle.restype, wintypes.BOOL)
                    calls.append('CloseHandle')
                    ctypes.set_last_error(999)  # Later calls must not replace evidence.
                    return True

                api.CreateMutexW = mock.Mock(side_effect=create)
                api.CloseHandle = mock.Mock(side_effect=close)
                output = io.StringIO()
                flags = types.SimpleNamespace(**{name: getattr(host.sys.flags, name)
                                                for name in dir(host.sys.flags) if not name.startswith('_')})
                flags.isolated = flags.no_site = flags.dont_write_bytecode = True
                with mock.patch.object(ctypes, 'WinDLL', return_value=api) as dll, \
                     mock.patch.object(ctypes, 'get_last_error', side_effect=capture_error), \
                     mock.patch.object(host.sys, 'argv', ['host', '--mutex-error-only']), \
                     mock.patch.object(host.sys, 'flags', flags), \
                     mock.patch.object(host, 'context', side_effect=AssertionError('must not load configuration/ledger')), \
                     contextlib.redirect_stdout(output):
                    status = host.main()
                dll.assert_called_once_with('kernel32', use_last_error=True)
                self.assertEqual(calls, ['CreateMutexW', 'GetLastError'] + (['CloseHandle'] if handle else []))
                value = json.loads(output.getvalue())
                self.assertEqual(value['win32_error'], error)
                self.assertEqual(value['handle_opened'], bool(handle))
                self.assertEqual(status, 0 if handle else 1)
                if handle:
                    self.assertEqual(value['existed'], error == 183)
                    self.assertEqual(value['scope'], 'mutex_handle_only_no_wait_no_ledger')
                else:
                    self.assertEqual(value['code'], 'worker_lock_unavailable')


if __name__ == '__main__':
    unittest.main()
