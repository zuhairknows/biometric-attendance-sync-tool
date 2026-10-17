# Step 4 only. Administrator-operated; all SCM operations name the staging service literally.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$StageRoot, [switch]$ResumeCreation)
$ErrorActionPreference='Stop'

function Get-StagingCreationErrorMessage {
    param([Exception]$Exception)
    # PowerShell wraps exceptions from .NET static methods. Preserve the real native
    # code; GetLastWin32Error here would be stale after the managed call returned.
    $current=$Exception
    for ($depth=0; $null -ne $current -and $depth -lt 16; $depth++) {
        if ($current -is [ComponentModel.Win32Exception]) {
            $code=$current.NativeErrorCode
            $message=[ComponentModel.Win32Exception]::new($code).Message
            return "FPFPhase1Staging creation failed: Win32 code=$code; $message. No retry was attempted. Preserve staging artifacts."
        }
        $current=$current.InnerException
    }
    return ('FPFPhase1Staging creation failed: native Win32 code unavailable; HRESULT=0x{0:X8}. No retry was attempted. Preserve staging artifacts.' -f $Exception.HResult)
}

function Wait-StagingMockCompletion {
    param([object]$Process,[int]$TimeoutMilliseconds=10000)
    if (-not $Process.WaitForExit($TimeoutMilliseconds)) { throw 'Local mock exit timed out. Preserve diagnostics.' }
    # After a bounded, confirmed exit, drain redirected output before inspecting it.
    $Process.WaitForExit()
    $exitCode=$Process.ExitCode
    if ($null -eq $exitCode) { throw 'Local mock exited but its native exit code is unavailable. Do not assume success.' }
    if ($exitCode -ne 0) { throw "Local mock exited with native code $exitCode. Preserve stdout/stderr and result." }
    return $exitCode
}

function Test-Step4ResumeArtifacts {
    param([string]$ReviewedSource)
    # This path is ONLY for the earlier failure before service creation. Validate
    # every retained file before compiling a helper or making any SCM mutation.
    $codeNames=@('probe-source.cs','local-mock.py','FPFPhase1Staging.exe','localhost.cer',
        'service-sid.txt','synthetic-readonly.txt','probe-provenance.json','mock-endpoint.json')
    $dataNames=@('mock-stdout.txt','mock-stderr.txt','mock-result.json')
    $snapshot=@()
    foreach ($directory in @($code,$data)) {
        $allowed=if ($directory -eq $code) { $codeNames } else { $dataNames }
        $files=@(Get-ChildItem -LiteralPath $directory -Force)
        if ($directory -eq $code -and $files.Count -ne $codeNames.Count) { throw 'Incomplete or unexpected retained code artifacts.' }
        foreach ($file in $files) {
            if ($file.PSIsContainer -or $file.Name -notin $allowed -or
                ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Unexpected retained Step 4 artifact. Preserve it; resume refused.' }
            $acl=Get-Acl -LiteralPath $file.FullName
            $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
            if ($acl.AreAccessRulesProtected -or $rules.Count -ne 3 -or
                $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544',$identity.User.Value)) { throw 'Unexpected retained file permissions.' }
            foreach ($rule in $rules) {
                $rights=if ($rule.IdentityReference.Value -eq $serviceSid) {
                    if ($directory -eq $code) { [Security.AccessControl.FileSystemRights]::ReadAndExecute } else { [Security.AccessControl.FileSystemRights]::Modify }
                } else { [Security.AccessControl.FileSystemRights]::FullControl }
                $rights=$rights -bor [Security.AccessControl.FileSystemRights]::Synchronize
                if (-not $rule.IsInherited -or $rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne $rights -or
                    $rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544',$serviceSid)) { throw "Unexpected retained file grant: $($file.Name)." }
            }
            $snapshot+=@([pscustomobject]@{Path=$file.FullName;Hash=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash})
        }
    }
    $record=Get-Content -LiteralPath (Join-Path $code 'probe-provenance.json') -Raw | ConvertFrom-Json
    if ($record.revision -ne $revision -or $record.step2_report_sha256 -ne $step2Hash -or
        $record.service_sid -ne $serviceSid -or $record.runner_sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Retained Step 4 provenance mismatch.' }
    foreach ($pair in @(@('probe-source.cs','source_sha256'),@('FPFPhase1Staging.exe','executable_sha256'),@('local-mock.py','mock_sha256'))) {
        if ($record.($pair[1]) -notmatch '^[0-9a-fA-F]{64}$' -or
            (Get-FileHash -LiteralPath (Join-Path $code $pair[0]) -Algorithm SHA256).Hash -ne $record.($pair[1])) { throw 'Retained Step 4 hash mismatch.' }
    }
    # The frozen executable remains unchanged. Its reviewed source may predate only
    # the separately added registration helper, which is not part of service execution.
    $currentSource=$ReviewedSource.Replace("`r`n","`n").Trim()
    $legacySource=[regex]::Replace($currentSource,'(?ms)^public static class StagingRegistration\n\{.*?^\}\n\n','')
    $retainedSource=(Get-Content -LiteralPath (Join-Path $code 'probe-source.cs') -Raw).Replace("`r`n","`n").Trim()
    if ($retainedSource -cne $currentSource -and $retainedSource -cne $legacySource) { throw 'Retained service source is not the reviewed probe.' }
    if ((Get-Content -LiteralPath (Join-Path $code 'service-sid.txt') -Raw).Trim() -ne $serviceSid -or
        (Get-Content -LiteralPath (Join-Path $code 'synthetic-readonly.txt') -Raw) -cne 'STAGE-SYNTHETIC-READONLY') { throw 'Retained SID or synthetic canary mismatch.' }
    $pem=Get-Content -LiteralPath $certificate -Raw
    $der=[Convert]::FromBase64String(($pem.Replace('-----BEGIN CERTIFICATE-----','').Replace('-----END CERTIFICATE-----','') -replace '\s',''))
    if ([Convert]::ToBase64String($der) -cne [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $code 'localhost.cer')))) { throw 'Retained public certificate differs from Step 2.' }
    $endpoint=Get-Content -LiteralPath (Join-Path $code 'mock-endpoint.json') -Raw | ConvertFrom-Json
    if (@($endpoint.PSObject.Properties).Count -ne 1 -or
        ($endpoint.port -isnot [int] -and $endpoint.port -isnot [long]) -or
        $endpoint.port -lt 1 -or $endpoint.port -gt 65535) { throw 'Invalid retained loopback endpoint.' }
    if (@(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -eq $endpoint.port }).Count -ne 0) { throw 'Retained mock port is occupied; no fallback or endpoint overwrite allowed.' }
    if (@(Get-CimInstance Win32_Process -Filter "Name='FPFPhase1Staging.exe'").Count -ne 0) { throw 'Unexpected staging probe process exists.' }
    return [pscustomobject]@{Record=$record;Snapshot=$snapshot;Port=$endpoint.port}
}

$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run PowerShell as administrator using the same Step 2/3 Windows user.'
}
$stage=(Get-Item -LiteralPath $StageRoot -Force).FullName
if ((Split-Path -Parent $stage) -ne [Environment]::GetFolderPath('LocalApplicationData') -or
    (Split-Path -Leaf $stage) -notmatch '^FPF-Phase1-Staging-[0-9a-f]{32}$') { throw 'Existing same-user staging directory required.' }
$cursor=Get-Item -LiteralPath $stage -Force
while ($null -ne $cursor) {
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
    $cursor=$cursor.Parent
}
$drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($stage))
if ($drive.DriveType -ne 'Fixed' -or $drive.DriveFormat -ne 'NTFS') { throw 'Local NTFS required.' }
$repo=Split-Path -Parent $PSScriptRoot
$revision=& git -C $repo rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $revision -ne '4256cd6201b17dcda3f9d078552acf1d3c58b3f0') { throw 'Pinned 4256cd6 required.' }
if (@(Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'").Count -ne 0) { throw 'Staging service already exists. Preserve state; do not reinstall or rerun.' }
$serviceName='FPFPhase1Staging'
$sidText=& sc.exe showsid $serviceName
if ($LASTEXITCODE -ne 0) { throw 'Cannot calculate staging SID.' }
$sidMatches=[regex]::Matches(($sidText -join "`n"),'S-1-5-80-(?:[0-9]+-){4}[0-9]+')
if ($sidMatches.Count -ne 1) { throw 'Unexpected staging SID.' }
$serviceSid=$sidMatches[0].Value
$boundary=Join-Path $stage 'service-boundary'
$code=Join-Path $boundary 'code'
$data=Join-Path $boundary 'data'
$programs=@((Join-Path $code 'FPFPhase1Staging.exe'),(Join-Path $code 'runtime\python.exe'))
$rulePrefix='FPF-Phase1-' + (Split-Path -Leaf $stage).Substring(19)
$nonLoopback=@('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::-::','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
$names=@()
foreach ($index in @(0,1)) { foreach ($suffix in @('Outbound','Inbound','UDP')) { $names+="$rulePrefix-$index-$suffix" } }

# Reuse reviewed checks without executing either bootstrap's top-level operations.
function Import-ReviewedFunctions {
    param([string]$Path,[string[]]$Names)
    $tokens=$null; $errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile($Path,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw 'Helper syntax error.' }
    foreach ($name in $Names) {
        $found=@($ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true))
        if ($found.Count -ne 1) { throw 'Expected reviewed helper function missing.' }
        Set-Item -Path "Function:script:$name" -Value ([ScriptBlock]::Create($found[0].Body.Extent.Text.Trim('{','}')))
    }
}
$boundaryHelper=Join-Path $PSScriptRoot 'prepare_phase1_staging_boundary.ps1'
Import-ReviewedFunctions $boundaryHelper @('Test-PrivateStagingDirectory','Test-StagingFirewallRule')
Test-PrivateStagingDirectory $boundary ReadAndExecute
Test-PrivateStagingDirectory $code ReadAndExecute
Test-PrivateStagingDirectory $data Modify
if (-not $ResumeCreation) {
    foreach ($directory in @($code,$data)) {
        if (@(Get-ChildItem -LiteralPath $directory -Force).Count -ne 0) { throw 'Step 4 requires empty code/data or validated -ResumeCreation. Do not overwrite evidence.' }
    }
}
$step2=Join-Path $stage 'https-self-test.json'
$step2Hash=(Get-FileHash -LiteralPath $step2 -Algorithm SHA256).Hash
$prepared=$false
foreach ($item in @(Get-ChildItem -LiteralPath $boundary -Force)) {
    if ($item.FullName -in @($code,$data)) { continue }
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Name -notmatch '^boundary-preparation(?:-[0-9a-f]{32})?\.json$') { throw 'Unexpected boundary evidence.' }
    $previous=Get-Content -LiteralPath $item.FullName -Raw | ConvertFrom-Json
    if ($previous.status -ne 'PREPARED_NOT_RUNTIME_VERIFIED' -or $previous.service_name -ne $serviceName -or
        $previous.service_sid -ne $serviceSid -or $previous.step2_report_sha256 -ne $step2Hash -or
        $previous.code_directory -ne $code -or $previous.data_directory -ne $data) { throw 'Boundary evidence mismatch.' }
    if ($previous.preparation_script_sha256 -eq (Get-FileHash -LiteralPath $boundaryHelper -Algorithm SHA256).Hash) { $prepared=$true }
}
if (-not $prepared) { throw 'A passing Step 3 report from the current reviewed helper is required.' }
function Test-StagingFirewallBoundary {
    $profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore)
    if ($profiles.Count -ne 3 -or @($profiles | Where-Object { $_.Enabled.ToString() -ne 'True' -or $_.AllowLocalFirewallRules.ToString() -eq 'False' }).Count) {
        throw 'Effective firewall policy must remain enabled and allow local rules.'
    }
    foreach ($store in @('PersistentStore','ActiveStore')) {
        $rules=@(Get-NetFirewallRule -PolicyStore $store | Where-Object { $_.Name -like "$rulePrefix-*" -or $_.Group -eq $rulePrefix })
        if ($rules.Count -ne 6) { throw 'Six existing staging firewall rules required; no policy changes are performed.' }
        foreach ($name in $names) { Test-StagingFirewallRule -Rule @($rules | Where-Object { $_.Name -eq $name }) -Name $name -Active:($store -eq 'ActiveStore') }
    }
}
Test-StagingFirewallBoundary
$certificate=Join-Path $stage 'localhost-cert.pem'
$privateKey=Join-Path $stage 'localhost-key.pem'
foreach ($path in @($step2,$certificate,$privateKey)) {
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse input refused.' }
}
if ((Get-FileHash -LiteralPath $certificate -Algorithm SHA256).Hash -ne (Get-Content -LiteralPath $step2 -Raw | ConvertFrom-Json).certificate_sha256) { throw 'Step 2 certificate hash changed.' }
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'Existing .NET Framework compiler required; do not install packages.' }
$python=& py -3 -I -S -B -c 'import sys; print(sys.executable)'
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Existing Python required.' }
$openssl=Join-Path (Split-Path -Parent (Split-Path -Parent (Get-Command git -ErrorAction Stop).Source)) 'usr\bin\openssl.exe'
Import-ReviewedFunctions (Join-Path $PSScriptRoot 'prepare_phase1_windows_staging.ps1') @('Invoke-StagingOpenSsl')
Invoke-StagingOpenSsl -OpenSslArguments @('x509','-in',$certificate,'-noout','-checkend','0')
$resumePlan=$null
if ($ResumeCreation) { $resumePlan=Test-Step4ResumeArtifacts -ReviewedSource (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'phase1_staging_service.cs') -Raw) }

# From here, create only NEW staging artifacts. Never rewrite earlier evidence or ACLs.
function Write-NewStagingText {
    param([string]$Path,[string]$Text)
    $file=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $bytes=[Text.UTF8Encoding]::new($false).GetBytes($Text); $file.Write($bytes,0,$bytes.Length) } finally { $file.Dispose() }
}
function Read-ClosedStagingJson {
    param([string]$Path)
    $deadline=[DateTime]::UtcNow.AddSeconds(5)
    do {
        try {
            # Exclusive read waits until the mock/service writer has closed the file.
            $file=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
            try { $reader=[IO.StreamReader]::new($file); $json=$reader.ReadToEnd() } finally { $file.Dispose() }
            return ($json | ConvertFrom-Json)
        } catch [IO.IOException] {
            if (($_.Exception.HResult -band 0xffff) -ne 32) { throw }
            Start-Sleep -Milliseconds 100
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Staging JSON writer did not finish.'
}
if (-not $ResumeCreation) {
foreach ($pair in @(@('phase1_staging_service.cs','probe-source.cs'),@('phase1_service_local_mock.py','local-mock.py'))) {
    $source=Join-Path $PSScriptRoot $pair[0]; $destination=Join-Path $code $pair[1]
    [IO.File]::Copy($source,$destination,$false)
    if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $destination).Hash) { throw 'Staging source transfer mismatch.' }
}
$exe=$programs[0]
& $compiler /nologo /target:exe "/out:$exe" /reference:System.ServiceProcess.dll /reference:System.Web.Extensions.dll (Join-Path $code 'probe-source.cs')
if ($LASTEXITCODE -ne 0) { throw 'Staging probe compilation failed.' }
Invoke-StagingOpenSsl -OpenSslArguments @('x509','-in',$certificate,'-outform','DER','-out',(Join-Path $code 'localhost.cer'))
Write-NewStagingText (Join-Path $code 'service-sid.txt') $serviceSid
Write-NewStagingText (Join-Path $code 'synthetic-readonly.txt') 'STAGE-SYNTHETIC-READONLY'
$provenance=[ordered]@{ revision=$revision; runner_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash;
    source_sha256=(Get-FileHash -LiteralPath (Join-Path $code 'probe-source.cs')).Hash;
    executable_sha256=(Get-FileHash -LiteralPath $exe).Hash; mock_sha256=(Get-FileHash -LiteralPath (Join-Path $code 'local-mock.py')).Hash;
    step2_report_sha256=$step2Hash; service_sid=$serviceSid }
Write-NewStagingText (Join-Path $code 'probe-provenance.json') ($provenance | ConvertTo-Json)
$registrationAssembly=$exe
$mockScript=Join-Path $code 'local-mock.py'
$mockData=$data
$readyPath=Join-Path $code 'mock-endpoint.json'
$mockExtra=@()
} else {
    # Frozen probe/endpoint/provenance are reused, never rebuilt or overwritten.
    # New registration code runs in the operator process, not in the service.
    $attemptId=[guid]::NewGuid().ToString('N')
    $mockData=Join-Path $data ('step4-resume-'+$attemptId)
    New-Item -ItemType Directory -Path $mockData -ErrorAction Stop | Out-Null
    $registrationSource=Join-Path $code ('registration-'+$attemptId+'.cs')
    $registrationAssembly=Join-Path $code ('registration-'+$attemptId+'.dll')
    $mockScript=Join-Path $code ('resume-mock-'+$attemptId+'.py')
    [IO.File]::Copy((Join-Path $PSScriptRoot 'phase1_staging_service.cs'),$registrationSource,$false)
    [IO.File]::Copy((Join-Path $PSScriptRoot 'phase1_service_local_mock.py'),$mockScript,$false)
    if ((Get-FileHash -LiteralPath $registrationSource).Hash -ne (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'phase1_staging_service.cs')).Hash -or
        (Get-FileHash -LiteralPath $mockScript).Hash -ne (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'phase1_service_local_mock.py')).Hash -or
        (Test-Path -LiteralPath $registrationAssembly)) { throw 'Resume helper transfer mismatch or output already exists.' }
    & $compiler /nologo /target:library "/out:$registrationAssembly" /reference:System.ServiceProcess.dll /reference:System.Web.Extensions.dll $registrationSource
    if ($LASTEXITCODE -ne 0) { throw 'Corrected registration helper compilation failed. Preserve all artifacts.' }
    $exe=$programs[0]
    $provenance=$resumePlan.Record
    $readyPath=Join-Path $mockData 'mock-ready.json'
    $mockExtra=@('--resume-attempt',$attemptId)
    $resumeRecord=[ordered]@{ frozen_provenance_sha256=(Get-FileHash -LiteralPath (Join-Path $code 'probe-provenance.json')).Hash;
        runner_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash; retained_port=$resumePlan.Port;
        registration_source_sha256=(Get-FileHash -LiteralPath $registrationSource).Hash;
        registration_assembly_sha256=(Get-FileHash -LiteralPath $registrationAssembly).Hash;
        resume_mock_sha256=(Get-FileHash -LiteralPath $mockScript).Hash; original_files=$resumePlan.Snapshot }
    Write-NewStagingText (Join-Path $mockData 'resume-provenance.json') ($resumeRecord | ConvertTo-Json -Depth 5)
}

$mock=$null; $started=$false
try {
    # Only this administrator-owned localhost mock runs outside the service. It cannot
    # initiate outbound connections. The service launches no children and needs no key.
    $mockArguments=@('-I','-S','-B',('"'+$mockScript+'"'),'--stage-root',('"'+$stage+'"')) + $mockExtra
    $mock=Start-Process -FilePath $python -ArgumentList $mockArguments `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $mockData 'mock-stdout.txt') -RedirectStandardError (Join-Path $mockData 'mock-stderr.txt')
    # PowerShell 5.1 may otherwise lose ExitCode for a redirected Start-Process.
    # Open/cache the process handle while the mock is alive; never treat null as 0.
    $mockHandle=$mock.Handle
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $readyPath) -and -not $mock.HasExited -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (-not (Test-Path -LiteralPath $readyPath) -or $mock.HasExited) { throw 'Local mock startup failed. Preserve staging diagnostics.' }
    $ready=Read-ClosedStagingJson $readyPath
    if ($ResumeCreation -and $ready.port -ne $resumePlan.Port) { throw 'Resume mock port mismatch; no service creation allowed.' }
    # WMI maps native errors to provider return values and loses GetLastError detail.
    # Use the already compiled staging assembly's narrowly scoped native helper.
    [Reflection.Assembly]::LoadFrom($registrationAssembly) | Out-Null
    try { [StagingRegistration]::Create($exe) }
    catch { throw (Get-StagingCreationErrorMessage $_.Exception) }
    $service=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
    if ($service.StartName -ne 'NT SERVICE\FPFPhase1Staging' -or $service.PathName -ne ('"'+$exe+'"') -or $service.StartMode -ne 'Manual') { throw 'Staging registration mismatch; service not started.' }
    $started=$true
    Start-Service -Name 'FPFPhase1Staging'
    $resultPath=Join-Path $data 'step4-result.json'
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    while (-not (Test-Path -LiteralPath $resultPath) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
    if (-not (Test-Path -LiteralPath $resultPath)) { throw 'Service-token result missing; Step 4 FAIL.' }
    # Wait for the writer and mock to finish; no inference from a partially written file.
    $mockExitCode=Wait-StagingMockCompletion -Process $mock
    Write-NewStagingText (Join-Path $mockData 'mock-completion.json') (@{wait_completed=$true;native_exit_code=$mockExitCode} | ConvertTo-Json)
    $mockResult=Get-Content -LiteralPath (Join-Path $mockData 'mock-result.json') -Raw | ConvertFrom-Json
    if ($mockResult.status -ne 'PASS' -or $mockResult.synthetic_exchanges -ne 1) { throw 'Synthetic mock evidence mismatch.' }
    $result=Read-ClosedStagingJson $resultPath
    $checks=@($result.checks.PSObject.Properties)
    if ($result.schema -ne 'phase1-service-token-probe-v1' -or $result.status -ne 'PASS' -or $checks.Count -ne 14 -or
        @($checks | Where-Object { $_.Value -isnot [bool] -or $_.Value -ne $true }).Count -ne 0 -or
        $result.off_host_packets_attempted -ne $false) { throw 'Service-token checks failed. Preserve private result; no next step authorized.' }
    Write-Output 'STEP 4 CHECKS PASS: 14 service-token checks; localhost HTTPS only. Staging service will now stop.'
} finally {
    try {
        if ($started) {
            Stop-Service -Name 'FPFPhase1Staging'
            (Get-Service -Name 'FPFPhase1Staging').WaitForStatus('Stopped',[TimeSpan]::FromSeconds(15))
        }
    } finally {
        if ($null -ne $mock -and -not $mock.HasExited) { $mock.Kill(); $mock.WaitForExit() }
    }
}
$service=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
if ($service.State -ne 'Stopped' -or $service.ExitCode -ne 0 -or (Get-FileHash -LiteralPath $exe).Hash -ne $provenance.executable_sha256) { throw 'Staging stop/hash verification failed.' }
Test-StagingFirewallBoundary
if ($ResumeCreation) {
    foreach ($original in $resumePlan.Snapshot) {
        if ((Get-FileHash -LiteralPath $original.Path -Algorithm SHA256).Hash -ne $original.Hash) { throw 'Original staging evidence changed; verification FAIL.' }
    }
}
Write-Output 'STEP 4 PASS: dedicated staging service Stopped, Manual startup; artifacts retained. Stop here.'
