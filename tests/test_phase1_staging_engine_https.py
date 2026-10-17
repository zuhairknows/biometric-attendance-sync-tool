"""Step 5B synthetic tests; never operate SCM, firewall, devices or Frappe."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

from scripts.phase1_staging_engine_https import Blocked, HASHES, Policy, transport

ROOT = Path(__file__).resolve().parents[1]
OPENSSL = shutil.which('openssl') or r'C:\Program Files\Git\usr\bin\openssl.exe'


class EngineHttpsTests(unittest.TestCase):
    def test_pinned_source_bytes_are_unchanged(self):
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)

    def test_guard_allows_only_ledger_and_fixed_acl_child(self):
        root = Path(tempfile.gettempdir()).resolve() / 'step5b-unit'
        db = root / 'data' / 'synthetic.sqlite3'
        guard = Policy(root, root.parent / 'cert.pem', root.parent / 'key.pem', db, 'fixed-reviewed-script')
        guard.port = 12345
        guard.audit('socket.connect', (None, ('127.0.0.1', 12345)))
        guard.audit('sqlite3.connect', (str(db),))
        guard.audit('sqlite3.connect', (db.as_uri() + '?mode=ro',))
        guard.audit('subprocess.Popen', ('powershell.exe', ['powershell.exe', '-NoProfile',
            '-NonInteractive', '-Command', 'fixed-reviewed-script'], None,
            dict(FPF_LEDGER_DIRECTORY=str(db.parent), FPF_LEDGER_SERVICE_SID='', PATH=guard.powershell_directory)))
        self.assertEqual(guard.acl_checks, 1)
        cases = [
            ('subprocess.Popen', ('powershell.exe', ['powershell.exe', '-Command', 'changed'], None, {})),
            ('sqlite3.connect', (str(root.parent / 'other.sqlite3'),)),
            ('socket.connect', (None, ('127.0.0.1', 4370))),
            ('socket.connect', (None, ('192.0.2.1', 443))),
            ('socket.getaddrinfo', ('must-not-resolve.invalid', 443)),
            ('socket.__new__', (None, socket.AF_INET, socket.SOCK_DGRAM, 0)),
            ('open', (str(root / 'code' / 'durable_sync.py'), 'w', os.O_WRONLY)),
            ('open', (str(root.parent / 'cert.pem'), 'w', os.O_WRONLY)),
            ('os.remove', (str(root.parent / 'old-evidence'), -1)),
            ('ctypes.dlopen', ('other-library',)),
            ('ctypes.dlsym', (None, 'CreateProcessW')),
        ]
        for event, args in cases:
            with self.subTest(event=event), self.assertRaises(Blocked):
                guard.audit(event, args)

    def test_transport_rejects_wrong_origins_before_opener(self):
        class NeverOpen:
            def open(self, *args, **kwargs):
                raise AssertionError('Network opener must not be reached')
        request = transport(NeverOpen(), 'https://127.0.0.1:12345')
        for url in ('http://127.0.0.1:12345/health', 'https://localhost:12345/health',
                    'https://127.0.0.1:12346/health', 'https://must-not-resolve.invalid/health',
                    'https://127.0.0.1:12345/health#fragment'):
            with self.subTest(url=url), self.assertRaises(Blocked):
                request('GET', url)
        with self.assertRaises(Blocked):
            request('POST', 'https://127.0.0.1:12345/api/resource/Employee')

    @unittest.skipUnless(sys.platform == 'win32' and Path(OPENSSL).is_file(), 'Windows and existing OpenSSL required')
    def test_actual_engine_with_hostile_proxies_and_retained_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            stage = parent / 'FPF-Phase1-Staging-synthetic-test'
            stage.mkdir()
            sentinel = stage / 'earlier-evidence.txt'
            sentinel.write_bytes(b'preserve all prior evidence')
            config = stage / 'request.cnf'
            config.write_text('[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n')
            generate = subprocess.run([OPENSSL, 'req', '-quiet', '-x509', '-newkey', 'rsa:2048', '-noenc',
                '-days', '1', '-config', str(config), '-subj', '/CN=localhost',
                '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
                '-addext', 'basicConstraints=critical,CA:TRUE',
                '-keyout', str(stage / 'localhost-key.pem'), '-out', str(stage / 'localhost-cert.pem')],
                capture_output=True, timeout=30)
            self.assertEqual(generate.returncode, 0, 'Synthetic certificate creation failed')
            original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.iterdir()}
            environment = dict(os.environ, LOCALAPPDATA=str(parent),
                HTTPS_PROXY='http://192.0.2.1:9999', HTTP_PROXY='http://192.0.2.1:9999', NO_PROXY='')
            result = subprocess.run([sys.executable, '-I', '-S', '-B',
                str(ROOT / 'scripts' / 'phase1_staging_engine_https.py'), '--stage-root', str(stage)],
                env=environment, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'PASS')
            self.assertTrue(all(report['checks'].values()))
            self.assertEqual(report['mock_records'], 1)
            for path, digest in original.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            attempt = stage / report['attempt']
            self.assertEqual(json.loads((attempt / 'data' / 'result.json').read_text()), report)
            self.assertFalse((stage / 'MUST-NOT-CREATE.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
