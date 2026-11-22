# One service-token mutex observation. No engine/ledger/ACL/firewall operations.
param([Parameter(Mandatory=$true)][string]$StageRoot,
      [string]$AttemptName='scm-524378a7758046dfa7d518a62ed79259')
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator PowerShell required.' }
$stage=(Get-Item -LiteralPath $StageRoot -Force).FullName
if ((Split-Path -Parent $stage) -ne [Environment]::GetFolderPath('LocalApplicationData') -or
    (Split-Path -Leaf $stage) -notmatch '^FPF-Phase1-Staging-[0-9a-f]{32}$' -or $AttemptName -notmatch '^scm-[0-9a-f]{32}$') { throw 'Existing staging paths required.' }
$repo=Split-Path -Parent $PSScriptRoot
$attempt=Join-Path $stage $AttemptName
$code=Join-Path $attempt 'code';$data=Join-Path $attempt 'data'
function Assert-NoReparse([string]$Path) {
    $item=Get-Item -LiteralPath $Path -Force
    while($null -ne $item) {
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse path refused.'}
        $item=$item.Parent
    }
}
Assert-NoReparse $code;Assert-NoReparse $data
function Get-Staging { Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'" }
$original=Get-Staging
$oldImage='"'+(Join-Path $stage 'service-boundary\code\FPFPhase1Staging.exe')+'"'
if ($null -eq $original -or $original.State -ne 'Stopped' -or $original.ProcessId -ne 0 -or
    $original.PathName -ne $oldImage -or $original.StartMode -ne 'Manual' -or $original.StartName -ne 'NT SERVICE\FPFPhase1Staging') { throw 'Original staging registration must be restored/stopped.' }
$retained=[IO.File]::ReadAllText((Join-Path $data 'result.json')) | ConvertFrom-Json
$runtime=$retained.runtime_path
if ((Split-Path -Parent $runtime) -ne (Join-Path $stage 'service-boundary\code') -or
    (Split-Path -Leaf $runtime) -notmatch '^runtime-[0-9a-f]{32}$') { throw 'Existing private runtime required.' }
Assert-NoReparse $runtime
$manifestPath=Join-Path $runtime 'runtime-manifest.json'
if ((Get-FileHash -LiteralPath $manifestPath).Hash -ne $retained.runtime_manifest_sha256) { throw 'Runtime provenance mismatch.' }
$manifest=[IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
foreach($entry in $manifest.sha256.PSObject.Properties) {
    if ([IO.Path]::IsPathRooted($entry.Name) -or $entry.Name -match '(^|[\\/])\.\.([\\/]|$)') { throw 'Invalid runtime inventory path.' }
    $path=Join-Path $runtime $entry.Name
    Assert-NoReparse $path
    if((Get-FileHash -LiteralPath $path).Hash -ne $entry.Value){throw 'Runtime hash mismatch.'}
}
$id=[guid]::NewGuid().ToString('N')
$source=Join-Path $PSScriptRoot 'phase1_service_mutex_diagnostic.py'
$hostPath=Join-Path $code ('phase1_service_mutex_'+$id+'.py')
$report=Join-Path $data ('service-mutex-'+$id+'.json')
$stream=[IO.File]::Open($hostPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
try{$bytes=[IO.File]::ReadAllBytes($source);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
$sourceHash=(Get-FileHash -LiteralPath $source).Hash
if((Get-FileHash -LiteralPath $hostPath).Hash -ne $sourceHash){throw 'Diagnostic transfer mismatch.'}
$image='"'+(Join-Path $runtime 'python.exe')+'" -I -S -B "'+$hostPath+'"'
$failure=$null;$change=$null;$native=$null
Write-Output "Service-token report: $report"
try {
    $change=Invoke-CimMethod -InputObject $original -MethodName Change -Arguments @{PathName=$image}
    if($change.ReturnValue -ne 0){throw 'Staging ImagePath change failed.'}
    $controller=Get-Service -Name FPFPhase1Staging
    $controller.Start()
    $controller.Refresh()
    # This service runs one bounded API observation and exits itself. No phase
    # arguments, polling, recovery or ledger opening are supported by its host.
    $controller.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30))
} catch {
    $failure='Service-token diagnostic did not complete; preserve evidence.'
    $cause=$_.Exception
    while($null -ne $cause){if($cause -is [ComponentModel.Win32Exception]){$native=$cause.NativeErrorCode};$cause=$cause.InnerException}
} finally {
    $current=Get-Staging
    if($current.PathName -eq $image -and $current.StartName -eq $original.StartName -and $current.State -ne 'Stopped') {
        try{$owned=Get-Service -Name FPFPhase1Staging;if($current.State -ne 'Stop Pending'){$owned.Stop()};$owned.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30))}
        catch{$failure='Diagnostic service did not stop; no forced termination performed.'}
        $current=Get-Staging
    }
    if($current.PathName -eq $image -and $current.State -eq 'Stopped') {
        $restore=Invoke-CimMethod -InputObject $current -MethodName Change -Arguments @{PathName=$original.PathName}
        if($restore.ReturnValue -ne 0){$failure='Staging registration restoration failed.'}
    }
}
$final=Get-Staging
$registrationSame=$true
foreach($name in @('Name','PathName','StartName','StartMode','DisplayName','ServiceType','DesktopInteract','ErrorControl')) {
    if($final.$name -ne $original.$name){$registrationSame=$false}
}
$checks=@{registration_preserved=$registrationSame;clean_stopped=($final.State -eq 'Stopped' -and $final.ProcessId -eq 0 -and $final.ExitCode -eq 0 -and $final.ServiceSpecificExitCode -eq 0)}
$diagnostic=$null
if([IO.File]::Exists($report)) {
    $diagnostic=[IO.File]::ReadAllText($report) | ConvertFrom-Json
    $owner=(Get-Acl -LiteralPath $report).GetOwner([Security.Principal.SecurityIdentifier]).Value
    $expectedSid='S-1-5-80-3239607251-981885122-1049059141-1040181771-757005368'
    $checks['actual_service_token']=($owner -eq $expectedSid -and $diagnostic.sid -eq $expectedSid)
    $checks['mutex_call_succeeded']=($diagnostic.status -eq 'PASS' -and $diagnostic.handle_opened -eq $true -and $diagnostic.handle_closed -eq $true)
} else {$checks['actual_service_token']=$false;$checks['mutex_call_succeeded']=$false}
$passed=($null -eq $failure -and @($checks.Values | Where-Object {$_ -ne $true}).Count -eq 0)
$result=Join-Path $repo ('.scm-service-mutex-'+$id+'.json')
$value=@{status=$(if($passed){'PASS'}else{'FAIL'});checks=$checks;failure=$failure;native_runner_error=$native;
    diagnostic=$diagnostic;service_report=$report;source_sha256=$sourceHash;
    runtime_manifest_sha256=$retained.runtime_manifest_sha256;original_path=$original.PathName} | ConvertTo-Json -Depth 5
$stream=[IO.File]::Open($result,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
try{$bytes=[Text.Encoding]::UTF8.GetBytes($value);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
Write-Output "Result: $result"
if($null -ne $diagnostic){Write-Output ("win32_error={0}; handle_opened={1}; existed={2}" -f $diagnostic.win32_error,$diagnostic.handle_opened,$diagnostic.existed)}
if(-not $passed){throw 'SERVICE MUTEX DIAGNOSTIC FAIL. Preserve report; do not retry.'}
Write-Output 'SERVICE MUTEX DIAGNOSTIC PASS. Original staging registration preserved/stopped.'
