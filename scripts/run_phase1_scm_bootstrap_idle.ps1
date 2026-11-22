# One idle-only diagnostic. Reuse the protected runtime; never repair ACLs/rules.
param([Parameter(Mandatory=$true)][string]$StageRoot,
      [Parameter(Mandatory=$true)][string]$AttemptName)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator PowerShell required.' }
$stage=(Get-Item -LiteralPath $StageRoot -Force).FullName
if ((Split-Path -Parent $stage) -ne [Environment]::GetFolderPath('LocalApplicationData') -or
    (Split-Path -Leaf $stage) -notmatch '^FPF-Phase1-Staging-[0-9a-f]{32}$' -or
    $AttemptName -notmatch '^scm-[0-9a-f]{32}$') { throw 'Existing staging paths required.' }
$attempt=Join-Path $stage $AttemptName
$code=Join-Path $attempt 'code'; $data=Join-Path $attempt 'data'
$repo=Split-Path -Parent $PSScriptRoot
$revision=& git -C $repo rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $revision -ne '4256cd6201b17dcda3f9d078552acf1d3c58b3f0') { throw 'Pinned engine revision required.' }
function Assert-NoReparse([string]$Path) {
    $item=Get-Item -LiteralPath $Path -Force
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
        $item=$item.Parent
    }
}
Assert-NoReparse $code; Assert-NoReparse $data
function Read-Json([string]$Path) { [IO.File]::ReadAllText($Path) | ConvertFrom-Json }
function Get-Staging { Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'" }
$oldImage='"'+(Join-Path $stage 'service-boundary\code\FPFPhase1Staging.exe')+'"'
$svc=Get-Staging
# SCM retains the previous attempt's failure codes after ImagePath restoration.
# They are history, not evidence of an active worker. Keep the new run's clean
# exit-code checks below, and retain these old codes in its result for review.
if ($null -eq $svc -or $svc.State -ne 'Stopped' -or $svc.StartMode -ne 'Manual' -or
    $svc.StartName -ne 'NT SERVICE\FPFPhase1Staging' -or $svc.PathName -ne $oldImage -or
    $svc.ProcessId -ne 0) { throw 'Original staging registration must be restored/stopped.' }
$sidOutput=& sc.exe showsid FPFPhase1Staging
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve staging SID.' }
$sidMatch=[regex]::Matches(($sidOutput -join "`n"),'S-1-5-80-(?:[0-9]+-){4}[0-9]+')
if ($sidMatch.Count -ne 1) { throw 'Invalid staging SID.' }
$serviceSid=$sidMatch[0].Value
$settings=Read-Json (Join-Path $code 'settings.json')
if ($settings.service_sid -ne $serviceSid) { throw 'Settings SID mismatch.' }
$previous=Read-Json (Join-Path $data 'result.json')
$runtime=$previous.runtime_path
$runtimeParent=Join-Path $stage 'service-boundary\code'
if ((Split-Path -Parent $runtime) -ne $runtimeParent -or
    (Split-Path -Leaf $runtime) -notmatch '^runtime-[0-9a-f]{32}$' -or
    $previous.fresh_firewall_group -notmatch '^FPF-SCM-[0-9a-f]{32}$') { throw 'Reviewed private runtime required.' }
Assert-NoReparse $runtime
# Load only the existing strict validators, never the old runners' bodies.
$t=$null;$e=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'verify_phase1_staging_service.ps1'),[ref]$t,[ref]$e)
$node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Import-ReviewedFunctions'},$true)
if ($e.Count -or $null -eq $node) { throw 'Validator loader unavailable.' }
. ([ScriptBlock]::Create($node.Extent.Text))
Import-ReviewedFunctions (Join-Path $PSScriptRoot 'prepare_phase1_staging_boundary.ps1') @('Test-PrivateStagingDirectory','Test-StagingFirewallRule')
Test-PrivateStagingDirectory $code ReadAndExecute
Test-PrivateStagingDirectory $data Modify
Test-PrivateStagingDirectory $runtime ReadAndExecute
$manifestPath=Join-Path $runtime 'runtime-manifest.json'
if ((Get-FileHash -LiteralPath $manifestPath).Hash -ne $previous.runtime_manifest_sha256) { throw 'Runtime manifest provenance mismatch.' }
$manifest=Read-Json $manifestPath
$expected=@($manifest.sha256.PSObject.Properties)
if (-not $expected.Count) { throw 'Empty runtime manifest.' }
$oldFiles=@{}
# Visit before descending so a reparse entry cannot redirect inspection.
foreach ($root in @($runtime,$code,$data)) {
    $pending=[Collections.Generic.Stack[string]]::new();$pending.Push($root)
    while ($pending.Count) {
        foreach ($item in @(Get-ChildItem -LiteralPath $pending.Pop() -Force)) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse artifact refused.' }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) } else { $oldFiles[$item.FullName]=(Get-FileHash -LiteralPath $item.FullName).Hash }
        }
    }
}
foreach ($entry in $expected) {
    if ([IO.Path]::IsPathRooted($entry.Name) -or $entry.Name -match '(^|[\\/])\.\.([\\/]|$)' -or $entry.Value -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid runtime manifest entry.' }
    $path=Join-Path $runtime $entry.Name
    if (-not $oldFiles.ContainsKey($path) -or $oldFiles[$path] -ne $entry.Value) { throw 'Runtime hash mismatch.' }
}
$runtimeFiles=@($oldFiles.Keys | Where-Object {$_.StartsWith($runtime+'\',[StringComparison]::OrdinalIgnoreCase)})
if ($runtimeFiles.Count -ne $expected.Count+1) { throw 'Unexpected runtime files.' }
$bundle=Read-Json (Join-Path $code 'bundle.json')
foreach ($entry in $bundle.sha256.PSObject.Properties) {
    if ($entry.Name -ne [IO.Path]::GetFileName($entry.Name) -or $entry.Value -notmatch '^[0-9a-fA-F]{64}$' -or
        (Get-FileHash -LiteralPath (Join-Path $code $entry.Name)).Hash -ne $entry.Value) { throw 'Retained bundle mismatch.' }
}
if (@(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith($runtime+'\',[StringComparison]::OrdinalIgnoreCase)}).Count) { throw 'Runtime already active.' }
function Assert-Isolation {
    if (@(Get-NetFirewallProfile -PolicyStore ActiveStore | Where-Object {$_.Enabled.ToString() -ne 'True' -or $_.AllowLocalFirewallRules.ToString() -eq 'False'}).Count) { throw 'Firewall profile invalid.' }
    $rulePrefix=$previous.fresh_firewall_group
    $programs=@((Join-Path $runtimeParent 'FPFPhase1Staging.exe'),(Join-Path $runtime 'python.exe'))
    $nonLoopback=@('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::-::','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
    foreach ($store in @('PersistentStore','ActiveStore')) {
        $rules=@(Get-NetFirewallRule -PolicyStore $store | Where-Object {$_.Name -like "$rulePrefix-*" -or $_.Group -eq $rulePrefix})
        if ($rules.Count -ne 3) { throw 'Expected existing runtime firewall rules.' }
        foreach ($suffix in @('Outbound','Inbound','UDP')) {
            $name="$rulePrefix-1-$suffix"
            Test-StagingFirewallRule -Rule @($rules | Where-Object {$_.Name -eq $name}) -Name $name -Active:($store -eq 'ActiveStore')
        }
    }
}
Assert-Isolation
$id=[guid]::NewGuid().ToString('N')
$hostPath=Join-Path $code ('phase1_staging_scm_bootstrap_'+$id+'.py')
$source=Join-Path $PSScriptRoot 'phase1_staging_scm_host.py'
# CreateNew copy, with inherited existing ACLs. No old file/ACL is overwritten.
$stream=[IO.File]::Open($hostPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
try { $bytes=[IO.File]::ReadAllBytes($source);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true) } finally { $stream.Dispose() }
$hostHash=(Get-FileHash -LiteralPath $hostPath).Hash
if ($hostHash -ne (Get-FileHash -LiteralPath $source).Hash) { throw 'Adapter transfer mismatch.' }
$log=Join-Path $data ('bootstrap-'+$id+'.jsonl')
$ready=Join-Path $data ('bootstrap-'+$id+'-idle-ready.json')
$image='"'+(Join-Path $runtime 'python.exe')+'" -I -S -B "'+$hostPath+'" --bootstrap-idle'
$failure=$null;$native=$null;$checks=@{};$change=$null
Write-Output "Diagnostic log: $log"
try {
    $change=Invoke-CimMethod -InputObject $svc -MethodName Change -Arguments @{PathName=$image}
    if ($change.ReturnValue -ne 0) { throw 'Staging ImagePath change failed.' }
    $controller=Get-Service -Name FPFPhase1Staging
    $controller.Start([string[]]@('idle'))
    $until=[DateTime]::UtcNow.AddSeconds(25)
    while (-not (Test-Path -LiteralPath $ready)) {
        if ((Get-Staging).State -eq 'Stopped' -or [DateTime]::UtcNow -ge $until) { throw 'Idle readiness unavailable.' }
        Start-Sleep -Milliseconds 100
    }
    $report=Read-Json $ready;$running=Get-Staging
    $checks['actual_service_token']=($report.sid -eq $serviceSid -and $report.pid -eq $running.ProcessId -and $running.State -eq 'Running' -and $report.phase -eq 'idle')
    $checks['empty_synthetic_ledger']=($report.integrity -eq 'ok' -and $report.states.pending -eq 0 -and $report.states.synced -eq 0)
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $controller.Stop();$controller.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30));$clock.Stop()
    $stopped=Get-Staging
    $checks['clean_idle_stop']=($clock.Elapsed.TotalSeconds -lt 10 -and $stopped.ExitCode -eq 0 -and $stopped.ServiceSpecificExitCode -eq 0 -and $stopped.ProcessId -eq 0)
} catch {
    $failure='Idle bootstrap failed; inspect sanitized log and preserve evidence.'
    $cause=$_.Exception
    while ($null -ne $cause) { if ($cause -is [ComponentModel.Win32Exception]) {$native=$cause.NativeErrorCode};$cause=$cause.InnerException }
} finally {
    $current=Get-Staging
    if ($current.PathName -eq $image -and $current.StartName -eq 'NT SERVICE\FPFPhase1Staging' -and $current.State -ne 'Stopped') {
        try { $owned=Get-Service -Name FPFPhase1Staging;if($current.State -ne 'Stop Pending'){$owned.Stop()};$owned.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30)) }
        catch { $failure='Staging service did not stop; no forced termination performed.' }
        $current=Get-Staging
    }
    if ($current.PathName -eq $image -and $current.State -eq 'Stopped') {
        $restore=Invoke-CimMethod -InputObject $current -MethodName Change -Arguments @{PathName=$oldImage}
        if ($restore.ReturnValue -ne 0) { $failure='Staging registration restoration failed.' }
    }
}
$final=Get-Staging
$checks['restored_stopped']=($final.PathName -eq $oldImage -and $final.State -eq 'Stopped' -and $final.StartMode -eq 'Manual' -and $final.StartName -eq 'NT SERVICE\FPFPhase1Staging')
$events=@()
if (Test-Path -LiteralPath $log) {
    $events=@(Get-Content -LiteralPath $log | ForEach-Object {$_ | ConvertFrom-Json})
    $owner=(Get-Acl -LiteralPath $log).GetOwner([Security.Principal.SecurityIdentifier]).Value
    $checks['log_owned_by_service']=($owner -eq $serviceSid)
    $required=@('process_entry_python_initialized','stdlib_initialization_complete','bundle_validation_complete','dispatcher_registered','service_control_handler_registered','idle_engine_cycle_complete','service_run_returned','dispatcher_returned')
    $checks['bootstrap_complete']=(@($required | Where-Object {$_ -notin $events.stage}).Count -eq 0 -and @($events | Where-Object {$_.stage -like '*exception'}).Count -eq 0)
} else { $checks['log_owned_by_service']=$false;$checks['bootstrap_complete']=$false }
Assert-Isolation
$checks['previous_evidence_unchanged']=$true
foreach ($path in $oldFiles.Keys) { if ((Get-FileHash -LiteralPath $path).Hash -ne $oldFiles[$path]) { $checks['previous_evidence_unchanged']=$false } }
$passed=($null -eq $failure -and $checks.Count -eq 7 -and @($checks.Values | Where-Object {$_ -ne $true}).Count -eq 0)
$result=Join-Path $repo ('.scm-idle-bootstrap-'+$id+'.json')
$value=@{status=$(if($passed){'PASS'}else{'FAIL'});checks=$checks;failure=$failure;win32_code=$native;
    previous_exit_code=$svc.ExitCode;previous_service_specific_exit_code=$svc.ServiceSpecificExitCode;
    cim_return_code=$(if($null -ne $change){$change.ReturnValue}else{$null});log=$log;adapter_sha256=$hostHash;
    runner_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash;runtime_manifest_sha256=$previous.runtime_manifest_sha256;
    engine_revision=$revision;activation_ready=$false;scope='idle_bootstrap_only'} | ConvertTo-Json -Depth 5
$stream=[IO.File]::Open($result,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
try {$bytes=[Text.Encoding]::UTF8.GetBytes($value);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)} finally {$stream.Dispose()}
Write-Output "Result: $result"
if (-not $passed) { throw 'IDLE BOOTSTRAP FAIL. Preserve evidence. Do not rerun.' }
Write-Output 'IDLE BOOTSTRAP PASS: 7 checks. Original staging registration restored/stopped.'
