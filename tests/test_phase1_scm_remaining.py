"""Remaining-session preparation only. Never execute SCM or firewall commands."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
class RemainingSessionTests(unittest.TestCase):
    def test_only_remaining_phases_and_no_runtime_recreation(self):
        path = ROOT / 'scripts' / 'run_phase1_staging_scm.ps1'
        script = r'''
$ErrorActionPreference='Stop';$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(RUNNER,[ref]$t,[ref]$e)
if($e.Count){throw 'Runner syntax failed'}
$node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-SessionPhases'},$true)
. ([ScriptBlock]::Create($node.Extent.Text))
$remainingOnly=$true
if((@(Get-SessionPhases) -join ',') -ne 'inflight,recover'){throw 'Idle scenario repeated or recovery omitted'}
$remainingOnly=$false
if((@(Get-SessionPhases) -join ',') -ne 'idle,inflight,recover'){throw 'Original session changed'}
# Inspect control flow without invoking the runner or its side-effecting body.
$pack=$ast.Find({param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.Extent.Text -like '*--package-runtime*'},$true)
$guard=$pack.Parent
while($null -ne $guard -and $guard -isnot [Management.Automation.Language.IfStatementAst]){$guard=$guard.Parent}
if($null -eq $guard -or $guard.Clauses[0].Item1.Extent.Text -ne '-not $remainingOnly'){throw 'Existing runtime may be recreated'}
$newRules=$ast.Find({param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Add-FreshFirewall'},$true)
$guard=$newRules.Parent
while($null -ne $guard -and $guard -isnot [Management.Automation.Language.IfStatementAst]){$guard=$guard.Parent}
if($null -eq $guard -or $guard.Clauses[0].Item1.Extent.Text -ne '$FreshRuntime'){throw 'Remaining session may create firewall rules'}
Write-Output 'PASS: two remaining phases; runtime and firewall creation excluded'
'''.replace('RUNNER', "'" + str(path).replace("'", "''") + "'")
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS:', result.stdout)
        self.assertEqual(result.stderr, '')
        text = path.read_text()
        self.assertIn("if ($remainingOnly) {14} else {18}", text)
        self.assertIn('accepted_idle_evidence_reused', text)
        self.assertIn("if (-not $remainingOnly) { Invoke-Host @('--preflight') 'preflight' }", text)
        self.assertIn('retained.runtime_manifest_sha256', text)
        self.assertIn("$checks['stop_preserved_pending_without_send']", text)
        self.assertIn("$checks['restart_recovered_same_pending']", text)
        self.assertIn("$checks['original_registration_restored_stopped']", text)


if __name__ == '__main__':
    unittest.main()
