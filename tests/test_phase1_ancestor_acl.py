"""ACL verification/resume tests. Set-Acl is always an in-memory fake."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest



def setUpModule():
    from windows_powershell_environment import native_modules
    unittest.enterModuleContext(native_modules())

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe')
REPAIR = ROOT / 'scripts' / 'repair_phase1_staging_ancestor_metadata.ps1'


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
class AncestorAclResumeTests(unittest.TestCase):
    def run_script(self, body):
        load = r'''
$ErrorActionPreference='Stop'
$source=[IO.File]::ReadAllText(REPAIR)
$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(REPAIR,[ref]$t,[ref]$e)
if($e.Count){throw 'Repair syntax failed'}
foreach($name in @('New-MetadataOnlyRule','Test-AceSequence','Test-ExactAcl')) {
 $node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
 . ([ScriptBlock]::Create($node.Extent.Text))
}
$sid='S-1-5-80-3239607251-981885122-1049059141-1040181771-757005368'
'''.replace('REPAIR', "'" + str(REPAIR).replace("'", "''") + "'")
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', load + body],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_ai_normalization_keeps_all_other_security_checks_strict(self):
        self.run_script(r'''
$before='O:SYG:SYD:(A;OICIID;FA;;;SY)'
$ai='O:SYG:SYD:AI(A;OICIID;FA;;;SY)'
if(-not (Test-ExactAcl $before $ai)){throw 'Windows AI addition rejected'}
if(-not (Test-ExactAcl $ai $ai)){throw 'Exact match rejected'}
if(Test-ExactAcl $ai $before){throw 'AI removal accepted'}
foreach($bad in @(
 'O:BAG:SYD:AI(A;OICIID;FA;;;SY)',
 'O:SYG:BAD:AI(A;OICIID;FA;;;SY)',
 'O:SYG:SYD:PAI(A;OICIID;FA;;;SY)',
 'O:SYG:SYD:ARAI(A;OICIID;FA;;;SY)',
 'O:SYG:SYD:AI(A;OICIID;FR;;;SY)',
 'O:SYG:SYD:AI(A;OICI;FA;;;SY)',
 'O:SYG:SYD:AI(D;OICIID;FA;;;SY)',
 'O:SYG:SYD:AI(A;OICIID;FA;;;SY)(A;;FA;;;AU)',
 'O:SYG:SYD:AI',
 'O:SYG:SYD:AI(A;OICIID;FA;;;SY)S:(AU;SA;FA;;;SY)')) {
 if(Test-ExactAcl $before $bad){throw 'Security drift accepted'}
}
$ordered='O:SYG:SYD:(A;;FR;;;SY)(A;;FR;;;BA)'
$reordered='O:SYG:SYD:(A;;FR;;;BA)(A;;FR;;;SY)'
if(Test-ExactAcl $ordered $reordered){throw 'ACE ordering drift accepted'}
Write-Output 'PASS: only added AI metadata accepted'
''')

    def test_resume_writes_only_missing_two_and_refuses_drift_before_writes(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-acl-resume-') as directory:
            self.run_script("$testRoot='" + directory.replace("'", "''") + "'\n" + r'''
$stagingUserProfile='C:\synthetic-profile'
$stage=Join-Path $stagingUserProfile 'AppData\Local\synthetic-stage'
$paths=@($stage,(Join-Path $stagingUserProfile 'AppData\Local'),(Join-Path $stagingUserProfile 'AppData'),$stagingUserProfile)
$original=@();$script:current=@{};$script:writes=@()
for($i=0;$i -lt 4;$i++) {
 $sddl=if($i -in @(0,3)){'O:SYG:SYD:P(A;OICI;FA;;;SY)'}else{'O:SYG:SYD:(A;OICIID;FA;;;SY)'}
 $acl=[Security.AccessControl.DirectorySecurity]::new()
 $acl.SetSecurityDescriptorSddlForm($sddl);$acl.AddAccessRule((New-MetadataOnlyRule))
 $original+=@{path=$paths[$i];before_sddl=$sddl;after_sddl=$acl.Sddl}
 $script:current[$paths[$i]]=if($i -lt 2){$acl.Sddl}else{$sddl}
}
# Reproduce the real failure: only the second completed directory has added AI.
$script:current[$paths[1]]=$script:current[$paths[1]].Replace('D:','D:AI')
$completed=@($script:current[$paths[0]],$script:current[$paths[1]])
$OriginalBackup=Join-Path $testRoot 'original.json'
[IO.File]::WriteAllText($OriginalBackup,($original | ConvertTo-Json))
$originalDigest=(Get-FileHash -LiteralPath $OriginalBackup).Hash
function Get-Item {param($LiteralPath,[switch]$Force) [pscustomobject]@{FullName=$LiteralPath;PSIsContainer=$true;Attributes=[IO.FileAttributes]::Directory}}
function Get-Acl {
 param($LiteralPath)
 $acl=[Security.AccessControl.DirectorySecurity]::new()
 $acl.SetSecurityDescriptorSddlForm($script:current[$LiteralPath]);return $acl
}
function Set-Acl {
 param($LiteralPath,$AclObject)
 $script:writes+=@($LiteralPath)
 $sddl=$AclObject.Sddl
 if(-not $AclObject.AreAccessRulesProtected){$sddl=$sddl.Replace('D:','D:AI')}
 $script:current[$LiteralPath]=$sddl
}
$Resume=$true;$Apply=$true
# Skip administrator/path discovery only. Run the real plan/apply logic against
# synthetic descriptors and fake Get/Set-Acl; backup files alone use real IO.
$body=$source.Substring($source.IndexOf('$paths=@('))
$body=$body.Replace('(Split-Path -Parent $PSScriptRoot)','$testRoot')
. ([ScriptBlock]::Create($body))
if($script:writes.Count -ne 2 -or $script:writes[0] -ne $paths[2] -or $script:writes[1] -ne $paths[3]){throw 'Wrong write targets'}
if($script:current[$paths[0]] -ne $completed[0] -or $script:current[$paths[1]] -ne $completed[1]){throw 'Completed ACL rewritten'}
if((Get-FileHash -LiteralPath $OriginalBackup).Hash -ne $originalDigest){throw 'Original backup changed'}
for($i=0;$i -lt 4;$i++){if(-not (Test-ExactAcl $original[$i].after_sddl $script:current[$paths[$i]])){throw 'Final ACL mismatch'}}
# A further resume is a no-op; existing completed grants are never rewritten.
$script:writes=@();. ([ScriptBlock]::Create($body))
if($script:writes.Count){throw 'Completed resume wrote permissions'}
# Changed owner/extra rights must stop preflight before any write.
$script:current[$paths[3]]=$script:current[$paths[3]].Replace('O:SY','O:BA')
$rejected=$false
try{. ([ScriptBlock]::Create($body))}catch{$rejected=$true}
if(-not $rejected -or $script:writes.Count){throw 'Drift did not fail closed'}
Write-Output 'PASS: missing-two-only resume; original evidence preserved; drift refused'
''')


if __name__ == '__main__':
    unittest.main()
