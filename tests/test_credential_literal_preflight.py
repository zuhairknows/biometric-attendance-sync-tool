"""Synthetic-only tests; no integration imports or network requests."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    'credential_preflight', Path(__file__).resolve().parents[1]
    / 'scripts' / 'credential_literal_preflight.py')
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class CredentialPreflightTests(unittest.TestCase):
    def test_literals_and_missing(self):
        self.assertEqual(preflight.inspect_source("ERPNEXT_API_KEY = 'synthetic-key'"), {
            'ERPNEXT_API_KEY': 'PRESENT_LITERAL', 'ERPNEXT_API_SECRET': 'MISSING'})

    def test_empty(self):
        result = preflight.inspect_source("ERPNEXT_API_KEY = ''\nERPNEXT_API_SECRET = '   '")
        self.assertEqual(set(result.values()), {'EMPTY'})

    def test_duplicates_even_identical_are_ambiguous(self):
        result = preflight.inspect_source("ERPNEXT_API_KEY = 'a'\nERPNEXT_API_KEY = 'a'")
        self.assertEqual(result['ERPNEXT_API_KEY'], 'AMBIGUOUS')

    def test_conditional_override_is_ambiguous(self):
        result = preflight.inspect_source(
            "ERPNEXT_API_KEY = 'a'\nif flag:\n ERPNEXT_API_KEY = 'b'")
        self.assertEqual(result['ERPNEXT_API_KEY'], 'AMBIGUOUS')

    def test_dynamic_syntax_cannot_certify_literals(self):
        for extra in ("import os", "globals().update({})", "exec('pass')",
                      "from other_config import *", "del ERPNEXT_API_SECRET",
                      "if flag:\n ERPNEXT_API_SECRET = 'b'",
                      "ERPNEXT_API_SECRET: str = 'b'", "ERPNEXT_API_SECRET = value"):
            with self.subTest(extra=extra):
                result = preflight.inspect_source("ERPNEXT_API_KEY = 'a'\n" + extra)
                self.assertEqual(result['ERPNEXT_API_KEY'], 'DYNAMIC')

    def test_non_string_is_not_certified(self):
        for value in ("None", "123", "b'bytes'"):
            with self.subTest(value=value):
                self.assertEqual(preflight.inspect_source('ERPNEXT_API_KEY = ' + value)
                                 ['ERPNEXT_API_KEY'], 'DYNAMIC')

    def test_passive_other_settings_do_not_expose_them(self):
        result = preflight.inspect_source(
            "'''doc'''\nERPNEXT_API_KEY = 'a'\nERPNEXT_API_SECRET = 'b'\n"
            "devices = [{'port': 4370, 'password': 0, 'direction': None}]\ninterval = -1")
        self.assertEqual(set(result.values()), {'PRESENT_LITERAL'})

    def test_cli_read_only_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'local_config.py'
            original = b"ERPNEXT_API_KEY='synthetic-key'\nERPNEXT_API_SECRET='synthetic-secret'\n"
            path.write_bytes(original)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = preflight.main([str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(output.getvalue(), 'ERPNEXT_API_KEY: PRESENT_LITERAL\n'
                             'ERPNEXT_API_SECRET: PRESENT_LITERAL\n')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_syntax_and_file_errors_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'local_config.py'
            for content in (b"ERPNEXT_API_KEY='secret-syntax-error", b'\xff',
                            b'x' * (preflight.MAX_BYTES + 1)):
                path.write_bytes(content)
                output = io.StringIO()
                errors = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    self.assertEqual(preflight.main([str(path)]), 1)
                self.assertEqual(output.getvalue(), 'ERPNEXT_API_KEY: DYNAMIC\n'
                                 'ERPNEXT_API_SECRET: DYNAMIC\n')
                self.assertEqual(errors.getvalue(), '')

    def test_untrusted_source_is_never_executed(self):
        result = preflight.inspect_source("raise RuntimeError('synthetic-secret')")
        self.assertEqual(set(result.values()), {'DYNAMIC'})


if __name__ == '__main__':
    unittest.main()
