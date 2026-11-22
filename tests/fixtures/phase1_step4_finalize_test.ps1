# Full read-only finalization body against synthetic evidence and mocked OS queries.
param([string]$ScriptsRoot,[string]$Root)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'phase1_step4_resume_test.ps1') `
    -Runner (Join-Path $ScriptsRoot 'verify_phase1_staging_service.ps1') `
    -ReviewedSourcePath (Join-Path $ScriptsRoot 'phase1_staging_service.cs') -Root $Root | Out-Null
$stage=$Root; $stageName=Split-Path -Leaf $stage
$boundary=$Root
# Move is deliberately unnecessary: this fixture's code/data already model the
# boundary. Replace only the computed boundary path when evaluating the body.
$attemptId='1'*32
$attempt=Join-Path $data ('step4-resume-'+$attemptId)
New-Item -ItemType Directory -Path $attempt | Out-Null
$snapshot=@(Get-ChildItem -LiteralPath $code,$data -File | ForEach-Object { @{Path=$_.FullName;Hash=(Get-FileHash -LiteralPath $_.FullName).Hash} })
$step2=Join-Path $stage 'https-self-test.json'
[IO.File]::WriteAllText($step2,'{}')
$record.step2_report_sha256=(Get-FileHash -LiteralPath $step2).Hash
[IO.File]::WriteAllText($provenancePath,($record | ConvertTo-Json))
foreach ($entry in $snapshot) { if ($entry.Path -eq $provenancePath) { $entry.Hash=(Get-FileHash -LiteralPath $provenancePath).Hash } }
foreach ($name in @("registration-$attemptId.cs","registration-$attemptId.dll","resume-mock-$attemptId.py")) { [IO.File]::WriteAllText((Join-Path $code $name),'synthetic helper, never loaded') }
$resume=@{original_files=$snapshot;retained_port=12345;frozen_provenance_sha256=(Get-FileHash -LiteralPath $provenancePath).Hash;
    registration_source_sha256=(Get-FileHash -LiteralPath (Join-Path $code "registration-$attemptId.cs")).Hash;
    registration_assembly_sha256=(Get-FileHash -LiteralPath (Join-Path $code "registration-$attemptId.dll")).Hash;
    resume_mock_sha256=(Get-FileHash -LiteralPath (Join-Path $code "resume-mock-$attemptId.py")).Hash}
[IO.File]::WriteAllText((Join-Path $attempt 'resume-provenance.json'),($resume | ConvertTo-Json -Depth 5))
$checks=@{}
foreach ($name in @('dedicated_service_token','not_system_or_admin','code_read_allowed','code_write_denied','code_delete_denied',
    'code_acl_change_denied','code_create_denied','data_create_read_write_allowed','data_modify_access_allowed',
    'external_tcp_denied_before_socket','dns_names_denied_before_resolution','other_loopback_ports_denied',
    'udp_denied_before_socket','localhost_https_synthetic_exchange')) { $checks[$name]=$true }
$resultFile=Join-Path $data 'step4-result.json'
$result=@{schema='phase1-service-token-probe-v1';status='PASS';off_host_packets_attempted=$false;checks=$checks}
[IO.File]::WriteAllText($resultFile,($result | ConvertTo-Json))
[IO.File]::WriteAllText((Join-Path $attempt 'mock-ready.json'),'{"port":12345}')
[IO.File]::WriteAllText((Join-Path $attempt 'mock-result.json'),'{"status":"PASS","synthetic_exchanges":1}')
foreach ($name in @('mock-stdout.txt','mock-stderr.txt')) { [IO.File]::WriteAllText((Join-Path $attempt $name),'') }
function sc.exe { param($verb,$name) $global:LASTEXITCODE=0; "SERVICE SID: $serviceSid" }
function Get-Acl {
    param($LiteralPath)
    $isData=$LiteralPath -eq $data -or $LiteralPath.StartsWith($data+'\')
    $rights=if ($isData) {'0x1301bf'} else {'0x1200a9'}
    $isDirectory=(Get-Item -LiteralPath $LiteralPath).PSIsContainer
    $flags=if ($isDirectory) {'OICI'} else {'ID'}
    $protection=if ($isDirectory) {'P'} else {''}
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetSecurityDescriptorSddlForm("O:BAG:BAD:$protection(A;$flags;FA;;;SY)(A;$flags;FA;;;BA)(A;$flags;$rights;;;$serviceSid)")
    return $acl
}
function Get-CimInstance {
    param($ClassName,$Filter)
    if ($ClassName -eq 'Win32_Service') { [pscustomobject]@{State='Stopped';StartMode='Manual';StartName='NT SERVICE\FPFPhase1Staging';PathName=('"'+(Join-Path $code 'FPFPhase1Staging.exe')+'"');ExitCode=0;ServiceSpecificExitCode=0;ProcessId=0} }
}
function Get-NetFirewallProfile { param($PolicyStore) foreach ($n in @(1,2,3)) { [pscustomobject]@{Enabled='True';AllowLocalFirewallRules='True'} } }
function Get-NetFirewallRule {
    param($PolicyStore)
    foreach ($index in @(0,1)) { foreach ($suffix in @('Inbound','Outbound','UDP')) {
        $name="$rulePrefix-$index-$suffix"
        [pscustomobject]@{Name=$name;Group=$rulePrefix;DisplayName=$name;Enabled='True';Action='Block';PrimaryStatus='OK';
            Direction=$(if ($suffix -eq 'Inbound'){'Inbound'}else{'Outbound'});Profile='Any';Index=$index;Suffix=$suffix}
    } }
}
function Get-NetFirewallApplicationFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{Program=$programs[$Rule.Index]} } }
function Get-NetFirewallAddressFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{LocalAddress='Any';RemoteAddress=$(if ($Rule.Suffix -eq 'UDP'){@('Any')}else{$nonLoopback})} } }
function Get-NetFirewallPortFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{LocalPort='Any';RemotePort='Any';Protocol=$(if($Rule.Suffix -eq 'UDP'){'UDP'}else{'Any'})} } }
function Get-NetFirewallServiceFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{Service='Any'} } }
function Get-NetFirewallInterfaceFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{InterfaceAlias='Any'} } }
function Get-NetFirewallInterfaceTypeFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{InterfaceType='Any'} } }
function Get-NetFirewallSecurityFilter { param([Parameter(ValueFromPipeline=$true)]$Rule) process { [pscustomobject]@{Authentication='NotRequired';Encryption='NotRequired';OverrideBlockRules=$false;LocalUser='Any';RemoteUser='Any';RemoteMachine='Any'} } }
$source=Get-Content -LiteralPath (Join-Path $ScriptsRoot 'finalize_phase1_staging_service.ps1') -Raw
# Only elevation/location guards are excluded for the isolated temporary fixture.
$body=$source.Substring($source.IndexOf('$boundary=Join-Path'))
$body=$body.Replace('$boundary=Join-Path $stage ''service-boundary''','$boundary=$stage').Replace('$PSScriptRoot','$ScriptsRoot')
$before=@(Get-ChildItem -LiteralPath $Root -File -Recurse | ForEach-Object { @{Path=$_.FullName;Hash=(Get-FileHash -LiteralPath $_.FullName).Hash} })
$output=Invoke-Expression $body
if (-not ($output -match 'STEP 4 FUNCTIONAL EVIDENCE VERIFIED')) { throw 'Finalization did not pass' }
foreach ($item in $before) { if ((Get-FileHash -LiteralPath $item.Path).Hash -ne $item.Hash) { throw 'Finalization modified evidence' } }
$result.checks.code_write_denied=$false
[IO.File]::WriteAllText($resultFile,($result | ConvertTo-Json))
$refused=$false
try { Invoke-Expression $body | Out-Null } catch { $refused=$true }
if (-not $refused) { throw 'False service check accepted' }
Write-Output 'PASS: read-only finalization and failed-check refusal'
