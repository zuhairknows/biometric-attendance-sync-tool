# Synthetic files and ACL objects. The registration boundary is a recording mock.
param([string]$Runner,[string]$ReviewedSourcePath,[string]$Root)
$ErrorActionPreference='Stop'
Import-Module Microsoft.PowerShell.Utility
Import-Module Microsoft.PowerShell.Management
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Runner,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Syntax error' }
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-Step4ResumeArtifacts'},$true)
. ([ScriptBlock]::Create($function.Extent.Text))
$reviewed=Get-Content -LiteralPath $ReviewedSourcePath -Raw
$legacy=[regex]::Replace($reviewed.Replace("`r`n","`n").Trim(),'(?ms)^public static class StagingRegistration\n\{.*?^\}\n\n','')
$Root=(Get-Item -LiteralPath $Root).FullName
$code=Join-Path $Root 'code'; $data=Join-Path $Root 'data'
New-Item -ItemType Directory -Path $code,$data | Out-Null
$revision='4256cd6201b17dcda3f9d078552acf1d3c58b3f0'
$step2Hash='a'*64
$serviceSid='S-1-5-80-1-2-3-4-5'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$certificate=Join-Path $Root 'cert.pem'
# Tiny synthetic DER bytes suffice for this equality check; real certificate/TLS
# parsing and validation are covered separately. Never import or execute this EXE.
[IO.File]::WriteAllText($certificate,"-----BEGIN CERTIFICATE-----`nAQID`n-----END CERTIFICATE-----")
foreach ($pair in @(@('probe-source.cs',$legacy),@('local-mock.py','# retained synthetic mock'),
    @('FPFPhase1Staging.exe','synthetic-not-executable'),@('service-sid.txt',$serviceSid),
    @('synthetic-readonly.txt','STAGE-SYNTHETIC-READONLY'),@('mock-endpoint.json','{"port":12345}'))) {
    [IO.File]::WriteAllText((Join-Path $code $pair[0]),$pair[1])
}
[IO.File]::WriteAllBytes((Join-Path $code 'localhost.cer'),[byte[]]@(1,2,3))
$record=@{revision=$revision;step2_report_sha256=$step2Hash;service_sid=$serviceSid;runner_sha256=('b'*64)}
foreach ($pair in @(@('probe-source.cs','source_sha256'),@('FPFPhase1Staging.exe','executable_sha256'),@('local-mock.py','mock_sha256'))) {
    $record[$pair[1]]=(Get-FileHash -LiteralPath (Join-Path $code $pair[0])).Hash
}
$provenancePath=Join-Path $code 'probe-provenance.json'
[IO.File]::WriteAllText($provenancePath,($record | ConvertTo-Json))
[IO.File]::WriteAllText((Join-Path $data 'mock-stdout.txt'),'original stdout evidence')
[IO.File]::WriteAllText((Join-Path $data 'mock-stderr.txt'),'original stderr evidence')
$script:badAcl=$false; $script:occupied=$false; $script:probeExists=$false; $script:creationCalls=0
function Get-Acl {
    param($LiteralPath)
    $rights=if ((Split-Path -Parent $LiteralPath) -eq $code) {'0x1200a9'} else {'0x1301bf'}
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetSecurityDescriptorSddlForm("O:BAG:BAD:(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;$rights;;;$serviceSid)")
    if ($script:badAcl) { $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'Read','Allow')) }
    return $acl
}
function Get-NetTCPConnection { param($State) if ($script:occupied) { [pscustomobject]@{LocalPort=12345} } }
function Get-CimInstance { param($ClassName,$Filter) if ($script:probeExists) { [pscustomobject]@{Name='FPFPhase1Staging.exe'} } }
function Invoke-ValidatedMockCreation {
    $plan=Test-Step4ResumeArtifacts -ReviewedSource $reviewed
    # No real SCM API is loaded/called in this fixture.
    $script:creationCalls++
    if ($plan.Port -ne 12345 -or $plan.Snapshot.Count -ne 10) { throw 'Unexpected resume plan' }
}
function Expect-Refusal {
    $before=$script:creationCalls; $refused=$false
    try { Invoke-ValidatedMockCreation } catch { $refused=$true }
    if (-not $refused -or $before -ne $script:creationCalls) { throw 'Invalid artifacts reached mocked creation' }
}
Invoke-ValidatedMockCreation
if ($creationCalls -ne 1) { throw 'Valid legacy artifacts did not reach mocked creation' }
$original=@(Get-ChildItem -LiteralPath $code,$data -File | ForEach-Object { [pscustomobject]@{Path=$_.FullName;Hash=(Get-FileHash -LiteralPath $_.FullName).Hash} })
$script:badAcl=$true; Expect-Refusal; $script:badAcl=$false
$script:occupied=$true; Expect-Refusal; $script:occupied=$false
$script:probeExists=$true; Expect-Refusal; $script:probeExists=$false
$exe=Join-Path $code 'FPFPhase1Staging.exe'
[IO.File]::WriteAllText($exe,'tampered executable'); Expect-Refusal
[IO.File]::WriteAllText($exe,'synthetic-not-executable')
$canary=Join-Path $code 'synthetic-readonly.txt'
[IO.File]::WriteAllText($canary,'changed canary'); Expect-Refusal
[IO.File]::WriteAllText($canary,'STAGE-SYNTHETIC-READONLY')
$sidPath=Join-Path $code 'service-sid.txt'
[IO.File]::WriteAllText($sidPath,'S-1-5-80-9-8-7-6-5'); Expect-Refusal
[IO.File]::WriteAllText($sidPath,$serviceSid)
$endpoint=Join-Path $code 'mock-endpoint.json'
foreach ($bad in @('{"port":true}','{"port":0}','{"port":65536}','{"port":12345,"host":"remote.invalid"}')) {
    [IO.File]::WriteAllText($endpoint,$bad); Expect-Refusal
}
[IO.File]::WriteAllText($endpoint,'{"port":12345}')
$unexpected=Join-Path $data 'step4-result.json'
[IO.File]::WriteAllText($unexpected,'{}'); Expect-Refusal
# Delete only the test-owned synthetic rejection fixture; never staging evidence.
[IO.File]::Delete($unexpected)
foreach ($file in $original) { if ((Get-FileHash -LiteralPath $file.Path).Hash -ne $file.Hash) { throw 'Preflight changed evidence' } }
Invoke-ValidatedMockCreation
Write-Output 'PASS: frozen legacy artifacts, mocked creation, rejection cases and unchanged evidence'
