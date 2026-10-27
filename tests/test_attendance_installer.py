"""Installer preflight in a child shell with fake hashes/SCM; never touch production."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which('pwsh') or shutil.which('powershell')


@unittest.skipUnless(SHELL, 'PowerShell required for installer preflight tests')
class AttendanceInstallerTests(unittest.TestCase):
    def preflight(self, scenario):
        script = str(ROOT / 'scripts' / 'install_attendance_fix.ps1').replace("'", "''")
        # All reads and mutation commands are overridden. An accidental service/file
        # mutation fails the test, even if preflight unexpectedly reaches that branch.
        harness = r'''
$ErrorActionPreference = 'Stop'
$scenario = '__SCENARIO__'
function Get-FileHash {
    param($LiteralPath, $Algorithm)
    $installed = $LiteralPath.StartsWith('C:\Program Files\')
    $ledger = $LiteralPath.EndsWith('sync_ledger.py')
    $before = if ($ledger) { '5E6713142F432B808C7BBA50A5F55798733C5C981657E381F09576FDE7BC7A2D' } else { '50BECDEBEB006B2F150DDFF72DAFAC940201BC4D33ED52A5669F07F7AA18923F' }
    $after = if ($ledger) { '15BF03152CE450B9B5D37CBB9E43D62AE863CDF6AB8B0D9CCD1846DAEEACD9B2' } else { '48A7EA4DB376F2C2090E06BA7E87E7C84815AF5FF98C0BFA5D9DAD8C88267DBD' }
    $hash = if ($installed) { $before } else { $after }
    if ($installed -and ($scenario -eq 'already' -or ($scenario -eq 'mixed' -and $ledger))) { $hash = $after }
    if (($installed -and $scenario -eq 'unknown') -or (-not $installed -and $scenario -eq 'payload')) { $hash = 'BAD' }
    [pscustomobject]@{ Hash = $hash }
}
function Get-CimInstance {
    [pscustomobject]@{ State = 'Running'; PathName = '"C:\Program Files\FPFBiometricSyncPhase2\erpnext_sync_win.py"' }
}
function Stop-Service { throw 'MUTATION_ATTEMPTED' }
function Start-Service { throw 'MUTATION_ATTEMPTED' }
function Copy-Item { throw 'MUTATION_ATTEMPTED' }
function New-Item { throw 'MUTATION_ATTEMPTED' }
try { & '__SCRIPT__' } catch { Write-Output $_.Exception.Message; exit 1 }
'''.replace('__SCENARIO__', scenario).replace('__SCRIPT__', script)
        result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-Command', harness],
                                capture_output=True, text=True, timeout=30)
        self.assertNotIn('MUTATION_ATTEMPTED', result.stdout + result.stderr)
        return result

    def test_default_is_preflight_only(self):
        result = self.preflight('baseline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Preflight passed. No changes made.', result.stdout)

    def test_already_installed_is_noop(self):
        result = self.preflight('already')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already installed', result.stdout)

    def test_unknown_mixed_and_changed_payload_are_refused(self):
        for scenario, message in (
            ('unknown', 'differs from the inspected version'),
            ('mixed', 'Mixed installed versions'),
            ('payload', 'Repository payload hash mismatch'),
        ):
            with self.subTest(scenario=scenario):
                result = self.preflight(scenario)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stdout)
