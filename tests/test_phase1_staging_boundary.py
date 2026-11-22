"""Step 3 preparation checks; never change real ACLs, firewall policy or services."""
import ipaddress
from pathlib import Path
import re
import shutil
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'prepare_phase1_staging_boundary.ps1'
POWERSHELL = shutil.which('powershell.exe')


class StagingBoundaryTests(unittest.TestCase):
    def test_no_service_controls_or_shared_interpreter_rules(self):
        source = SCRIPT.read_text()
        for forbidden in ('Start-Service', 'Stop-Service', 'Restart-Service', 'New-Service',
                          'Set-Service', 'Set-NetFirewallProfile', 'Remove-NetFirewallRule',
                          'ERPNextBiometricPushService', 'erpnext_sync', 'local_config'):
            self.assertNotIn(forbidden, source)
        self.assertIn('& sc.exe showsid $serviceName', source)
        self.assertIn("Join-Path $code 'FPFPhase1Staging.exe'", source)
        self.assertIn("Join-Path $code 'runtime\\python.exe'", source)
        self.assertIn('PREPARED_NOT_RUNTIME_VERIFIED', source)
        self.assertIn('-PolicyStore ActiveStore', source)
        self.assertIn('[IO.FileMode]::CreateNew', source)
        self.assertNotIn('Set-Content', source)

    def test_address_policy_covers_all_non_loopback_addresses(self):
        source = SCRIPT.read_text().split('$nonLoopback = @(', 1)[1].split(')', 1)[0]
        ranges = []
        for start, end in re.findall("'([^']+)-([^']+)'", source):
            ranges.append((ipaddress.ip_address(start), ipaddress.ip_address(end)))
        self.assertEqual(len(ranges), 4)
        # Boundary values prove coverage without opening sockets or resolving DNS.
        for address in ('0.0.0.0', '10.0.0.1', '126.255.255.255', '128.0.0.0',
                        '192.0.2.1', '255.255.255.255', '::', '::2', '::ffff:192.0.2.1',
                        'fe80::1', 'ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'):
            value = ipaddress.ip_address(address)
            self.assertTrue(any(value.version == low.version and low <= value <= high for low, high in ranges))
        for address in ('127.0.0.1', '127.255.255.255', '::1'):
            value = ipaddress.ip_address(address)
            self.assertFalse(any(value.version == low.version and low <= value <= high for low, high in ranges))

    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
    def test_partial_state_resume_without_real_firewall_or_acl_changes(self):
        fixture = Path(__file__).parent / 'fixtures' / 'phase1_staging_resume_test.ps1'
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(fixture),
                                 '-Source', str(SCRIPT)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: partial state', result.stdout)
        self.assertEqual(result.stderr, '')

    @unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
    def test_powershell_syntax_and_synthetic_acl_verification(self):
        # Real .NET ACL objects, but mocked filesystem commands: zero ACL mutations.
        quoted = "'" + str(SCRIPT).replace("'", "''") + "'"
        code = r"""
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(SCRIPT_PATH,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Syntax error' }
foreach ($function in $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $true)) {
    . ([ScriptBlock]::Create($function.Extent.Text))
}
$serviceSid='S-1-5-80-1-2-3-4-5'
function New-Item { param($ItemType,$Path,$ErrorAction) }
function Get-Item { param($LiteralPath,[switch]$Force) [pscustomobject]@{PSIsContainer=$true; Attributes=[IO.FileAttributes]::Directory} }
function Set-Acl { param($LiteralPath,$AclObject) $script:savedAcl=$AclObject }
function Get-Acl { param($LiteralPath) return $script:savedAcl }
foreach ($rights in @('ReadAndExecute','Modify')) {
    New-PrivateStagingDirectory -Path 'synthetic-only' -ServiceRights $rights
    if ($savedAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]).Count -ne 3) { throw 'Wrong ACE count' }
}
# An extra broad allow must cause refusal, not be ignored by verification.
function Get-Acl {
    param($LiteralPath)
    $script:savedAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new('S-1-1-0'), 'Read', 'Allow'))
    return $script:savedAcl
}
$refused=$false
try { New-PrivateStagingDirectory -Path 'synthetic-only' -ServiceRights Modify }
catch { $refused=$true }
if (-not $refused) { throw 'Broad grant accepted' }
Write-Output 'PASS'
""".replace('SCRIPT_PATH', quoted)
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', code],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
