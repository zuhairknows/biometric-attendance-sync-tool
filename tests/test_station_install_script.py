import hashlib
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deployment" / "Install-StationTerminal.ps1"


def normalized_sha256(path):
    text = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class InstallStationTerminalScriptTests(unittest.TestCase):
    """The install script refuses files it does not know, so its pins must match the source."""

    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")

    def pinned_new(self, name):
        match = re.search(re.escape("'" + name + "'") + r"\s*=\s*@\{.*?New = '([0-9a-f]{64})'", self.text, re.S)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_new_versions_match_the_source(self):
        for name in ("erpnext_sync.py", "realtime_capture.py"):
            self.assertEqual(self.pinned_new(name), normalized_sha256(ROOT / name), name)

    def test_script_is_ascii_for_windows_powershell(self):
        self.assertTrue(all(ord(ch) < 128 for ch in self.text))

    def test_python_one_liners_use_single_quotes_only(self):
        # Windows PowerShell 5.1 does not escape double quotes in native arguments.
        probe = re.search(r"^\$probe = '(.*)'$", self.text, re.M)
        self.assertIsNotNone(probe)
        self.assertNotIn('"', probe.group(1))


if __name__ == "__main__":
    unittest.main()
