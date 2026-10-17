# Synthetic in-memory Windows objects only. No real ACL, firewall or service changes.
param([string]$Source)
$ErrorActionPreference = 'Stop'
# Load built-ins before installing mocks; module auto-loading must not replace them.
Import-Module Microsoft.PowerShell.Utility
Import-Module Microsoft.PowerShell.Management
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Syntax error' }
foreach ($function in $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $true)) {
    . ([ScriptBlock]::Create($function.Extent.Text))
}
$Resume=$true
$serviceName='FPFPhase1Staging'
$serviceSid='S-1-5-80-1-2-3-4-5'
$boundary='C:\synthetic\service-boundary'
$code="$boundary\code"; $data="$boundary\data"
$programs=@("$code\FPFPhase1Staging.exe", "$code\runtime\python.exe")
$rulePrefix='FPF-Phase1-synthetic'
$step2='C:\synthetic\https-self-test.json'
$reportPath="$boundary\boundary-preparation.json"
$nonLoopback=@('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::-::','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
$names=@()
foreach ($index in @(0,1)) { foreach ($suffix in @('Outbound','Inbound','UDP')) { $names+="$rulePrefix-$index-$suffix" } }
function New-Item { param($ItemType,$Path,$ErrorAction) throw 'Resume attempted to create a directory' }
function Set-Acl { param($LiteralPath,$AclObject) throw 'Resume attempted to change an ACL' }
function Get-Acl {
    param($LiteralPath)
    if ($LiteralPath -eq $reportPath) { return $script:reportAcl }
    return $script:acls[$LiteralPath]
}
function Get-Content { param($LiteralPath,[switch]$Raw) if ($LiteralPath -ne $reportPath) { throw 'Unexpected read' }; return $script:reportJson }
function Get-FileHash { param($LiteralPath,$Algorithm) if ($LiteralPath -ne $step2) { throw 'Unexpected hash' }; [pscustomobject]@{Hash=('a'*64)} }
function Get-Item {
    param($LiteralPath,[switch]$Force)
    if (-not $script:acls.ContainsKey($LiteralPath)) { throw 'Missing directory' }
    $attributes=if ($script:reparse) { [IO.FileAttributes]::ReparsePoint } else { [IO.FileAttributes]::Directory }
    [pscustomobject]@{PSIsContainer=$true; Attributes=$attributes}
}
function Get-ChildItem {
    param($LiteralPath,[switch]$Force)
    if ($script:unexpected) { return [pscustomobject]@{FullName="$LiteralPath\unexpected.txt";Name='unexpected.txt';PSIsContainer=$false;Attributes=0} }
    if ($LiteralPath -eq $boundary) {
        foreach ($path in @($code,$data)) { [pscustomobject]@{FullName=$path;Name=($path.Split('\')[-1]);PSIsContainer=$true;Attributes=16} }
        if ($script:hasReport) { [pscustomobject]@{FullName=$reportPath;Name='boundary-preparation.json';PSIsContainer=$false;Attributes=0} }
    }
}
function Get-NetFirewallRule {
    param($PolicyStore,$Name)
    $rules=@($script:rules.Values)
    if ($PolicyStore -eq 'ActiveStore' -and $script:activeMissing) { $rules=@() }
    if ($Name) { $rules | Where-Object { $_.Name -eq $Name } } else { $rules }
}
function Get-NetFirewallApplicationFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{Program=$Rule.Program} } }
function Get-NetFirewallAddressFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{RemoteAddress=$Rule.RemoteAddress;LocalAddress='Any'} } }
function Get-NetFirewallPortFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{Protocol=$Rule.Protocol;LocalPort='Any';RemotePort=$Rule.RemotePort} } }
function Get-NetFirewallServiceFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{Service='Any'} } }
function Get-NetFirewallInterfaceFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{InterfaceAlias='Any'} } }
function Get-NetFirewallInterfaceTypeFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{InterfaceType='Any'} } }
function Get-NetFirewallSecurityFilter {
    param([Parameter(ValueFromPipeline=$true)]$Rule)
    process { [pscustomobject]@{Authentication='NotRequired';Encryption='NotRequired';OverrideBlockRules=$false;LocalUser=$Rule.LocalUser;RemoteUser='Any';RemoteMachine='Any'} }
}
function New-NetFirewallRule {
    param($PolicyStore,$Name,$DisplayName,$Group,$Enabled,$Profile,$Direction,$Action,$Program,$Protocol,$RemoteAddress)
    if ($script:failAfterOne -and $script:created.Count -eq 1) { throw 'Synthetic firewall creation failure' }
    if ($script:rules.ContainsKey($Name)) { throw 'Duplicate creation attempted' }
    $script:rules[$Name]=[pscustomobject]@{
        Name=$Name;DisplayName=$DisplayName;Group=$Group;Enabled=$Enabled;Profile=$Profile;Direction=$Direction;
        Action=$Action;Program=$Program;Protocol=$Protocol;RemoteAddress=$RemoteAddress;RemotePort='Any';PrimaryStatus='OK';LocalUser='Any'
    }
    $script:created+=@($Name)
}
function Reset-State {
    $script:acls=@{}; $script:rules=@{}; $script:created=@()
    $script:reparse=$false; $script:unexpected=$false; $script:activeMissing=$false; $script:failAfterOne=$false
    $script:hasReport=$false
    $script:reportJson=@{
        status='PREPARED_NOT_RUNTIME_VERIFIED';service_name=$serviceName;service_sid=$serviceSid;
        service_installed=$false;service_started=$false;step2_report_sha256=('a'*64);
        preparation_script_sha256=('b'*64);code_directory=$code;data_directory=$data;
        protected_program_paths=$programs;firewall_rule_names=$names
    } | ConvertTo-Json
    $script:reportAcl=[Security.AccessControl.DirectorySecurity]::new()
    $script:reportAcl.SetSecurityDescriptorSddlForm("O:BAG:BAD:(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;0x1200a9;;;$serviceSid)")
    foreach ($path in @($boundary,$code,$data)) {
        $acl=[Security.AccessControl.DirectorySecurity]::new()
        $acl.SetAccessRuleProtection($true,$false)
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        foreach ($sid in @('S-1-5-18','S-1-5-32-544',$serviceSid)) {
            $rights=if ($sid -ne $serviceSid) { 'FullControl' } elseif ($path -eq $data) { 'Modify' } else { 'ReadAndExecute' }
            $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
                [Security.Principal.SecurityIdentifier]::new($sid),$rights,'ContainerInherit, ObjectInherit','None','Allow'))
        }
        $script:acls[$path]=$acl
    }
}
function Expect-Refusal {
    $before=$script:created.Count
    $refused=$false
    try { Complete-StagingBoundary } catch { $refused=$true }
    if (-not $refused -or $script:created.Count -ne $before) { throw 'Unexpected state accepted or mutation before refusal' }
}

# The reported partial state: all three valid directories, zero firewall rules.
Reset-State
Complete-StagingBoundary
if ($created.Count -ne 6 -or $rules.Count -ne 6) { throw 'Missing rules not completed' }
Complete-StagingBoundary
if ($created.Count -ne 6) { throw 'Idempotent retry created duplicates' }

# Actual Windows canonicalization: "::-::" is returned as the single address "::".
foreach ($rule in $rules.Values) {
    $rule.RemoteAddress=@($rule.RemoteAddress | ForEach-Object { if ($_ -eq '::-::') { '::' } else { $_ } })
}
Complete-StagingBoundary
if ($created.Count -ne 6) { throw 'Canonical spelling caused rule creation' }
# Canonicalization must never accept a changed address or missing coverage.
$rules[$names[0]].RemoteAddress=@($nonLoopback | Where-Object { $_ -ne '::-::' }) + @('::1')
Expect-Refusal
$rules[$names[0]].RemoteAddress=@($nonLoopback | Where-Object { $_ -ne '::-::' })
Expect-Refusal

# A real rule-creation failure leaves validated partial state; retry adds only five.
Reset-State
$script:failAfterOne=$true
$failed=$false
try { Complete-StagingBoundary } catch { $failed=$true }
if (-not $failed -or $rules.Count -ne 1) { throw 'Synthetic interruption did not occur' }
$script:failAfterOne=$false
Complete-StagingBoundary
if ($created.Count -ne 6 -or $rules.Count -ne 6) { throw 'Interrupted creation did not resume safely' }

foreach ($field in @('Program','Enabled','Protocol','RemotePort','Group','RemoteAddress','LocalUser')) {
    Reset-State; Complete-StagingBoundary
    # Remove an early expected rule: a later bad rule must block even that creation.
    $rules.Remove($names[0])
    $rules[$names[5]].$field='unexpected'
    Expect-Refusal
}
Reset-State; Complete-StagingBoundary; $script:activeMissing=$true; Expect-Refusal
Reset-State; $script:reparse=$true; Expect-Refusal
Reset-State; $script:unexpected=$true; Expect-Refusal
Reset-State; $acls.Remove($data); Expect-Refusal
Reset-State
$acls[$code].AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'))
Expect-Refusal
Reset-State
$acls[$data].SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-80-9-8-7-6-5'))
Expect-Refusal
Reset-State
$acls[$data].PurgeAccessRules([Security.Principal.SecurityIdentifier]::new($serviceSid))
$acls[$data].AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-5-80-9-8-7-6-5'),'Modify','ContainerInherit, ObjectInherit','None','Allow'))
Expect-Refusal
Reset-State; $script:hasReport=$true
$savedReport=$reportJson
Complete-StagingBoundary
if ($reportJson -ne $savedReport) { throw 'Original report changed' }
Complete-StagingBoundary
if ($created.Count -ne 6) { throw 'Report-bearing retry created duplicate rules' }
Reset-State; $script:hasReport=$true; $script:reportJson='invalid JSON'; Expect-Refusal
Reset-State; $script:hasReport=$true; $script:reportJson=$reportJson.Replace($serviceSid,'S-1-5-80-9-8-7-6-5'); Expect-Refusal
Reset-State; $script:hasReport=$true
$reportAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow'))
Expect-Refusal
Write-Output 'PASS: partial state, interruption, idempotence and refusal scenarios'
