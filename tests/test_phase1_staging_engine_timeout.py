"""Synthetic HTTPS timeout tests. Never invoke a real endpoint or service."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.phase1_staging_engine_timeout import CRASH_HELPER_HASH, helpers

REPOSITORY = Path(__file__).resolve().parents[1]
ROOT = REPOSITORY


def setUpModule():
    global ROOT, _frozen
    from windows_powershell_environment import native_modules
    unittest.enterModuleContext(native_modules())
    from frozen_phase1 import materialize
    _frozen, ROOT = materialize(REPOSITORY)


def tearDownModule():
    _frozen.cleanup()

OPENSSL = shutil.which('openssl') or r'C:\Program Files\Git\usr\bin\openssl.exe'


class EngineTimeoutTests(unittest.TestCase):
    def test_changed_helper_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'phase1_staging_engine_crash.py').write_text('raise AssertionError("must never execute")')
            with mock.patch('scripts.phase1_staging_engine_timeout.__file__', str(root / 'runner.py')):
                with self.assertRaisesRegex(ValueError, 'step5c1_helper_hash_mismatch'):
                    helpers()

    def test_exact_reviewed_helpers(self):
        shared, base = helpers()
        self.assertEqual(hashlib.sha256(Path(shared.__file__).read_bytes()).hexdigest(), CRASH_HELPER_HASH)
        self.assertEqual(base.REVISION, '4256cd6201b17dcda3f9d078552acf1d3c58b3f0')

    @unittest.skipUnless(sys.platform == 'win32' and Path(OPENSSL).is_file(), 'Windows and existing OpenSSL required')
    def test_actual_timeout_conservative_retries_and_positive_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            stage = parent / 'FPF-Phase1-Staging-timeout-test'
            stage.mkdir()
            prior = stage / 'step5c1-prior'
            prior.mkdir()
            (prior / 'result.json').write_bytes(b'{"status":"PASS"}')
            config = stage / 'request.cnf'
            config.write_text('[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n')
            result = subprocess.run([OPENSSL, 'req', '-quiet', '-x509', '-newkey', 'rsa:2048', '-noenc',
                '-days', '1', '-config', str(config), '-subj', '/CN=localhost',
                '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
                '-addext', 'basicConstraints=critical,CA:TRUE',
                '-keyout', str(stage / 'localhost-key.pem'), '-out', str(stage / 'localhost-cert.pem')],
                capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, 'Synthetic certificate generation failed')
            originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.rglob('*') if p.is_file()}
            environment = dict(os.environ, LOCALAPPDATA=str(parent),
                HTTPS_PROXY='http://192.0.2.1:9999', HTTP_PROXY='http://192.0.2.1:9999', NO_PROXY='')
            result = subprocess.run([sys.executable, '-I', '-S', '-B',
                str(ROOT / 'scripts' / 'phase1_staging_engine_timeout.py'), '--stage-root', str(stage)],
                env=environment, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'PASS')
            self.assertTrue(all(report['checks'].values()))
            self.assertEqual(len(report['checks']), 42)
            self.assertEqual(report['post_count'], 2)
            self.assertEqual(report['mock_records'], 2)
            self.assertEqual(report['confirmation_reads'], 6)
            output = stage / report['attempt'] / 'data'
            self.assertEqual(json.loads((output / 'result.json').read_text()), report)
            events = json.loads((output / 'transport-events.json').read_text())
            self.assertEqual([e['method'] for e in events], ['POST', 'POST'] + ['GET'] * 6)
            self.assertIs(events[0]['timeout'], True)
            initial = json.loads((output / 'accepted_timeout.json').read_text())
            self.assertEqual(initial['rows'][0]['error_category'], 'transport')
            for label in ('unverified_retry_1', 'unverified_retry_2', 'unverified_retry_3', 'empty_confirmation', 'null_confirmation'):
                held = json.loads((output / (label + '.json')).read_text())
                self.assertEqual(held['uncertain'], 1)
                self.assertEqual(held['rows'][0]['state'], 'retry')
                self.assertEqual(held['rows'][1]['state'], 'synced')
                self.assertEqual(held['post_count'], 2)
            confirmed = json.loads((output / 'positive_confirmation.json').read_text())
            self.assertEqual(confirmed['uncertain'], 0)
            self.assertEqual(confirmed['rows'][0]['state'], 'duplicate')
            self.assertEqual(confirmed['rows'][0]['error_category'], 'destination_confirmed')
            for path, digest in originals.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main()
