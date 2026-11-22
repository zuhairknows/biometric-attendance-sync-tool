# READ ONLY: verify retained Step 4 evidence. No processes/services are started or stopped.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$StageRoot)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Read-only finalization requires administrator access to existing protected evidence.'
}
$stage=(Get-Item -LiteralPath $StageRoot -Force).FullName
$stageName=Split-Path -Leaf $stage
if ((Split-Path -Parent $stage) -ne [Environment]::GetFolderPath('LocalApplicationData') -or
    $stageName -notmatch '^FPF-Phase1-Staging-[0-9a-f]{32}$') { throw 'Existing same-user staging directory required.' }
$cursor=Get-Item -LiteralPath $stage -Force
while ($null -ne $cursor) {
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
    $cursor=$cursor.Parent
}
$boundary=Join-Path $stage 'service-boundary'
$code=Join-Path $boundary 'code'; $data=Join-Path $boundary 'data'
$exe=Join-Path $code 'FPFPhase1Staging.exe'
$serviceName='FPFPhase1Staging'
$sidText=& sc.exe showsid $serviceName
if ($LASTEXITCODE -ne 0) { throw 'Cannot calculate staging SID.' }
$sidMatches=[regex]::Matches(($sidText -join "`n"),'S-1-5-80-(?:[0-9]+-){4}[0-9]+')
if ($sidMatches.Count -ne 1) { throw 'Unexpected staging SID.' }
$serviceSid=$sidMatches[0].Value

# Load only named read-only functions. Never execute the service runner's top level.
$runner=Join-Path $PSScriptRoot 'verify_phase1_staging_service.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Runner syntax error.' }
$loader=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Import-ReviewedFunctions'},$true)
if ($null -eq $loader) { throw 'Reviewed function loader missing.' }
. ([ScriptBlock]::Create($loader.Extent.Text))
Import-ReviewedFunctions $runner @('Read-ClosedStagingJson','Test-StagingFirewallBoundary')
Import-ReviewedFunctions (Join-Path $PSScriptRoot 'prepare_phase1_staging_boundary.ps1') @('Test-PrivateStagingDirectory','Test-StagingFirewallRule')
Test-PrivateStagingDirectory $boundary ReadAndExecute
Test-PrivateStagingDirectory $code ReadAndExecute
Test-PrivateStagingDirectory $data Modify

function Assert-EvidenceFile {
    param([string]$Path,[string]$Hash)
    $item=Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Unexpected evidence file type.' }
    # All files remain private; service-owned data files are expected after the probe.
    $acl=Get-Acl -LiteralPath $Path
    $isData=($data -eq $item.DirectoryName -or $data -eq $item.Directory.Parent.FullName)
    $allowedOwners=@('S-1-5-18','S-1-5-32-544',$identity.User.Value)
    if ($isData) { $allowedOwners+=@($serviceSid) }
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $allowedOwners) { throw 'Unexpected evidence owner.' }
    $fileRules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if ($fileRules.Count -ne 3 -or $acl.AreAccessRulesProtected) { throw 'Unexpected evidence ACL.' }
    foreach ($rule in $fileRules) {
        $expectedRights=if ($rule.IdentityReference.Value -ne $serviceSid) { [Security.AccessControl.FileSystemRights]::FullControl }
            elseif ($isData) { [Security.AccessControl.FileSystemRights]::Modify } else { [Security.AccessControl.FileSystemRights]::ReadAndExecute }
        $expectedRights=$expectedRights -bor [Security.AccessControl.FileSystemRights]::Synchronize
        if ($rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544',$serviceSid) -or
            -not $rule.IsInherited -or $rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne $expectedRights) { throw 'Unexpected evidence permission.' }
    }
    if ($Hash) {
        if ($Hash -notmatch '^[0-9a-fA-F]{64}$' -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Hash) { throw 'Retained evidence hash mismatch.' }
    }
}

$service=@(Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'")
if ($service.Count -ne 1 -or $service[0].State -ne 'Stopped' -or $service[0].StartMode -ne 'Manual' -or
    $service[0].StartName -ne 'NT SERVICE\FPFPhase1Staging' -or $service[0].PathName -ne ('"'+$exe+'"') -or
    $service[0].ExitCode -ne 0 -or $service[0].ServiceSpecificExitCode -ne 0 -or $service[0].ProcessId -ne 0) { throw 'Staging service final state mismatch.' }
$live=@(Get-CimInstance Win32_Process -Filter "Name='FPFPhase1Staging.exe' OR (Name='python.exe' AND CommandLine LIKE '%$stageName%')")
if ($live.Count -ne 0) { throw 'Staging probe/mock still running.' }
$attempts=@(Get-ChildItem -LiteralPath $data -Directory -Filter 'step4-resume-*')
if ($attempts.Count -ne 1 -or $attempts[0].Name -notmatch '^step4-resume-([0-9a-f]{32})$') { throw 'Exactly one unambiguous retained resume attempt required.' }
$attempt=$attempts[0].FullName; $attemptId=$attempts[0].Name.Substring(13)
if (($attempts[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse attempt directory refused.' }
$resumeFile=Join-Path $attempt 'resume-provenance.json'; Assert-EvidenceFile $resumeFile
$resume=Read-ClosedStagingJson $resumeFile
$originalCode=@('probe-source.cs','local-mock.py','FPFPhase1Staging.exe','localhost.cer','service-sid.txt','synthetic-readonly.txt','probe-provenance.json','mock-endpoint.json')
$requiredPaths=@($originalCode | ForEach-Object { Join-Path $code $_ }) + @((Join-Path $data 'mock-stdout.txt'),(Join-Path $data 'mock-stderr.txt'))
$allowedPaths=$requiredPaths + @((Join-Path $data 'mock-result.json'))
$seen=@()
foreach ($original in @($resume.original_files)) {
    if ($original.Path -notin $allowedPaths -or $original.Path -in $seen) { throw 'Unexpected or duplicate original evidence path.' }
    Assert-EvidenceFile $original.Path $original.Hash
    $seen+=@($original.Path)
}
foreach ($path in $requiredPaths) { if ($path -notin $seen) { throw 'Incomplete original evidence snapshot.' } }
$provenanceFile=Join-Path $code 'probe-provenance.json'
Assert-EvidenceFile $provenanceFile $resume.frozen_provenance_sha256
$record=Read-ClosedStagingJson $provenanceFile
if ($record.service_sid -ne $serviceSid -or $record.revision -ne '4256cd6201b17dcda3f9d078552acf1d3c58b3f0') { throw 'Frozen probe identity mismatch.' }
Assert-EvidenceFile $exe $record.executable_sha256
Assert-EvidenceFile (Join-Path $code 'probe-source.cs') $record.source_sha256
Assert-EvidenceFile (Join-Path $code 'local-mock.py') $record.mock_sha256
if ((Get-FileHash -LiteralPath (Join-Path $stage 'https-self-test.json')).Hash -ne $record.step2_report_sha256) { throw 'Step 2 evidence changed.' }
Assert-EvidenceFile (Join-Path $code ('registration-'+$attemptId+'.cs')) $resume.registration_source_sha256
Assert-EvidenceFile (Join-Path $code ('registration-'+$attemptId+'.dll')) $resume.registration_assembly_sha256
Assert-EvidenceFile (Join-Path $code ('resume-mock-'+$attemptId+'.py')) $resume.resume_mock_sha256

$resultFile=Join-Path $data 'step4-result.json'; Assert-EvidenceFile $resultFile
$result=Read-ClosedStagingJson $resultFile
$expectedChecks=@('dedicated_service_token','not_system_or_admin','code_read_allowed','code_write_denied','code_delete_denied',
    'code_acl_change_denied','code_create_denied','data_create_read_write_allowed','data_modify_access_allowed',
    'external_tcp_denied_before_socket','dns_names_denied_before_resolution','other_loopback_ports_denied',
    'udp_denied_before_socket','localhost_https_synthetic_exchange')
$checks=@($result.checks.PSObject.Properties)
if ($result.schema -ne 'phase1-service-token-probe-v1' -or $result.status -ne 'PASS' -or
    $result.off_host_packets_attempted -ne $false -or $checks.Count -ne 14 -or
    @(Compare-Object @($checks.Name) $expectedChecks).Count -ne 0 -or
    @($checks | Where-Object { $_.Value -isnot [bool] -or $_.Value -ne $true }).Count -ne 0) { throw 'Service-token evidence incomplete or failed.' }
foreach ($name in @('mock-ready.json','mock-result.json','mock-stdout.txt','mock-stderr.txt')) { Assert-EvidenceFile (Join-Path $attempt $name) }
$mockResult=Read-ClosedStagingJson (Join-Path $attempt 'mock-result.json')
$ready=Read-ClosedStagingJson (Join-Path $attempt 'mock-ready.json')
$endpoint=Read-ClosedStagingJson (Join-Path $code 'mock-endpoint.json')
if ($mockResult.status -ne 'PASS' -or $mockResult.synthetic_exchanges -ne 1 -or
    $ready.port -ne $resume.retained_port -or $endpoint.port -ne $resume.retained_port) { throw 'Mock evidence mismatch.' }
foreach ($name in @('mock-stdout.txt','mock-stderr.txt')) {
    if ((Get-Item -LiteralPath (Join-Path $attempt $name)).Length -ne 0) { throw 'Unexpected mock output requires review.' }
}
$rulePrefix='FPF-Phase1-'+$stageName.Substring(19)
$programs=@($exe,(Join-Path $code 'runtime\python.exe'))
$nonLoopback=@('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::-::','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
$names=@(); foreach ($index in @(0,1)) { foreach ($suffix in @('Outbound','Inbound','UDP')) { $names+="$rulePrefix-$index-$suffix" } }
Test-StagingFirewallBoundary
Write-Output 'STEP 4 FUNCTIONAL EVIDENCE VERIFIED: 14 checks PASS; one localhost HTTPS exchange; service Stopped/Manual; original hashes and firewall policy verified.'
Write-Output 'READ ONLY: no service tests repeated. Original harness failure retained; historical mock native exit code remains unavailable. Kernel packet drops and the durable engine remain outside this verification.'
