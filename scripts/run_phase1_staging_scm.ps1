# One staging-only SCM session. Preparation never targets the production service.
param([Parameter(Mandatory=$true)][string]$StageRoot,[switch]$FreshRuntime,
      [string]$RemainingFromAttempt,[string]$IdleEvidence)
$ErrorActionPreference='Stop'
$remainingOnly=-not [string]::IsNullOrWhiteSpace($RemainingFromAttempt)
if (($remainingOnly -and ($FreshRuntime -or -not $IdleEvidence)) -or (-not $remainingOnly -and $IdleEvidence)) {
    throw 'RemainingFromAttempt requires IdleEvidence and cannot use FreshRuntime.'
}
function Get-SessionPhases {
    # Do not repeat the accepted idle test in the remaining session.
    if ($remainingOnly) { return @('inflight','recover') }
    return @('idle','inflight','recover')
}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator PowerShell under the original staging user is required.'
}
$stage=(Get-Item -LiteralPath $StageRoot -Force).FullName
if ((Split-Path -Parent $stage) -ne [Environment]::GetFolderPath('LocalApplicationData') -or
    (Split-Path -Leaf $stage) -notmatch '^FPF-Phase1-Staging-[0-9a-f]{32}$') { throw 'Existing staging root required.' }
$cursor=Get-Item -LiteralPath $stage -Force
while ($null -ne $cursor) {
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
    $cursor=$cursor.Parent
}
$repo=Split-Path -Parent $PSScriptRoot
$revision=& git -C $repo rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $revision -ne '4256cd6201b17dcda3f9d078552acf1d3c58b3f0') { throw 'Pinned 4256cd6 required.' }
$serviceName='FPFPhase1Staging'
$boundary=Join-Path $stage 'service-boundary'
$code=Join-Path $boundary 'code'; $data=Join-Path $boundary 'data'
$oldImage='"'+(Join-Path $code 'FPFPhase1Staging.exe')+'"'
$svc=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
if ($null -eq $svc -or $svc.State -ne 'Stopped' -or $svc.StartName -ne 'NT SERVICE\FPFPhase1Staging' -or
    $svc.StartMode -ne 'Manual' -or $svc.PathName -ne $oldImage -or $svc.ProcessId -ne 0) { throw 'Expected stopped original staging probe.' }
$sidOutput=& sc.exe showsid FPFPhase1Staging
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve staging SID.' }
$sidMatch=[regex]::Matches(($sidOutput -join "`n"),'S-1-5-80-(?:[0-9]+-){4}[0-9]+')
if ($sidMatch.Count -ne 1) { throw 'Invalid staging SID.' }
$serviceSid=$sidMatch[0].Value
# Import named validation functions only. Never execute another runner's body.
$loaderPath=Join-Path $PSScriptRoot 'verify_phase1_staging_service.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($loaderPath,[ref]$tokens,[ref]$errors)
$loader=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Import-ReviewedFunctions'},$true)
if ($errors.Count -ne 0 -or $null -eq $loader) { throw 'Reviewed function loader unavailable.' }
. ([ScriptBlock]::Create($loader.Extent.Text))
Import-ReviewedFunctions (Join-Path $PSScriptRoot 'prepare_phase1_staging_boundary.ps1') @('Test-PrivateStagingDirectory','Test-StagingFirewallRule')
Test-PrivateStagingDirectory $boundary ReadAndExecute
Test-PrivateStagingDirectory $code ReadAndExecute
Test-PrivateStagingDirectory $data Modify
$probe=Get-Content -LiteralPath (Join-Path $code 'probe-provenance.json') -Raw | ConvertFrom-Json
$step2=Get-Content -LiteralPath (Join-Path $stage 'https-self-test.json') -Raw | ConvertFrom-Json
if ($probe.revision -ne $revision -or $probe.service_sid -ne $serviceSid -or
    $probe.executable_sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
    (Get-FileHash -LiteralPath (Join-Path $code 'FPFPhase1Staging.exe')).Hash -ne $probe.executable_sha256 -or
    $step2.status -ne 'PASS' -or (Get-FileHash -LiteralPath (Join-Path $stage 'localhost-cert.pem')).Hash -ne $step2.certificate_sha256) {
    throw 'Retained probe/TLS provenance mismatch; nothing is re-created.'
}
$retainedRuntime=Join-Path $code 'runtime'
$runtime=$retainedRuntime
$retainedInspection=$null; $freshRulePrefix=$null; $freshRulesPrepared=$false
$idlePass=$null; $idleEvidenceHash=$null; $reusedManifestHash=$null
if ($remainingOnly) {
    if ($RemainingFromAttempt -notmatch '^scm-[0-9a-f]{32}$') { throw 'Invalid retained attempt name.' }
    $retainedAttempt=Join-Path $stage $RemainingFromAttempt
    $retained=Get-Content -LiteralPath (Join-Path $retainedAttempt 'data\result.json') -Raw | ConvertFrom-Json
    $idlePath=(Get-Item -LiteralPath $IdleEvidence -Force).FullName
    if ((Split-Path -Parent $idlePath) -ne $repo -or (Split-Path -Leaf $idlePath) -notmatch '^\.scm-idle-bootstrap-[0-9a-f]{32}\.json$') { throw 'Repository-local idle PASS evidence required.' }
    $idlePass=[IO.File]::ReadAllText($idlePath) | ConvertFrom-Json
    $idleChecks=@('bootstrap_complete','actual_service_token','clean_idle_stop','previous_evidence_unchanged','log_owned_by_service','restored_stopped','empty_synthetic_ledger')
    if ($idlePass.status -ne 'PASS' -or $idlePass.scope -ne 'idle_bootstrap_only' -or $idlePass.engine_revision -ne $revision -or
        @($idlePass.checks.PSObject.Properties).Count -ne 7 -or @($idlePass.checks.PSObject.Properties | Where-Object {$_.Value -ne $true}).Count -or
        @($idleChecks | Where-Object {$_ -notin $idlePass.checks.PSObject.Properties.Name}).Count -or
        $idlePass.adapter_sha256 -ne (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'phase1_staging_scm_host.py')).Hash -or
        (Split-Path -Parent $idlePass.log) -ne (Join-Path $retainedAttempt 'data') -or
        $idlePass.runtime_manifest_sha256 -ne $retained.runtime_manifest_sha256) { throw 'Accepted idle evidence does not match this runtime/attempt.' }
    $idleEvidenceHash=(Get-FileHash -LiteralPath $idlePath).Hash
    $runtime=$retained.runtime_path
    if ((Split-Path -Parent $runtime) -ne $code -or (Split-Path -Leaf $runtime) -notmatch '^runtime-[0-9a-f]{32}$' -or
        $retained.fresh_firewall_group -notmatch '^FPF-SCM-[0-9a-f]{32}$') { throw 'Validated existing staging runtime required.' }
    $freshRulePrefix=$retained.fresh_firewall_group
    $freshRulesPrepared=$true  # Validate the EXISTING rules; never create them.
    $reusedManifestHash=$retained.runtime_manifest_sha256
}
if ($FreshRuntime) {
    # Read-only validation first. Never execute or adopt the retained runtime.
    $retainedInspection=(& (Join-Path $PSScriptRoot 'inspect_phase1_reserved_runtime.ps1') -StageRoot $stage) | ConvertFrom-Json
    if ($retainedInspection.safe_for_fresh_runtime -ne $true) { throw 'Retained runtime/service inspection does not permit a fresh attempt.' }
    $freshId=[guid]::NewGuid().ToString('N')
    $runtime=Join-Path $code ('runtime-'+$freshId)
    $freshRulePrefix='FPF-SCM-'+$freshId
}
if (-not $remainingOnly -and (Test-Path -LiteralPath $runtime)) { throw 'Reserved runtime already exists. Preserve it; no overwrite/resume.' }
# Original rules always retain their original program paths.
$programs=@((Join-Path $code 'FPFPhase1Staging.exe'),(Join-Path $retainedRuntime 'python.exe'))
$rulePrefix='FPF-Phase1-'+(Split-Path -Leaf $stage).Substring(19)
$nonLoopback=@('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255','::-::','::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
function Assert-Firewall {
    $profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore)
    if ($profiles.Count -ne 3 -or @($profiles | Where-Object { $_.Enabled.ToString() -ne 'True' -or $_.AllowLocalFirewallRules.ToString() -eq 'False' }).Count) { throw 'Firewall policy invalid.' }
    foreach ($store in @('PersistentStore','ActiveStore')) {
        $rules=@(Get-NetFirewallRule -PolicyStore $store | Where-Object { $_.Name -like "$rulePrefix-*" -or $_.Group -eq $rulePrefix })
        if ($rules.Count -ne 6) { throw 'Exactly six existing isolation rules required.' }
        foreach ($index in @(0,1)) { foreach ($suffix in @('Outbound','Inbound','UDP')) {
            $name="$rulePrefix-$index-$suffix"
            Test-StagingFirewallRule -Rule @($rules | Where-Object {$_.Name -eq $name}) -Name $name -Active:($store -eq 'ActiveStore')
        } }
    }
    if ($freshRulesPrepared) { Assert-FreshFirewall }
}
function Assert-FreshFirewall {
    # Local variables intentionally bind the existing strict rule validator to
    # the NEW program; none of the old rule objects or expectations is changed.
    $rulePrefix=$freshRulePrefix
    $programs=@((Join-Path $code 'FPFPhase1Staging.exe'),(Join-Path $runtime 'python.exe'))
    foreach ($store in @('PersistentStore','ActiveStore')) {
        $rules=@(Get-NetFirewallRule -PolicyStore $store | Where-Object {$_.Name -like "$rulePrefix-*" -or $_.Group -eq $rulePrefix})
        if ($rules.Count -ne 3) { throw 'Exactly three fresh-runtime isolation rules required.' }
        foreach ($suffix in @('Outbound','Inbound','UDP')) {
            $name="$rulePrefix-1-$suffix"
            Test-StagingFirewallRule -Rule @($rules | Where-Object {$_.Name -eq $name}) -Name $name -Active:($store -eq 'ActiveStore')
        }
    }
}
function Add-FreshFirewall {
    foreach ($store in @('PersistentStore','ActiveStore')) {
        if (@(Get-NetFirewallRule -PolicyStore $store | Where-Object {$_.Name -like "$freshRulePrefix-*" -or $_.Group -eq $freshRulePrefix}).Count) { throw 'Fresh firewall namespace already exists; no overwrite.' }
    }
    foreach ($suffix in @('Outbound','Inbound','UDP')) {
        $name="$freshRulePrefix-1-$suffix"
        $direction=if ($suffix -eq 'Inbound') {'Inbound'} else {'Outbound'}
        $protocol=if ($suffix -eq 'UDP') {'UDP'} else {'Any'}
        $addresses=if ($suffix -eq 'UDP') {@('Any')} else {$nonLoopback}
        New-NetFirewallRule -PolicyStore PersistentStore -Name $name -DisplayName $name -Group $freshRulePrefix `
            -Enabled True -Profile Any -Direction $direction -Action Block -Program (Join-Path $runtime 'python.exe') `
            -Protocol $protocol -RemoteAddress $addresses | Out-Null
    }
    Assert-FreshFirewall
}
Assert-Firewall
# Snapshot old evidence once, before making any new directory. Do not rerun tests.
$oldFiles=@{}
$pendingDirectories=[Collections.Generic.Stack[string]]::new()
$pendingDirectories.Push($stage)
while ($pendingDirectories.Count -gt 0) {
    foreach ($file in @(Get-ChildItem -LiteralPath $pendingDirectories.Pop() -Force)) {
        if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse evidence refused.' }
        if ($file.PSIsContainer) { $pendingDirectories.Push($file.FullName) }
        else { $oldFiles[$file.FullName]=(Get-FileHash -LiteralPath $file.FullName).Hash }
    }
}
function Set-NewPrivateDirectory {
    param([string]$Path,[Security.AccessControl.FileSystemRights]$ServiceRights)
    $acl=[Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    $acl.SetAccessRuleProtection($true,$false)
    foreach ($sid in @('S-1-5-18','S-1-5-32-544',$serviceSid)) {
        $rights=if ($sid -eq $serviceSid) {$ServiceRights} else {[Security.AccessControl.FileSystemRights]::FullControl}
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),$rights,'ContainerInherit,ObjectInherit','None','Allow'))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    Test-PrivateStagingDirectory $Path $ServiceRights
}
$attempt=Join-Path $stage ('scm-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $attempt | Out-Null
Set-NewPrivateDirectory $attempt ReadAndExecute
$bundle=Join-Path $attempt 'code'; $output=Join-Path $attempt 'data'
foreach ($path in @($bundle,$output)) { New-Item -ItemType Directory -Path $path | Out-Null }
Set-NewPrivateDirectory $bundle ReadAndExecute
Set-NewPrivateDirectory $output Modify
$svc | Select-Object Name,State,StartMode,StartName,PathName,ExitCode,ServiceSpecificExitCode,ProcessId |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'original-registration.json') -Encoding UTF8
if ($null -ne $retainedInspection) {
    $retainedInspection | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'retained-runtime-inspection.json') -Encoding UTF8
}
$hostSource=Join-Path $PSScriptRoot 'phase1_staging_scm_host.py'
$sourceFiles=@('durable_sync.py','durable_startup.py','durable_delivery.py','sync_ledger.py',
    'punch_normalization.py','SMWinservice.py','erpnext_sync_win.py',
    'scripts\phase1_staging_engine_https.py','scripts\phase1_staging_engine_crash.py','scripts\phase1_staging_scm_host.py')
$hashes=@{}
foreach ($name in $sourceFiles) {
    $source=Join-Path $repo $name; $target=Join-Path $bundle (Split-Path -Leaf $name)
    Copy-Item -LiteralPath $source -Destination $target
    $hash=(Get-FileHash -LiteralPath $source).Hash
    if ((Get-FileHash -LiteralPath $target).Hash -ne $hash) { throw 'Bundle copy mismatch.' }
    $hashes[(Split-Path -Leaf $name)]=$hash.ToLowerInvariant()
}
Copy-Item -LiteralPath (Join-Path $stage 'localhost-cert.pem') -Destination (Join-Path $bundle 'localhost-cert.pem')
$hashes['localhost-cert.pem']=(Get-FileHash -LiteralPath (Join-Path $bundle 'localhost-cert.pem')).Hash.ToLowerInvariant()
@{sha256=$hashes} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $bundle 'bundle.json') -Encoding ASCII
$launcher=(Get-Command py.exe -CommandType Application -ErrorAction Stop).Source
function New-HelperLog([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw 'Helper log already exists; preserve evidence.' }
    New-Item -ItemType File -Path $Path | Out-Null
    $acl=Get-Acl -LiteralPath $Path
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    Set-Acl -LiteralPath $Path -AclObject $acl
}
function Set-NewDataOwners {
    # The engine validates EVERY file in its ledger parent under the service
    # token. Administrator-created files must not retain an individual user as
    # owner. Touch only new artifacts in this session, never old evidence.
    foreach ($file in @(Get-ChildItem -LiteralPath $output -Force -File)) {
        $acl=Get-Acl -LiteralPath $file.FullName
        $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($owner -in @('S-1-5-18','S-1-5-32-544',$serviceSid)) { continue }
        if ($owner -ne $identity.User.Value) { throw 'Unexpected new evidence owner.' }
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        Set-Acl -LiteralPath $file.FullName -AclObject $acl
    }
}
function Start-LocalChild {
    param([string]$Exe,[string[]]$Arguments,[string]$Label)
    # Native argument quoting for file paths containing spaces. These arguments
    # contain no embedded quotes or trailing backslashes.
    if (@($Arguments | Where-Object { $_.Contains('"') -or $_.EndsWith('\') }).Count) { throw 'Unsupported child argument.' }
    $quoted=@($Arguments | ForEach-Object { '"'+$_+'"' }) -join ' '
    New-HelperLog (Join-Path $output ($Label+'-stdout.txt'))
    New-HelperLog (Join-Path $output ($Label+'-stderr.txt'))
    $process=Start-Process -FilePath $Exe -ArgumentList $quoted -WorkingDirectory $bundle -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $output ($Label+'-stdout.txt')) -RedirectStandardError (Join-Path $output ($Label+'-stderr.txt'))
    $handle=$process.Handle
    return $process
}
function Finish-LocalChild {
    param($Process,[string]$Label,[int]$Milliseconds=30000)
    if (-not $Process.WaitForExit($Milliseconds)) {
        $Process.Kill(); $Process.WaitForExit()
        throw 'Owned staging helper timed out.'
    }
    $Process.WaitForExit()
    if ($null -eq $Process.ExitCode -or $Process.ExitCode -ne 0) { throw "Staging helper failed: $Label (native exit unavailable/nonzero)." }
}
function Invoke-Host {
    param([string[]]$Extra,[string]$Label)
    $process=Start-LocalChild $python (@('-I','-S','-B',$hostPath)+$Extra) $Label
    Finish-LocalChild $process $Label
}
function Read-Json([string]$Path) { Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
function Wait-Evidence([string]$Name) {
    $path=Join-Path $output $Name
    $until=[DateTime]::UtcNow.AddSeconds(25)
    while (-not (Test-Path -LiteralPath $path)) {
        $state=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
        if ($state.State -eq 'Stopped' -or [DateTime]::UtcNow -ge $until) { throw "New service evidence unavailable: $Name" }
        Start-Sleep -Milliseconds 100
    }
    Read-Json $path
}
function Get-StagingService {
    $value=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
    if ($null -eq $value -or $value.StartName -ne 'NT SERVICE\FPFPhase1Staging' -or
        $value.StartMode -ne 'Manual' -or $value.PathName -ne $image) { throw 'Staging registration changed unexpectedly.' }
    return $value
}
$mock=$null; $failure=$null; $nativeFailure=$null; $operation='runtime-copy'; $checks=@{}; $timings=@{}
$python=Join-Path $runtime 'python.exe'; $hostPath=Join-Path $bundle 'phase1_staging_scm_host.py'
$image='"'+$python+'" -I -S -B "'+$hostPath+'"'
try {
    if ($FreshRuntime) {
        $operation='fresh-runtime-firewall'
        Add-FreshFirewall
        $freshRulesPrepared=$true
        Assert-Firewall
    }
    $operation=if ($remainingOnly) {'retained-runtime'} else {'runtime-copy'}
    if (-not $remainingOnly) {
        $pack=Start-LocalChild $launcher @('-3','-I','-S','-B',$hostSource,'--package-runtime',$runtime) 'runtime-copy'
        Finish-LocalChild $pack 'runtime-copy' 60000
        # Only a NEW runtime receives ACLs; the reused runtime is never modified.
        Set-NewPrivateDirectory $runtime ReadAndExecute
    } elseif ((Get-FileHash -LiteralPath (Join-Path $runtime 'runtime-manifest.json')).Hash -ne $reusedManifestHash) {
        throw 'Retained runtime manifest provenance changed.'
    }
    $runtimeManifest=Read-Json (Join-Path $runtime 'runtime-manifest.json')
    foreach ($entry in $runtimeManifest.sha256.PSObject.Properties) {
        if ([IO.Path]::IsPathRooted($entry.Name) -or $entry.Name -match '(^|[\\/])\.\.([\\/]|$)') { throw 'Invalid runtime manifest path.' }
        if ((Get-FileHash -LiteralPath (Join-Path $runtime $entry.Name)).Hash -ne $entry.Value) { throw 'Runtime hash mismatch.' }
    }
    if ($remainingOnly) {
        $runtimeInventory=@($oldFiles.Keys | Where-Object {$_.StartsWith($runtime+'\',[StringComparison]::OrdinalIgnoreCase)})
        if ($runtimeInventory.Count -ne @($runtimeManifest.sha256.PSObject.Properties).Count+1) { throw 'Unexpected retained runtime files.' }
        if ((Get-FileHash -LiteralPath $idlePath).Hash -ne $idleEvidenceHash) { throw 'Idle evidence changed.' }
        $checks['accepted_idle_evidence_reused']=$true
    }
    $operation='mock-startup'
    $mock=Start-LocalChild $launcher @('-3','-I','-S','-B',$hostPath,'--mock-stage',$stage) 'mock'
    $ready=Join-Path $output 'mock-ready.json'; $until=[DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $ready)) {
        if ($mock.HasExited -or [DateTime]::UtcNow -ge $until) { throw 'Local mock startup failed.' }
        Start-Sleep -Milliseconds 100
    }
    $port=(Read-Json $ready).port
    if ($port -isnot [int] -or $port -lt 1 -or $port -gt 65535) { throw 'Invalid mock port.' }
    @{port=$port;cutover=[DateTime]::Now.AddMinutes(2).ToString('yyyy-MM-dd HH:mm:ss');service_sid=$serviceSid} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundle 'settings.json') -Encoding ASCII
    $operation='preflight-and-provision'
    if (-not $remainingOnly) { Invoke-Host @('--preflight') 'preflight' }
    Invoke-Host @('--provision') 'provision'
    Set-NewDataOwners
    Assert-Firewall
    $operation='staging-ImagePath-change'
    $change=Invoke-CimMethod -InputObject $svc -MethodName Change -Arguments @{PathName=$image}
    if ($change.ReturnValue -ne 0) { throw "Staging ImagePath change failed; CIM result $($change.ReturnValue)." }
    foreach ($phase in (Get-SessionPhases)) {
        $operation=$phase
        if ((Get-StagingService).State -ne 'Stopped') { throw 'Staging service must be stopped before each start.' }
        $controller=Get-Service -Name FPFPhase1Staging
        $controller.Start([string[]]@($phase))
        $report=Wait-Evidence ($phase+'-ready.json')
        $current=Get-StagingService
        if ($report.phase -ne $phase -or $report.sid -ne $serviceSid -or $report.pid -ne $current.ProcessId -or $current.State -ne 'Running') { throw 'Wrong runtime token/process/phase.' }
        $checks["${phase}_actual_token_and_process"]=$true
        if ($phase -ne 'inflight') {
            Invoke-Host @('--inspect','locked') ($phase+'-lock')
            if ($phase -eq 'idle') { $checks['idle_lifetime_mutex_owned']=$true }
        }
        $clock=[Diagnostics.Stopwatch]::StartNew()
        $controller.Stop()
        if ($phase -eq 'inflight') {
            $checks['inflight_stop_pending']=((Get-StagingService).State -eq 'Stop Pending')
            if (-not $checks['inflight_stop_pending']) { throw 'Expected actual SCM STOP_PENDING while capture completes.' }
        }
        $controller.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30))
        $clock.Stop(); $timings[$phase]=$clock.Elapsed.TotalSeconds
        $stopped=Get-StagingService
        if ($stopped.ExitCode -ne 0 -or $stopped.ServiceSpecificExitCode -ne 0 -or $stopped.ProcessId -ne 0) { throw 'Staging service exited with an error.' }
        $checks["${phase}_clean_bounded_stop"]=($clock.Elapsed.TotalSeconds -lt $(if ($phase -eq 'inflight') {30} else {10}))
        Invoke-Host @('--inspect','unlocked') ($phase+'-unlocked')
        $checks["${phase}_mutex_released_and_integrity"]=$true
        $complete=Read-Json (Join-Path $output ($phase+'-complete.json'))
        if ($complete.integrity -ne 'ok' -or $complete.sid -ne $serviceSid) { throw 'Invalid completed-cycle evidence.' }
        if ($phase -eq 'idle') { $checks['idle_empty_ledger']=($complete.states.pending -eq 0 -and $complete.states.synced -eq 0) }
        if ($phase -eq 'inflight') { $checks['stop_preserved_pending_without_send']=($complete.stop_requested -eq $true -and $complete.states.pending -eq 1 -and $complete.states.synced -eq 0) }
        if ($phase -eq 'recover') { $checks['restart_recovered_same_pending']=($complete.states.pending -eq 0 -and $complete.states.synced -eq 1) }
        if (@($checks.Values | Where-Object {$_ -ne $true}).Count) { throw 'SCM acceptance assertion failed.' }
    }
    Finish-LocalChild $mock 'mock' 5000
    Set-NewDataOwners
    $mockReport=Read-Json (Join-Path $output 'mock-result.json')
    $checks['one_synthetic_https_submission']=($mockReport.status -eq 'PASS' -and $mockReport.mock_records -eq 1 -and $mockReport.stopped -eq $true)
    $checks['helpers_quiet']=(@(Get-ChildItem -LiteralPath $output -Filter '*-stderr.txt' | Where-Object {$_.Length -ne 0}).Count -eq 0)
    Assert-Firewall
    foreach ($path in $oldFiles.Keys) { if ((Get-FileHash -LiteralPath $path).Hash -ne $oldFiles[$path]) { throw 'Previous staging evidence changed.' } }
    if ($remainingOnly -and (Get-FileHash -LiteralPath $idlePath).Hash -ne $idleEvidenceHash) { throw 'Accepted idle evidence changed.' }
    $checks['old_evidence_unchanged']=$true
} catch {
    $failure="Staging session failed during $operation. Preserve this attempt."
    $native=$null; $errorType=$_.Exception.GetType().Name; $errorHResult=$_.Exception.HResult
    $cause=$_.Exception
    while ($null -ne $cause) {
        if ($cause -is [ComponentModel.Win32Exception]) { $native=$cause.NativeErrorCode }
        $cause=$cause.InnerException
    }
    $nativeFailure=@{operation=$operation;category=$errorType;hresult=$errorHResult;win32_code=$native;
        cim_return_code=$(if ($null -ne $change) {$change.ReturnValue} else {$null})}
} finally {
    $current=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
    if ($null -ne $current -and $current.PathName -eq $image -and $current.StartName -eq 'NT SERVICE\FPFPhase1Staging' -and $current.State -ne 'Stopped') {
        try {
            $owned=Get-Service -Name FPFPhase1Staging
            if ($current.State -ne 'Stop Pending') { $owned.Stop() }
            $owned.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30))
        } catch { $failure='Own staging service did not stop safely; no forced termination performed.' }
        $current=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
    }
    if ($null -ne $current -and $current.PathName -eq $image -and $current.State -eq 'Stopped') {
        $restore=Invoke-CimMethod -InputObject $current -MethodName Change -Arguments @{PathName=$oldImage}
        if ($restore.ReturnValue -ne 0) { $failure='Staging registration restoration failed; preserve state.' }
    } elseif ($null -ne $current -and $current.PathName -ne $oldImage) {
        $failure='Staging state is not safely stopped/restorable. No forced service termination performed.'
    }
    # Let a graceful service stop finish first. Never kill a service by name/PID;
    # only our own still-waiting foreground mock handle may be terminated.
    if ($null -ne $mock -and -not $mock.HasExited) { $mock.Kill(); $mock.WaitForExit() }
}
$final=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
$checks['original_registration_restored_stopped']=($null -ne $final -and $final.PathName -eq $oldImage -and
    $final.State -eq 'Stopped' -and $final.StartName -eq 'NT SERVICE\FPFPhase1Staging' -and $final.StartMode -eq 'Manual')
$expectedChecks=if ($remainingOnly) {14} else {18}
$passed=($null -eq $failure -and $checks.Count -eq $expectedChecks -and @($checks.Values | Where-Object {$_ -ne $true}).Count -eq 0)
@{status=$(if ($passed) {'PASS'} else {'FAIL'});checks=$checks;failure=$failure;native_failure=$nativeFailure;stop_seconds=$timings;
  engine_revision=$revision;bundle_sha256=$hashes;runner_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash;
  runtime_path=$runtime;fresh_firewall_group=$freshRulePrefix;retained_runtime_preserved=($FreshRuntime.IsPresent -or $remainingOnly);
  remaining_only=$remainingOnly;reused_idle_evidence=$idlePath;reused_idle_evidence_sha256=$idleEvidenceHash;
  settings_sha256=$(if (Test-Path -LiteralPath (Join-Path $bundle 'settings.json')) {(Get-FileHash -LiteralPath (Join-Path $bundle 'settings.json')).Hash} else {$null});
  runtime_manifest_sha256=$(if (Test-Path -LiteralPath (Join-Path $runtime 'runtime-manifest.json')) {(Get-FileHash -LiteralPath (Join-Path $runtime 'runtime-manifest.json')).Hash} else {$null});
  prior_evidence_sha256=$oldFiles;production_access=$false;scope='SCM_staging_only';activation_ready=$false} |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'result.json') -Encoding UTF8
Write-Output "Evidence directory: $attempt"
if (-not $passed) { throw 'SCM STAGING FAIL. Preserve artifacts. Do not retry or change service settings blindly.' }
if ($remainingOnly) { Write-Output 'REMAINING SCM PASS: 14 checks. Original staging registration restored; service and mock stopped.' }
else { Write-Output 'SCM STAGING PASS: 18 checks. Original staging registration restored; service and mock stopped.' }
