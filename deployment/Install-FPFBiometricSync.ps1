# Staged installer. No stage runs implicitly; Preflight is read-only.
[CmdletBinding()]
param(
    [ValidateSet('Preflight','Prepare','Activate','Install','Upgrade','Rollback','Health','Uninstall')]
    [string]$Stage = 'Preflight',
    [string]$Package,
    [string]$PackageSha256,
    [string]$PythonExe,
    [string]$Config,
    [string]$AppRoot = (Join-Path $env:ProgramFiles 'FPFBiometricSync'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'FPFBiometricSync'),
    [string]$Receipt,
    [string]$PreviousReceipt,
    [string]$Cutover,
    [string]$ReviewReference,
    [string]$MonitorSidApproval,
    [switch]$CheckNetwork,
    [switch]$ExclusiveHostWindow,
    [switch]$Approve,
    [int]$HealthSeconds = 600
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ServiceName = 'ERPNextBiometricPushService'
$SystemSid = 'S-1-5-18'
$AdminSid = 'S-1-5-32-544'
$Trusted = @($SystemSid,$AdminSid,'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')

function Require($Condition,[string]$Code) { if (-not $Condition) { throw $Code } }
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Absolute([string]$Path) {
    Require ([IO.Path]::IsPathRooted($Path) -and $Path -match '^[A-Za-z]:\\' -and $Path -notmatch '(^|\\)\.\.?($|\\)|:.*:') 'local_absolute_path_required'
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}
function Below([string]$Child,[string]$Parent) {
    return $Child.StartsWith($Parent.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)
}
function No-Link([string]$Path) {
    $item=Get-Item -LiteralPath $Path -Force
    Require (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'reparse_point_refused'
    return $item
}
function Trusted-Path([string]$Path,[switch]$Ancestor) {
    $item=No-Link $Path
    $acl=Get-Acl -LiteralPath $item.FullName
    Require ($Trusted -contains $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value) 'untrusted_owner'
    $forbidden=if($Ancestor){0xD0040}else{0xD0156}
    foreach($ace in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
        if($ace.AccessControlType -eq 'Allow' -and $Trusted -notcontains $ace.IdentityReference.Value -and
           ([int]$ace.PropagationFlags -band 2) -eq 0) {
            Require (([long]$ace.FileSystemRights -band $forbidden) -eq 0) 'untrusted_write_or_replacement'
        }
    }
}
function Private-Config([string]$Path) {
    Trusted-Path $Path
    $acl=Get-Acl -LiteralPath $Path
    foreach($ace in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        Require ($ace.AccessControlType -ne 'Allow' -or $Trusted -contains $ace.IdentityReference.Value) 'private_config_reader_untrusted'
    }
}
function Ancestors([string]$Path) {
    $item=Get-Item -LiteralPath $Path
    while($null -ne $item) { Trusted-Path $item.FullName -Ancestor; $item=$item.Parent }
}
function Protect([string]$Path,[switch]$PublicRead) {
    No-Link $Path | Out-Null
    $directory=Test-Path -LiteralPath $Path -PathType Container
    $acl=if($directory){[Security.AccessControl.DirectorySecurity]::new()}else{[Security.AccessControl.FileSecurity]::new()}
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new($AdminSid))
    $inherit=if($directory){[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}else{[Security.AccessControl.InheritanceFlags]::None}
    foreach($sid in @($SystemSid,$AdminSid)) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid),'FullControl',$inherit,'None','Allow'))
    }
    if($PublicRead) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'ReadAndExecute',$inherit,'None','Allow'))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    Trusted-Path $Path
}
function Protect-NewTree([string]$Path) {
    # Only a fresh, installer-created version slot; never an existing installation.
    Require (Below (Absolute $Path) $AppRoot) 'new_tree_outside_application_root'
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($Path)
    while($queue.Count){
        $current=$queue.Dequeue()
        Protect $current -PublicRead
        if(Test-Path -LiteralPath $current -PathType Container){
            foreach($child in Get-ChildItem -LiteralPath $current -Force){
                No-Link $child.FullName|Out-Null
                $queue.Enqueue($child.FullName)
            }
        }
    }
}
function Assert-CreationOwner([string]$Directory,[string]$AllowedRoot) {
    # Refuse a host policy that makes elevated-created data user-owned. Do not
    # change the machine's default-owner policy to make deployment pass.
    Require (Below (Absolute $Directory) (Absolute $AllowedRoot)) 'owner_probe_outside_owned_root'
    $probe=Join-Path $Directory (([guid]::NewGuid().ToString('N'))+'.owner-probe')
    $stream=[IO.File]::Open($probe,[IO.FileMode]::CreateNew);$stream.Dispose()
    $owner=(Get-Acl -LiteralPath $probe).GetOwner([Security.Principal.SecurityIdentifier]).Value
    Require ($Trusted -contains $owner) 'elevated_default_owner_requires_review'
    Require ((Get-Item -LiteralPath $probe).Length -eq 0) 'owner_probe_changed'
    [IO.File]::Delete($probe) # Exact newly created empty probe only; no directory removal.
}
function Copy-PrivateConfig([string]$Source,[string]$Destination) {
    Private-Config $Source
    $empty=[IO.File]::Open($Destination,[IO.FileMode]::CreateNew)
    $empty.Dispose()
    Protect $Destination
    [IO.File]::WriteAllBytes($Destination,[IO.File]::ReadAllBytes($Source))
}
function New-Protected([string]$Path,[switch]$PublicRead) {
    Require (-not (Test-Path -LiteralPath $Path)) 'fresh_directory_required'
    Ancestors (Split-Path -Parent $Path)
    [void][IO.Directory]::CreateDirectory($Path)
    Protect $Path -PublicRead:$PublicRead
}
function Windows-Argument([string]$Value) {
    # Preserve spaces and quotes on Windows PowerShell 5.1 native invocation.
    $buffer=[Text.StringBuilder]::new()
    [void]$buffer.Append([char]34); $slashes=0
    foreach($character in $Value.ToCharArray()) {
        if($character -eq [char]92) { $slashes++; continue }
        $repeat=if($character -eq [char]34){2*$slashes+1}else{$slashes}
        if($repeat){[void]$buffer.Append([char]92,[int]$repeat)}
        [void]$buffer.Append($character);$slashes=0
    }
    if($slashes){[void]$buffer.Append([char]92,[int](2*$slashes))}
    [void]$buffer.Append([char]34)
    return $buffer.ToString()
}
function Native([string]$Exe,[string[]]$Arguments,[int]$Timeout=300) {
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=$Exe
    $start.Arguments=(@($Arguments|ForEach-Object{Windows-Argument $_}) -join ' ')
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
    $process=[Diagnostics.Process]::new();$process.StartInfo=$start
    try {
        Require ($process.Start()) 'native_start_failed'
        $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()
        if(-not $process.WaitForExit($Timeout*1000)){
            # Only this owned child, never a process discovered by PID/name.
            $process.Kill();$process.WaitForExit();throw 'owned_child_timeout'
        }
        $stdout=$out.Result;$stderr=$err.Result
        Require ($process.ExitCode -eq 0) ('native_failed_exit_'+$process.ExitCode)
        return $stdout.Trim()
    } finally {$process.Dispose()}
}
function Safe-Python([string]$Exe) {
    $Exe=Absolute $Exe
    Require ([IO.Path]::GetFileName($Exe) -ieq 'python.exe') 'python_exe_required'
    $base=Split-Path -Parent $Exe
    Trusted-Path $base; Ancestors $base
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($base)
    while($queue.Count){
        foreach($item in Get-ChildItem -LiteralPath $queue.Dequeue() -Force){
            Trusted-Path $item.FullName
            if($item.PSIsContainer){$queue.Enqueue($item.FullName)}
        }
    }
    $info=Native $Exe @('-I','-B','-c','import sys,struct,json; assert sys.version_info[:3]==(3,11,9) and struct.calcsize("P")==8; print(json.dumps(dict(base=sys.base_prefix,prefix=sys.prefix)))') | ConvertFrom-Json
    Require ($info.base -eq $base -and $info.prefix -eq $base) 'protected_base_python_required'
}
function Tool($r,[string]$Action,[string[]]$Extra=@(),[int]$Timeout=300) {
    $arguments=@('-I','-B',(Join-Path $r.code 'deployment\portable_release.py'),$Action,'--code',$r.code,'--config',$r.config)+$Extra
    return Native $r.python $arguments $Timeout
}
function Service { Get-CimInstance Win32_Service -Filter ("Name='"+$ServiceName+"'") -OperationTimeoutSec 15 }
function Mutex-Absent {
    # Open only: never adopt/acquire the application's global worker mutex.
    try {
        $m=[Threading.Mutex]::OpenExisting('Global\ERPNextBiometricPushService-DurableWorker')
        $m.Dispose();throw 'another_worker_or_mutex_object_present'
    } catch [Threading.WaitHandleCannotBeOpenedException] { }
}
function Python-Inventory {
    foreach($p in @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")) {
        Require ($p.ExecutablePath -and $p.CommandLine) 'python_process_metadata_unavailable'
        Require ($p.CommandLine -notmatch 'erpnext_sync(_win)?\.py|scm_host\.py|phase1_staging') 'other_worker_process_present'
    }
}
function Stop-Owned($r) {
    $s=Service
    Require ($s -and $s.PathName -ceq $r.command -and $s.StartName -eq 'LocalSystem') 'unrecognized_service'
    $worker=if($s.ProcessId){Get-Process -Id $s.ProcessId -ErrorAction Stop}else{$null}
    if($s.State -ne 'Stopped') {
        $result=Invoke-CimMethod -InputObject $s -MethodName StopService -OperationTimeoutSec 15
        Require ($result.ReturnValue -eq 0) 'stop_request_failed'
    }
    (Get-Service $ServiceName).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(120))
    if($worker){Require ($worker.WaitForExit(120000)) 'worker_did_not_exit'}
    Python-Inventory; Mutex-Absent
}
function Set-Image($Expected,$Replacement) {
    $s=Service
    Require ($s.State -eq 'Stopped' -and $s.PathName -ceq $Expected -and $s.StartName -eq 'LocalSystem') 'service_switch_precondition'
    $result=Invoke-CimMethod -InputObject $s -MethodName Change -Arguments @{PathName=$Replacement} -OperationTimeoutSec 15
    Require ($result.ReturnValue -eq 0 -and (Service).PathName -ceq $Replacement) 'service_switch_failed'
}
function Save($r) {
    $target=$r.receipt_path
    $json=$r|ConvertTo-Json -Depth 20
    $temporary=$target+'.'+[guid]::NewGuid().ToString('N')+'.tmp'
    [IO.File]::WriteAllText($temporary,$json,[Text.UTF8Encoding]::new($false))
    if(Test-Path -LiteralPath $target) {
        [IO.File]::Replace($temporary,$target,[NullString]::Value)
    } else {[IO.File]::Move($temporary,$target)}
}
function Read-Receipt([string]$Path) {
    $Path=Absolute $Path
    Trusted-Path $Path;Ancestors (Split-Path -Parent $Path)
    $r=Get-Content -LiteralPath $Path -Raw|ConvertFrom-Json
    Require ($r.schema -ceq 'fpf-installation-v1' -and $r.service -ceq $ServiceName -and $r.receipt_path -ieq $Path) 'invalid_receipt'
    foreach($p in @($r.code,$r.python,$r.config,$r.package_root)) {
        Require (Below (Absolute $p) $r.app_root) 'receipt_path_escape'
        Trusted-Path $p
        Ancestors (Split-Path -Parent $p)
    }
    Ancestors $r.app_root
    Require ($r.command -ceq ('"'+$r.python+'" -B "'+(Join-Path $r.code 'erpnext_sync_win.py')+'"')) 'receipt_command_mismatch'
    Private-Config $r.config
    Require ((Hash $r.config) -ceq $r.config_sha256) 'private_configuration_changed'
    foreach($property in $r.code_hashes.PSObject.Properties) {
        Require ($property.Name -notmatch '\\|:|(^|/)\.\.?(/|$)') 'receipt_code_path'
        Require ((Hash (Join-Path $r.code $property.Name)) -ceq $property.Value) 'installed_source_changed'
    }
    return $r
}
function Same-Core($old,$new) {
    Require ($old.config_sha256 -ceq $new.config_sha256) 'upgrade_configuration_must_be_preserved'
    # This first hardening release supports packaging-only upgrades. Any changed
    # application protocol needs a new reviewed compatibility policy, not a switch.
    $a=@{};$b=@{}
    foreach($pair in @(@($old,$a),@($new,$b))) {
        foreach($p in $pair[0].code_hashes.PSObject.Properties) {
            if($p.Name -match '^[^/]+\.py$|^historical_attendance/.*\.py$'){$pair[1][$p.Name]=$p.Value}
        }
    }
    Require ((@($a.Keys|Sort-Object|ForEach-Object{$_+'='+$a[$_]}) -join "\n") -ceq
             (@($b.Keys|Sort-Object|ForEach-Object{$_+'='+$b[$_]}) -join "\n")) 'changed_application_requires_compatibility_review'
}
function Start-Healthy($r,[switch]$Start) {
    $since=Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fff'
    if($Start){
        $result=Invoke-CimMethod -InputObject (Service) -MethodName StartService -OperationTimeoutSec 15
        Require ($result.ReturnValue -eq 0) 'start_request_failed'
    } else {Require ((Service).State -eq 'Running') 'service_not_running'}
    (Get-Service $ServiceName).WaitForStatus('Running',[TimeSpan]::FromSeconds(60))
    $generation=(Service).ProcessId
    $deadline=[DateTime]::UtcNow.AddSeconds($HealthSeconds)
    do {
        $s=Service
        Require ($s.State -eq 'Running' -and $s.PathName -ceq $r.command -and $s.StartName -eq 'LocalSystem') 'service_not_healthy'
        $process=Get-CimInstance Win32_Process -Filter ("ProcessId="+$s.ProcessId)
        Require ($s.ProcessId -eq $generation -and $process -and $process.ExecutablePath -in @($r.python,$r.base_python) -and $process.CommandLine -and $process.CommandLine.Contains((Join-Path $r.code 'erpnext_sync_win.py'))) 'actual_service_process_mismatch'
        $owner=Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid -OperationTimeoutSec 15
        Require ($owner.ReturnValue -eq 0 -and $owner.Sid -eq $SystemSid) 'actual_localsystem_required'
        $remaining=[int][Math]::Ceiling(($deadline-[DateTime]::UtcNow).TotalSeconds)
        Require ($remaining -gt 0) 'health_deadline_expired'
        $probeSeconds=[Math]::Min(60,$remaining)
        $health=Tool $r 'health' @('--since',$since) $probeSeconds|ConvertFrom-Json
        if($health.healthy -and $health.delivery_observed) {
            $r.last_health=$health
            Save $r
            return
        }
        Start-Sleep -Seconds 5
    } while([DateTime]::UtcNow -lt $deadline)
    throw 'health_deadline_expired_capture_or_health_missing'
}

# Read-only prerequisites apply before every stage, including recovery.
Require ($env:OS -eq 'Windows_NT' -and [Environment]::Is64BitOperatingSystem -and
         [Environment]::Is64BitProcess -and $PSVersionTable.PSVersion.Major -ge 5) 'windows_x64_powershell_required'
Require ($HealthSeconds -ge 30 -and $HealthSeconds -le 1800) 'health_deadline_out_of_range'
$AppRoot=Absolute $AppRoot;$DataRoot=Absolute $DataRoot
Require ($AppRoot -ne $DataRoot -and -not (Below $AppRoot $DataRoot) -and -not (Below $DataRoot $AppRoot) -and
         (Split-Path -Parent $AppRoot) -and (Split-Path -Parent $DataRoot)) 'application_data_separation'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=[Security.Principal.WindowsPrincipal]::new($identity)
$lease=$null
try {
    if($Stage -ne 'Preflight') {
        Require ($Approve -and $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'explicit_elevated_approval_required'
        $created=$false
        $lease=[Threading.Mutex]::new($true,'Global\FPFBiometricSync-Deployment',[ref]$created)
        Require $created 'another_deployment_controller_present'
    }
    if($Stage -in @('Preflight','Prepare')) {
        $os=Get-CimInstance Win32_OperatingSystem
        Require ([int]$os.BuildNumber -ge 22000 -and $os.ProductType -eq 1) 'supported_baseline_windows_11_required'
        foreach($root in @($AppRoot,$DataRoot)){
            $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
            Require ($drive.DriveType -eq 'Fixed' -and $drive.DriveFormat -eq 'NTFS') 'local_NTFS_required'
        }
        Safe-Python $PythonExe
        Require ($PackageSha256 -cmatch '^[a-f0-9]{64}$' -and (Hash $Package) -ceq $PackageSha256) 'independently_approved_package_required'
        Private-Config $Config;Ancestors (Split-Path -Parent $Config)
        foreach($root in @($AppRoot,$DataRoot)) {
            if(Test-Path -LiteralPath $root){Trusted-Path $root;Ancestors $root}
            else {Ancestors (Split-Path -Parent $root)}
        }
        if($Stage -eq 'Preflight') {
            # No Python from the package, extraction, network or SQLite access.
            [pscustomobject]@{stage='READ_ONLY_PREREQUISITES_PASS';existing_service=($null -ne (Service));remaining='Prepare validates manifest/config; SCM and live health pending'}
            return
        }
        foreach($root in @($AppRoot,$DataRoot)) {
            if(-not(Test-Path -LiteralPath $root)){New-Protected $root -PublicRead:($root -eq $AppRoot)}
        }
        $slot=Join-Path $AppRoot ([guid]::NewGuid().ToString('N'))
        New-Protected $slot -PublicRead
        Assert-CreationOwner $slot $AppRoot
        $archive=Join-Path $slot 'approved.zip'
        Copy-Item -LiteralPath $Package -Destination $archive
        Require ((Hash $archive) -ceq $PackageSha256) 'package_changed_during_copy'
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip=[IO.Compression.ZipFile]::OpenRead($archive)
        $bundle=Join-Path $slot 'package';New-Protected $bundle -PublicRead
        try {
            $seen=@{};$total=0L
            foreach($entry in $zip.Entries) {
                $name=$entry.FullName
                Require ($name -and $name -notmatch '\\|:|(^|/)\.\.?(/|$)|^/|[ .](/|$)' -and
                         $name -notmatch '(^|/)(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|/|$)') 'unsafe_archive_entry'
                Require (-not $seen.ContainsKey($name.ToLowerInvariant())) 'duplicate_archive_entry'
                $seen[$name.ToLowerInvariant()]=$true;$total+=$entry.Length
                Require ($total -le 268435456 -and $entry.Length -le 67108864) 'package_size_limit'
                $target=Join-Path $bundle $name
                Require (Below ([IO.Path]::GetFullPath($target)) $bundle) 'archive_escape'
                [void][IO.Directory]::CreateDirectory((Split-Path -Parent $target))
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$target,$false)
            }
        } finally {$zip.Dispose()}
        Protect-NewTree $slot
        $helper=Join-Path $bundle 'code\deployment\portable_release.py'
        $manifest=Native $PythonExe @('-I','-B',$helper,'verify','--root',$bundle)|ConvertFrom-Json
        $code=Join-Path $slot 'code'
        Copy-Item -LiteralPath (Join-Path $bundle 'code') -Destination $code -Recurse
        Protect-NewTree $code
        $runtime=Join-Path $slot 'runtime'
        Native $PythonExe @('-I','-B','-m','venv','--copies',$runtime)|Out-Null
        Protect-NewTree $runtime
        $python=Join-Path $runtime 'Scripts\python.exe'
        Native $python @('-I','-B','-m','pip','--isolated','--disable-pip-version-check','--no-cache-dir','install','--no-index','--only-binary=:all:','--find-links',(Join-Path $bundle 'wheels'),'--require-hashes','-r',(Join-Path $bundle 'wheels\requirements.lock'))|Out-Null
        Protect-NewTree $runtime
        Native $python @('-I','-B','-m','pip','check')|Out-Null
        Native $python @('-I','-B',(Join-Path $code 'scripts\apply_pyzk_speedface_patch.py'))|Out-Null
        Native $python @('-I','-B',(Join-Path $code 'scripts\apply_pyzk_speedface_patch.py'),'--check')|Out-Null
        Protect-NewTree $runtime
        Native $python @('-I','-B',$helper,'runtime','--root',$bundle)|Out-Null
        $installedConfig=Join-Path $code 'local_config.py'
        # Protect an EMPTY file before copying any credentials into public code storage.
        Copy-PrivateConfig $Config $installedConfig
        $summary=Native $python @('-I','-B',$helper,'config','--config',$installedConfig)|ConvertFrom-Json
        Require ((Absolute $summary.ledger) -ne $AppRoot -and (Absolute $summary.logs) -ne $AppRoot -and -not (Below (Absolute $summary.ledger) $AppRoot) -and -not (Below (Absolute $summary.logs) $AppRoot)) 'state_must_be_outside_application'
        $hashes=[ordered]@{}
        foreach($p in $manifest.sha256.PSObject.Properties) {
            if($p.Name.StartsWith('code/')){$hashes[$p.Name.Substring(5)]=$p.Value}
        }
        $control=Join-Path $slot 'control'
        New-Protected $control
        $Receipt=Join-Path $control 'installation.json'
        $r=[pscustomobject]@{schema='fpf-installation-v1';receipt_path=$Receipt;service=$ServiceName;app_root=$AppRoot;data_root=$DataRoot;
            package_root=$bundle;code=$code;python=$python;base_python=$PythonExe;config=$installedConfig;config_sha256=(Hash $installedConfig);
            code_hashes=$hashes;release=$manifest.release_id;candidate=$manifest.uncommitted_candidate;
            command=('"'+$python+'" -B "'+(Join-Path $code 'erpnext_sync_win.py')+'"');
            state='PREPARED';last_health=$null;last_backup=$null;previous_receipt=$null}
        Save $r
        Write-Output ("PREPARED receipt: "+$Receipt)
        Write-Output 'No ledger provisioning, service registration or network check was performed.'
        return
    }
    $Receipt=Absolute $Receipt
    $r=Read-Receipt $Receipt
    if($Stage -ne 'Health'){Assert-CreationOwner (Split-Path -Parent $Receipt) $r.app_root}
    if($Stage -ne 'Health'){Require (-not $r.candidate) 'uncommitted_review_candidate_not_installable'}
    if($Stage -eq 'Activate') {
        Require $ExclusiveHostWindow 'exclusive_host_use_window_required'
        Require (-not(Service)) 'existing_service_requires_upgrade'
        Python-Inventory;Mutex-Absent
        $summary=Tool $r 'config'|ConvertFrom-Json
        if($summary.monitor_sid){Require ($MonitorSidApproval -ceq $summary.monitor_sid) 'independent_monitor_SID_approval_required'}
        $data=Join-Path $r.data_root 'data';$logs=Join-Path $r.data_root 'logs'
        Require ((Absolute $summary.ledger) -ieq (Join-Path $data 'sync.sqlite3') -and
                 (Absolute $summary.logs) -ieq $logs) 'new_install_state_paths_must_match_selected_data_root'
        Require (-not(Test-Path -LiteralPath $data) -and -not(Test-Path -LiteralPath $logs)) 'fresh_data_required'
        New-Protected $data;New-Protected $logs
        Tool $r 'provision' @('--cutover',$Cutover,'--reference',$ReviewReference)|Out-Null
        if($summary.monitor_sid) {
            Require ($MonitorSidApproval -ceq $summary.monitor_sid) 'independent_monitor_SID_approval_required'
            $sid=[Security.Principal.SecurityIdentifier]::new($summary.monitor_sid)
            foreach($pair in @(@($data,'ReadAndExecute'),@($summary.ledger,'Read'))) {
                $acl=Get-Acl -LiteralPath $pair[0]
                $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,$pair[1],'None','None','Allow'))
                Set-Acl -LiteralPath $pair[0] -AclObject $acl
            }
        }
        $r.state='ACTIVATED';Save $r;return
    }
    if($Stage -eq 'Health') { Start-Healthy $r;return }
    Require $ExclusiveHostWindow 'exclusive_host_use_window_required'
    Require (-not $r.candidate) 'uncommitted_review_candidate_not_installable'
    if($Stage -eq 'Install') {
        Require ($r.state -eq 'ACTIVATED' -and -not(Service) -and $CheckNetwork) 'fresh_activated_install_and_network_approval_required'
        Tool $r 'network'|Out-Null
        Python-Inventory;Mutex-Absent
        try {
        New-Service -Name $ServiceName -BinaryPathName $r.command -DisplayName 'ERPNext Biometric Push Service' -StartupType Automatic|Out-Null
        & "$env:SystemRoot\System32\sc.exe" config $ServiceName start= delayed-auto | Out-Null
        Require ($LASTEXITCODE -eq 0) 'delayed_auto_configuration_failed'
        Start-Healthy $r -Start;$r.state='INSTALLED';Save $r
        }
        catch {
            Stop-Owned $r
            Set-Service $ServiceName -StartupType Manual
            Require ((Service).StartMode -eq 'Manual') 'failed_install_manual_restoration_failed'
            throw 'install_failed_service_stopped_manual_data_preserved'
        }
        return
    }
    if($Stage -eq 'Uninstall') {
        Stop-Owned $r
        & "$env:SystemRoot\System32\sc.exe" delete $ServiceName | Out-Null
        Require ($LASTEXITCODE -eq 0) 'service_removal_failed'
        Require (-not(Service)) 'service_deletion_pending_close_handles_and_review'
        $r.state='UNINSTALLED_DATA_RETAINED';Save $r;return
    }
    $old=if($Stage -eq 'Rollback'){Read-Receipt $r.previous_receipt}else{Read-Receipt $PreviousReceipt}
    Same-Core $old $r
    $current=if($Stage -eq 'Rollback'){$r}else{$old}
    Require ((Service).PathName -ceq $current.command) 'unrecognized_existing_installation'
    Require $CheckNetwork 'read_only_network_check_approval_required'
    Tool $old 'network'|Out-Null
    $state=Tool $r 'state'|ConvertFrom-Json
    Require ($state.recovery_intents -eq 0) 'recovery_intents_block_rollback'
    Stop-Owned $current
    $backupDir=Join-Path $r.data_root 'backups'
    if(-not(Test-Path -LiteralPath $backupDir)){New-Protected $backupDir}
    Private-Config $backupDir;Ancestors $backupDir
    $snapshot=Join-Path $backupDir (([guid]::NewGuid().ToString('N'))+'.sqlite3')
    $backupEvidence=Tool $r 'backup' @('--output',$snapshot)|ConvertFrom-Json
    $r.last_backup=@{database=$snapshot;database_sha256=$backupEvidence.database_sha256;activation_sha256=$backupEvidence.activation_sha256}
    Save $r
    # Re-read after the old writer exited. Never restore a pre-upgrade database.
    $state=Tool $r 'state'|ConvertFrom-Json
    Require ($state.recovery_intents -eq 0) 'recovery_intents_appeared_before_switch'
    if($Stage -eq 'Rollback') {
        Set-Image $r.command $old.command
        Start-Healthy $old -Start
        $r.state='ROLLED_BACK_CURRENT_LEDGER_RETAINED';Save $r;return
    }
    $r.previous_receipt=Absolute $PreviousReceipt;Save $r
    Set-Image $old.command $r.command
    try {Start-Healthy $r -Start;$r.state='INSTALLED';Save $r}
    catch {
        # Only a compatible preserved sender may resume the current state.
        Stop-Owned $r
        $state=Tool $r 'state'|ConvertFrom-Json
        Require ($state.recovery_intents -eq 0) 'unsafe_rollback_STOP_leave_service_stopped'
        Set-Image $r.command $old.command
        Start-Healthy $old -Start
        $r.state='UPGRADE_FAILED_ROLLED_BACK';Save $r
        throw 'upgrade_failed_original_sender_resumed_current_ledger'
    }
} finally {
    if($null -ne $lease) {
        try {$lease.ReleaseMutex()} catch {}
        $lease.Dispose()
    }
}
