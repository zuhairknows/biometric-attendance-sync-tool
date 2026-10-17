"""Synthetic process-death tests. No SCM calls, devices or external network."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.phase1_staging_engine_crash import EXPECTED_EXITS, HELPER_HASH, helper

ROOT = Path(__file__).resolve().parents[1]
OPENSSL = shutil.which('openssl') or r'C:\Program Files\Git\usr\bin\openssl.exe'


class EngineCrashTests(unittest.TestCase):
    def test_reuses_exact_reviewed_https_guard_without_changing_source(self):
        path = ROOT / 'scripts' / 'phase1_staging_engine_https.py'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), HELPER_HASH)
        self.assertEqual(helper().REVISION, '4256cd6201b17dcda3f9d078552acf1d3c58b3f0')
        self.assertEqual(EXPECTED_EXITS, dict(provision=0, before_commit=71, after_commit=72, restart=0))

    @unittest.skipUnless(sys.platform == 'win32' and Path(OPENSSL).is_file(), 'Windows and existing OpenSSL required')
    def test_real_crashes_rollback_restart_delivery_and_retained_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            stage = parent / 'FPF-Phase1-Staging-crash-test'
            stage.mkdir()
            (stage / 'step5b-previous-evidence.json').write_bytes(b'{"unchanged":true}')
            config = stage / 'request.cnf'
            config.write_text('[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n')
            result = subprocess.run([OPENSSL, 'req', '-quiet', '-x509', '-newkey', 'rsa:2048', '-noenc',
                '-days', '1', '-config', str(config), '-subj', '/CN=localhost',
                '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
                '-addext', 'basicConstraints=critical,CA:TRUE',
                '-keyout', str(stage / 'localhost-key.pem'), '-out', str(stage / 'localhost-cert.pem')],
                capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, 'Synthetic certificate generation failed')
            original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.iterdir()}
            environment = dict(os.environ, LOCALAPPDATA=str(parent),
                HTTPS_PROXY='http://192.0.2.1:9999', HTTP_PROXY='http://192.0.2.1:9999', NO_PROXY='')
            result = subprocess.run([sys.executable, '-I', '-S', '-B',
                str(ROOT / 'scripts' / 'phase1_staging_engine_crash.py'), '--stage-root', str(stage)],
                env=environment, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'PASS')
            self.assertTrue(all(report['checks'].values()))
            self.assertEqual(len(report['checks']), 23)
            self.assertEqual(report['worker_exit_codes'], EXPECTED_EXITS)
            self.assertEqual(report['mock_records'], 1)
            output = stage / report['attempt'] / 'data'
            self.assertEqual(json.loads((output / 'result.json').read_text()), report)
            for path, digest in original.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            before = json.loads((output / 'before_commit.json').read_text())
            after = json.loads((output / 'after_commit.json').read_text())
            restarted = json.loads((output / 'restart.json').read_text())
            self.assertEqual(before['count'], 0)
            self.assertIsNone(before['watermark'])
            self.assertEqual(after['states'], {'pending': 1})
            self.assertEqual(restarted['states'], {'synced': 1})
            self.assertTrue((output / 'before_commit-synthetic.sqlite3-journal').is_file())
            for phase in EXPECTED_EXITS:
                self.assertEqual((output / (phase + '-stdout.txt')).read_bytes(), b'')
                self.assertEqual((output / (phase + '-stderr.txt')).read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
