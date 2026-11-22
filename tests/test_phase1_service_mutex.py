"""New service-token probe only. No actual service, mutex or ledger access."""
import contextlib
import ctypes
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'win32', 'Windows pywin32 required')
class ServiceMutexTests(unittest.TestCase):
    def test_single_service_token_observation_and_fail_closed_runner(self):
        path = ROOT / 'scripts' / 'phase1_service_mutex_diagnostic.py'
        spec = importlib.util.spec_from_file_location('service_mutex_unit', path)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)  # Class declaration only; never SCM entry.
        for handle, error, token in ((None, 5, probe.SID), (0x100000001, 0, probe.SID), (None, 0, 'WRONG-TOKEN')):
            with self.subTest(handle=handle, error=error, token=token):
                api = types.SimpleNamespace()
                calls = []
                real_get_error = ctypes.get_last_error

                def create(*args):
                    self.assertEqual(args, (None, False, r'Global\ERPNextBiometricPushService-DurableWorker'))
                    self.assertIs(api.CreateMutexW.restype, ctypes.wintypes.HANDLE)
                    self.assertEqual(api.CreateMutexW.argtypes, (ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR))
                    calls.append('create');ctypes.set_last_error(error)
                    return handle

                def capture():
                    calls.append('get_error')
                    return real_get_error()

                def close(value):
                    self.assertEqual(value, handle)
                    calls.append('close');ctypes.set_last_error(999)
                    return True

                api.CreateMutexW = mock.Mock(side_effect=create)
                api.CloseHandle = mock.Mock(side_effect=close)
                stream = io.StringIO()
                wrapped = mock.Mock(wraps=stream)
                wrapped.fileno.return_value = 123
                report = mock.Mock()
                report.open.return_value = contextlib.nullcontext(wrapped)
                with mock.patch.object(probe, 'process_sid', return_value=token), \
                     mock.patch.object(ctypes, 'WinDLL', return_value=api) as dll, \
                     mock.patch.object(ctypes, 'get_last_error', side_effect=capture), \
                     mock.patch.object(probe.os, 'fsync') as fsync:
                    probe.run_probe(report)
                report.open.assert_called_once_with('x', encoding='ascii')
                fsync.assert_called_once_with(123)
                value = json.loads(stream.getvalue())
                self.assertFalse(value['ledger_opened'])
                self.assertEqual(value['sid'], token)
                if token == probe.SID:
                    dll.assert_called_once_with('kernel32', use_last_error=True)
                    self.assertEqual(calls[:2], ['create', 'get_error'])
                    self.assertEqual(value['win32_error'], error)
                    self.assertEqual(value['handle_opened'], bool(handle))
                    if handle:
                        self.assertTrue(value['handle_closed'])
                        self.assertEqual(calls, ['create', 'get_error', 'close', 'get_error'])
                    else:
                        self.assertEqual(calls, ['create', 'get_error'])
                else:
                    dll.assert_not_called()
                    self.assertEqual(value['status'], 'FAIL')
        runner = ROOT / 'scripts' / 'run_phase1_service_mutex.ps1'
        text = runner.read_text()
        for forbidden in ('Set-Acl', 'Set-NetFirewallRule', 'New-NetFirewallRule', 'Remove-Item',
                          'ERPNextBiometricPushService', 'sqlite3', '--provision', "'recover'"):
            self.assertNotIn(forbidden, text)
        self.assertIn('PathName=$original.PathName', text)
        self.assertIn('[IO.FileMode]::CreateNew', text)
        script = "$e=$null;$t=$null;[void][Management.Automation.Language.Parser]::ParseFile('" + str(runner) + "',[ref]$t,[ref]$e);if($e.Count){$e;exit 1}"
        result = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
