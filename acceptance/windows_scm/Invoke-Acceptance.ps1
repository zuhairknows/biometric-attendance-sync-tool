# Preparation only until an operator explicitly invokes an administrative stage.
# No service names, ledger paths, endpoints or credentials are accepted as input.
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('Preflight','Prepare','Register','Run','Evidence','Cleanup','EnableReboot','VerifyReboot','RestoreManual')]
  [string]$Stage,
  [string]$PythonExe = 'C:\Program Files\Python311\python.exe',
  [string]$Wheelhouse,
  [string]$WheelhouseManifestSha256,
  [string]$MonitorAccount,
  [string]$MonitorSid,
  [string]$OpenSsl = 'C:\Program Files\Git\usr\bin\openssl.exe',
  [string]$Approval,
  [string]$MonitorResultJson,
  [string]$RebootApproval
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Candidate = '4f20179f5fb10f84a6f86a6ff720f9c2876efc20'
$RunId = 'A4F20179C01'
$Root = 'C:\FPFPhase2AclAcceptance-A4F20179C01'
$Evidence = 'C:\FPFPhase2AclEvidence-A4F20179C01'
$Service = 'FPFPhase2AclAcceptance_A4F20179C01'
$Repo = 'C:\GitHub\biometric-attendance-sync-tool'
$HostSource = Join-Path $PSScriptRoot 'scm_host.py'
$Py = Join-Path $Root 'runtime\Scripts\python.exe'
$HostPath = Join-Path $Root 'tools\scm_host.py'
$Command = '"' + $Py + '" -I -B "' + $HostPath + '" service'
$AdminSid = 'S-1-5-32-544'
$SystemSid = 'S-1-5-18'
$InstallerSid = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
$Trusted = @($AdminSid,$SystemSid,$InstallerSid)
$GitExe = 'C:\Program Files\Git\cmd\git.exe'
$ScExe = Join-Path $env:SystemRoot 'System32\sc.exe'
if (-not $Wheelhouse) { $Wheelhouse = Join-Path $Repo '.acceptance\scm-wheels' }

function Assert-True($Condition, [string]$Code) { if (-not $Condition) { throw $Code } }
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Json-Write($Value,[string]$Path) {
  [IO.File]::WriteAllText($Path,($Value | ConvertTo-Json -Depth 20),[Text.UTF8Encoding]::new($false))
}
function Windows-Argument([AllowEmptyString()][string]$Value) {
  # CommandLineToArgvW-compatible escaping; PowerShell 5.1 otherwise strips quotes.
  $buffer = [Text.StringBuilder]::new()
  [void]$buffer.Append([char]34)
  $slashes = 0
  foreach ($character in $Value.ToCharArray()) {
    if ($character -eq [char]92) { $slashes++; continue }
    $repeat = if ($character -eq [char]34) { 2 * $slashes + 1 } else { $slashes }
    if ($repeat) { [void]$buffer.Append([char]92,[int]$repeat) }
    [void]$buffer.Append($character)
    $slashes = 0
  }
  if ($slashes) { [void]$buffer.Append([char]92,[int](2 * $slashes)) }
  [void]$buffer.Append([char]34)
  return $buffer.ToString()
}
function Native([string]$Exe,[string[]]$Arguments) {
  Assert-True ([IO.Path]::IsPathRooted($Exe) -and (Test-Path -LiteralPath $Exe -PathType Leaf)) 'absolute_native_executable_required'
  $start = [Diagnostics.ProcessStartInfo]::new()
  $start.FileName = $Exe
  $start.Arguments = (@($Arguments | ForEach-Object { Windows-Argument $_ }) -join ' ')
  $start.WorkingDirectory = Split-Path -Parent $Exe
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $start
  try {
    Assert-True ($process.Start()) 'native_child_start_failed'
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(300000)) {
      # Only the process created above, held by this handle; never a caller's PID.
      $process.Kill()
      $process.WaitForExit()
      throw 'owned_native_child_timeout'
    }
    $out = $stdout.Result
    $err = $stderr.Result
    if ($out) { $out.TrimEnd() }
    if ($err) { $err.TrimEnd() }
    Assert-True ($process.ExitCode -eq 0) ('native_command_failed: ' + [IO.Path]::GetFileName($Exe))
  } finally { $process.Dispose() }
}
function Require-Admin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  Assert-True ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) 'elevated_administrator_required'
  Assert-True ($Approval -ceq ('ISOLATED SCM ' + $RunId)) 'explicit_approval_phrase_required'
}
function No-Reparse([string]$Path) {
  $item = Get-Item -LiteralPath $Path -Force
  Assert-True (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'reparse_point_refused'
  return $item
}
function Tree([string]$Path) {
  # Inspect each directory before descending: never traverse a junction to another tree.
  $item = No-Reparse $Path
  $item
  if ($item.PSIsContainer) {
    foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
      Assert-True ($child.FullName.StartsWith($Root + '\',[StringComparison]::OrdinalIgnoreCase)) 'tree_escape'
      Tree $child.FullName
    }
  }
}
function Checked-Tree([string]$Path) {
  $item = No-Reparse $Path
  $item
  if ($item.PSIsContainer) {
    foreach ($child in Get-ChildItem -LiteralPath $Path -Force) { Checked-Tree $child.FullName }
  }
}
function Trusted-Object([string]$Path,[bool]$Ancestor) {
  $item = No-Reparse $Path
  $acl = Get-Acl -LiteralPath $Path
  $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
  Assert-True ($Trusted -contains $owner) ('untrusted_owner: ' + $Path)
  foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
    if ($rule.AccessControlType -eq 'Allow' -and $Trusted -notcontains $rule.IdentityReference.Value -and
        (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0)) {
      $mask = [int64]$rule.FileSystemRights
      # Runtime/config files reject writes; ancestors may allow sibling creation.
      $forbidden = if ($Ancestor) { 0xD0040 } else { 0xD0156 }
      Assert-True (($mask -band $forbidden) -eq 0) ('untrusted_write_or_replacement: ' + $Path)
    }
  }
}
function Safe-Runtime([string]$Executable) {
  Assert-True ([IO.Path]::IsPathRooted($Executable) -and [IO.Path]::GetFileName($Executable) -ieq 'python.exe') 'absolute_base_python_exe_required'
  $base = Split-Path -Parent $Executable
  # Trust checks precede any execution, including in an elevated preflight.
  Trusted-Object $Executable $false
  Trusted-Object $base $false
  foreach ($item in Checked-Tree $base) { Trusted-Object $item.FullName $false }
  $path = Get-Item -LiteralPath $base
  while ($null -ne $path) { Trusted-Object $path.FullName $true; $path = $path.Parent }
  $details = (Native $Executable @('-I','-B','-c',
    'import json,sys,struct; assert sys.version_info[:3]==(3,11,9) and struct.calcsize("P")==8; print(json.dumps({"base":sys.base_prefix,"prefix":sys.prefix,"exe":sys.executable}))')) | ConvertFrom-Json
  Assert-True ($details.base -eq $details.prefix -and $details.base -eq (Split-Path -Parent $Executable)) 'machine_wide_base_interpreter_required'
  # A user-owned interpreter or stdlib must never be elevated to LocalSystem.
  foreach ($item in Checked-Tree $details.base) { Trusted-Object $item.FullName $false }
  $path = Get-Item -LiteralPath $details.base
  while ($null -ne $path) { Trusted-Object $path.FullName $true; $path = $path.Parent }
  return $details
}
function Get-TestService {
  Get-CimInstance Win32_Service -Filter ("Name='" + $Service + "'")
}
function Owned-Service([switch]$AllowAutomatic) {
  $svc = Get-TestService
  Assert-True ($null -ne $svc -and $svc.PathName -ceq $Command -and $svc.StartName -eq 'LocalSystem' -and
    ($svc.StartMode -eq 'Manual' -or ($AllowAutomatic -and $svc.StartMode -eq 'Auto')) -and $svc.DisplayName -ceq ('Disposable Phase 2 ACL acceptance ' + $RunId)) 'unrecognized_service_refused'
  return $svc
}

function Normalize-WindowsPath([string]$Value) {
  Assert-True (-not [string]::IsNullOrWhiteSpace($Value)) 'missing_path_metadata'
  $value = $Value.Replace('/','\')
  if ($value.StartsWith('\\?\')) { $value = $value.Substring(4) }
  $value = [regex]::Replace($value,'\\+','\')
  Assert-True ($value -match '^[a-zA-Z]:\\') 'ambiguous_path_metadata'
  return [IO.Path]::GetFullPath($value).TrimEnd('\').ToLowerInvariant()
}
function Path-InEnvironment([string]$Value) {
  $normalized = Normalize-WindowsPath $Value
  foreach ($base in @($Root,$Evidence)) {
    $boundary = Normalize-WindowsPath $base
    if ($normalized -eq $boundary -or $normalized.StartsWith($boundary + '\',[StringComparison]::OrdinalIgnoreCase)) { return $true }
  }
  return $false
}
function Process-Classification($Process) {
  # Missing metadata cannot establish absence. Only OS kernel pseudo-processes are exempt.
  if ($Process.ProcessId -eq 0 -and $Process.Name -eq 'System Idle Process') { return 'clear' }
  if ($Process.ProcessId -eq 4 -and $Process.Name -eq 'System') { return 'clear' }
  $exe = $Process.ExecutablePath
  $line = $Process.CommandLine
  if ($exe) {
    try { if (Path-InEnvironment $exe) { return 'active' } } catch { return 'unknown' }
  }
  if ($line) {
    foreach ($match in [regex]::Matches($line,'(?i)"(?<path>[a-z]:[\\/][^"]+)"|(?<path>[a-z]:[\\/][^\s"<>|]+)')) {
      try { if (Path-InEnvironment $match.Groups['path'].Value) { return 'active' } } catch { return 'unknown' }
    }
    $normalized = [regex]::Replace($line.Replace('/','\'),'\\+','\').ToLowerInvariant()
    foreach ($base in @($Root,$Evidence)) {
      $boundary = [regex]::Escape((Normalize-WindowsPath $base))
      if ($normalized -match ('(?<![a-z0-9_\\])' + $boundary + '(?=\\|["''\s]|$)')) { return 'active' }
    }
    # Relative paths, short names, environment substitutions and NT aliases need operator investigation.
    if ($normalized -match 'scm_host\.py|%|\\device\\|\\\?\?\\|~[0-9]') { return 'unknown' }
  }
  if (-not $exe -or -not $line) { return 'unknown' }
  return 'clear'
}
function Assert-Processes([int]$ExpectedPid=0) {
  $observed = @(Get-CimInstance Win32_Process -Property ProcessId,Name,ExecutablePath,CommandLine,CreationDate)
  Assert-True ($observed.Count -gt 0) 'process_inventory_unavailable'
  foreach ($process in $observed) {
    # This controller is intentionally executing the protected installed driver.
    if ($process.ProcessId -eq $PID) { continue }
    $kind = Process-Classification $process
    if ($ExpectedPid -gt 0 -and $process.ProcessId -eq $ExpectedPid) {
      Assert-True ($kind -eq 'active' -and (Normalize-WindowsPath $process.ExecutablePath) -ceq
        (Normalize-WindowsPath $Py) -and $process.CommandLine -ceq $Command) 'expected_worker_process_mismatch'
      continue
    }
    Assert-True ($kind -eq 'clear') ('active_or_unclassifiable_process: ' + $process.ProcessId)
  }
  if ($ExpectedPid -gt 0) {
    Assert-True (@($observed | Where-Object ProcessId -eq $ExpectedPid).Count -eq 1) 'expected_worker_missing'
  }
}
function Assert-Worker {
  $before = Owned-Service
  $generation = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $before.ProcessId)
  Assert-True ($before.State -eq 'Running' -and $before.ProcessId -gt 0) 'expected_service_not_running'
  Assert-Processes $before.ProcessId
  Native $Py @('-I','-B',$HostPath,'contender','--pid',[string]$before.ProcessId) | Out-Null
  $after = Owned-Service
  $processAfter = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $after.ProcessId)
  Assert-True ($null -ne $generation -and $null -ne $processAfter -and
    $generation.CreationDate -eq $processAfter.CreationDate -and $after.State -eq 'Running' -and
    $after.ProcessId -eq $before.ProcessId) 'expected_service_changed'
}
$script:QuietLocks = @()
function Enter-Quiet {
  Assert-Processes
  # Create-only claims; no waiting on, releasing or inheriting another owner's objects.
  foreach ($name in @('Global\FPFPhase2AclAcceptance_A4F20179C01-Worker',
                       'Global\ERPNextBiometricPushService-DurableWorker')) {
    $created = $false
    $mutex = [Threading.Mutex]::new($true,$name,[ref]$created)
    if (-not $created) { $mutex.Dispose(); throw 'existing_or_competing_writer_refused' }
    $script:QuietLocks += $mutex
  }
  Assert-Processes
}
function Restore-Manual {
  Owned-Service -AllowAutomatic | Out-Null
  Set-Service -Name $Service -StartupType Manual
  $restored = Owned-Service
  Assert-True ($restored.StartMode -eq 'Manual') 'MANUAL_RESTORATION_FAILED_USE_DOCUMENTED_RECOVERY'
}
function Enable-Reboot {
  Assert-True ($RebootApproval -ceq ('APPROVED OPTIONAL REBOOT ' + $RunId)) 'separate_explicit_reboot_approval_required'
  Read-Receipt | Out-Null
  Assert-Worker
  $state = (Native $Py @('-I','-B',$HostPath,'snapshot')) | ConvertFrom-Json
  Json-Write @{boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; pid=(Owned-Service).ProcessId; state=$state} (Join-Path $Root 'evidence\reboot-before.json')
  try {
    Set-Service -Name $Service -StartupType Automatic
    Assert-True ((Owned-Service -AllowAutomatic).StartMode -eq 'Auto') 'automatic_start_not_verified'
  } catch { Restore-Manual; throw }
  'NO_REBOOT_ISSUED_OPERATOR_APPROVAL_REQUIRED'
}
function Verify-Reboot {
  # Restore even if receipt, process, mutex or durable-state verification throws.
  Owned-Service -AllowAutomatic | Out-Null
  try {
    Read-Receipt | Out-Null
    Restore-Manual
    Assert-Worker
    $before = Get-Content -LiteralPath (Join-Path $Root 'evidence\reboot-before.json') -Raw | ConvertFrom-Json
    $after = (Native $Py @('-I','-B',$HostPath,'snapshot')) | ConvertFrom-Json
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    $serviceNow = Owned-Service
    $processNow = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $serviceNow.ProcessId)
    $cycle = Get-Content -LiteralPath (Join-Path $Root 'evidence\cycle.json') -Raw | ConvertFrom-Json
    Assert-True ($boot -gt [DateTime]$before.boot -and $processNow.CreationDate -ge $boot -and
      $cycle.pid -eq $serviceNow.ProcessId -and $cycle.time -gt ([DateTimeOffset]$boot).ToUnixTimeSeconds() -and
      $cycle.posts -eq 0 -and $cycle.recovery_enabled -eq $false) 'fresh_postboot_cycle_required'
    Assert-True (($before.state | ConvertTo-Json -Compress -Depth 10) -ceq
      ($after | ConvertTo-Json -Compress -Depth 10)) 'reboot_state_changed'
    Json-Write @{boot=$boot;pid=$serviceNow.ProcessId;state=$after;cycle=$cycle} (Join-Path $Root 'evidence\reboot-after.json')
  } finally { Restore-Manual }
}
function Read-Receipt {
  Trusted-Object $Root $false
  Trusted-Object (Join-Path $Root 'receipt.json') $false
  $r = Get-Content -LiteralPath (Join-Path $Root 'receipt.json') -Raw | ConvertFrom-Json
  Assert-True ($r.schema -ceq 'fpf-phase2-scm-v1' -and $r.root -ceq $Root -and
    $r.candidate -ceq $Candidate -and $r.service -ceq $Service -and $r.command -ceq $Command) 'unrecognized_receipt'
  foreach ($p in $r.source_sha256.PSObject.Properties) {
    Assert-True ($p.Name -notmatch '(^[\\/]|:|(^|[\\/])\.\.([\\/]|$))') 'manifest_path_escape'
    Assert-True ((Hash (Join-Path $Root $p.Name)) -ceq $p.Value) 'source_hash_mismatch'
  }
  if ($r.prepared) {
    Trusted-Object (Join-Path $Root 'code\local_config.py') $false
    Assert-True ((Hash (Join-Path $Root 'code\local_config.py')) -ceq $r.config_sha256) 'synthetic_configuration_changed'
  }
  return $r
}
function Set-PrivateAcl([string]$Path,[bool]$PublicRead=$false) {
  # Called only for newly created, fixed acceptance directories/files.
  Assert-True ($Path -eq $Root -or $Path.StartsWith($Root + '\') -or
    $Path -eq $Evidence -or $Path.StartsWith($Evidence + '\')) 'ACL_target_outside_disposable_environment'
  $item = No-Reparse $Path
  $acl = if ($item.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
  $acl.SetAccessRuleProtection($true,$false)
  $acl.SetOwner([Security.Principal.SecurityIdentifier]::new($AdminSid))
  $inherit = if ($item.PSIsContainer) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
  foreach ($sid in @($SystemSid,$AdminSid)) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
      [Security.Principal.SecurityIdentifier]::new($sid),'FullControl',$inherit,'None','Allow'))
  }
  if ($PublicRead) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
      [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'ReadAndExecute',$inherit,'None','Allow'))
  }
  Set-Acl -LiteralPath $Path -AclObject $acl
}
function Owner-Only([string]$Path) {
  $acl = Get-Acl -LiteralPath $Path
  $acl.SetOwner([Security.Principal.SecurityIdentifier]::new($AdminSid))
  Set-Acl -LiteralPath $Path -AclObject $acl
}
function Inventory {
  $items = @(Tree $Root | ForEach-Object {
    if ($_.FullName -ne $Root) { $_.FullName.Substring($Root.Length + 1) }
  })
  Json-Write @($items + 'inventory.json' | Sort-Object -Unique) (Join-Path $Root 'inventory.json')
}
function Approved-Wheels {
  No-Reparse $Wheelhouse | Out-Null
  $manifest = Join-Path $Wheelhouse 'SHA256SUMS.txt'
  No-Reparse $manifest | Out-Null
  Assert-True ($WheelhouseManifestSha256 -cmatch '^[0-9a-f]{64}$' -and
    (Hash $manifest) -ceq $WheelhouseManifestSha256) 'independently_approved_wheel_manifest_required'
  $wheels = @{}
  foreach ($line in Get-Content -LiteralPath $manifest) {
    Assert-True ($line -cmatch '^([0-9a-f]{64})  ([A-Za-z0-9_.+-]+\.whl)$') 'invalid_wheel_manifest'
    $name=$Matches[2]; $expected=$Matches[1]
    Assert-True (-not $wheels.ContainsKey($name)) 'duplicate_wheel'
    No-Reparse (Join-Path $Wheelhouse $name) | Out-Null
    Assert-True ((Hash (Join-Path $Wheelhouse $name)) -ceq $expected) 'unapproved_wheel'
    $wheels[$name]=$expected
  }
  Assert-True ($wheels.Count -gt 0 -and
    @(Get-ChildItem -LiteralPath $Wheelhouse -File -Filter '*.whl').Count -eq $wheels.Count -and
    (Hash $manifest) -ceq $WheelhouseManifestSha256) 'wheel_manifest_changed_or_extra_wheels'
  return $wheels
}
function Preflight {
  Assert-True ($env:COMPUTERNAME -ieq 'ZUHAIR-PC') 'ZUHAIR_PC_only'
  Trusted-Object $GitExe $false
  $parent = Get-Item -LiteralPath (Split-Path -Parent $GitExe)
  while ($null -ne $parent) { Trusted-Object $parent.FullName $true; $parent=$parent.Parent }
  Assert-True ((Native $GitExe @('-C',$Repo,'branch','--show-current')).Trim() -ceq 'recovery') 'recovery_branch_required'
  Native $GitExe @('-C',$Repo,'merge-base','--is-ancestor',$Candidate,'HEAD') | Out-Null
  foreach ($changed in @(Native $GitExe @('-C',$Repo,'diff','--name-only',$Candidate,'HEAD'))) {
    Assert-True ($changed.StartsWith('acceptance/windows_scm/')) 'application_candidate_changed'
  }
  Assert-True (-not (Test-Path -LiteralPath $Root) -and -not (Test-Path -LiteralPath $Evidence)) 'fresh_disposable_paths_required'
  Assert-True ($null -eq (Get-TestService)) 'temporary_service_name_already_exists'
  Assert-True ($MonitorSid -cmatch '^S-1-5-21-([1-9][0-9]*)-([1-9][0-9]*)-([1-9][0-9]*)-([1-9][0-9]*)$') 'canonical_user_SID_required'
  Assert-True ([uint64]($MonitorSid.Split('-')[-1]) -ge 1000) 'ordinary_user_RID_required'
  $sid = [Security.Principal.SecurityIdentifier]::new($MonitorSid)
  Assert-True ($sid.Value -ceq $MonitorSid -and
    $sid.Translate([Security.Principal.NTAccount]).Value -ieq $MonitorAccount) 'independently_approved_account_SID_mismatch'
  $user = @(Get-CimInstance Win32_UserAccount -Filter ("SID='" + $MonitorSid + "'"))
  Assert-True ($user.Count -eq 1 -and $user[0].SIDType -eq 1 -and -not $user[0].Disabled) 'enabled_user_account_required'
  $runtime = Safe-Runtime $PythonExe
  Trusted-Object $OpenSsl $false
  $item = Get-Item -LiteralPath (Split-Path -Parent $OpenSsl)
  while ($null -ne $item) { Trusted-Object $item.FullName $true; $item = $item.Parent }
  Assert-True ((Get-Volume -DriveLetter C).FileSystem -eq 'NTFS') 'local_NTFS_required'
  Assert-True (@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -eq 18879).Count -eq 0) 'loopback_port_in_use'
  Assert-True (Test-Path -LiteralPath $Wheelhouse -PathType Container) 'offline_frozen_wheelhouse_required'
  Assert-Processes
  Approved-Wheels | Out-Null
  Native $PythonExe @('-I','-B',$HostSource,'preflight')
  [pscustomobject]@{ Stage='READ_ONLY_PREFLIGHT_PASS'; Candidate=$Candidate; Root=$Root; Evidence=$Evidence;
    Service=$Service; Python=$runtime.exe; PythonSha256=(Hash $PythonExe); OpenSslSha256=(Hash $OpenSsl);
    HostSha256=(Hash $HostSource); DriverSha256=(Hash $PSCommandPath); Destination='https://127.0.0.1:18879';
    Credentials='SYNTHETIC_SCM_ONLY'; ProductionTargets='NO_PARAMETERS_OR_FALLBACKS';
    Remaining='Actual LocalSystem/S4U/monitor-token/SCM acceptance required' }
}
function Wait-Cycle([int]$PreviousPid=0) {
  $until = [DateTime]::UtcNow.AddSeconds(90)
  while ([DateTime]::UtcNow -lt $until) {
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Root 'evidence\failure.json'))) 'service_failure_evidence'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Root 'evidence\receiver-failure.json'))) 'receiver_failure_evidence'
    $path = Join-Path $Root 'evidence\cycle.json'
    if (Test-Path -LiteralPath $path) {
      $value = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
      if ($value.pid -ne $PreviousPid) {
        $svc = Owned-Service
        Assert-True ($svc.State -eq 'Running' -and $svc.ProcessId -eq $value.pid -and
          $value.sid -ceq $SystemSid -and $value.executable -ieq $Py -and
          $value.config -ieq (Join-Path $Root 'code\local_config.py') -and
          $value.recovery_enabled -eq $false -and $value.twelve_polled -eq $true) 'SCM_execution_receipt_mismatch'
        Assert-Worker
        return $value
      }
    }
    Start-Sleep -Milliseconds 250
  }
  throw 'cycle_receipt_timeout'
}
function Stop-Owned {
  $svc = Owned-Service
  if ($svc.State -ne 'Stopped') {
    Stop-Service -Name $Service
    (Get-Service -Name $Service).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(45))
  }
}
function Prepare {
  $approvedHostHash = Hash $HostSource
  $approvedDriverHash = Hash $PSCommandPath
  $approvedSafetyHash = Hash (Join-Path $PSScriptRoot 'harness_safety.py')
  Preflight
  Require-Admin
  New-Item -ItemType Directory -Path $Root | Out-Null
  Set-PrivateAcl $Root $true
  $receipt = [ordered]@{ schema='fpf-phase2-scm-v1'; candidate=$Candidate; service=$Service;
    root=$Root; command=$Command; python_base=$PythonExe; monitor_sid=$MonitorSid; wheel_manifest_sha256=$WheelhouseManifestSha256; source_sha256=@{}; config_sha256=$null; prepared=$false }
  Json-Write $receipt (Join-Path $Root 'receipt.json')
  Set-PrivateAcl (Join-Path $Root 'receipt.json') $true
  try {
    foreach ($name in @('code','runtime','tools','data','evidence','wheels')) {
      $path = Join-Path $Root $name
      New-Item -ItemType Directory -Path $path | Out-Null
      Set-PrivateAcl $path ($name -in @('code','runtime','tools'))
    }
    Copy-Item -LiteralPath $HostSource -Destination $HostPath
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'harness_safety.py') -Destination (Join-Path $Root 'tools\harness_safety.py')
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $Root 'tools\Invoke-Acceptance.ps1')
    Assert-True ((Hash $HostPath) -ceq $approvedHostHash -and
      (Hash (Join-Path $Root 'tools\Invoke-Acceptance.ps1')) -ceq $approvedDriverHash -and
      (Hash (Join-Path $Root 'tools\harness_safety.py')) -ceq $approvedSafetyHash) 'reviewed_harness_changed_during_copy'
    Native $GitExe @('-C',$Repo,'archive','--format=zip','--output='+(Join-Path $Root 'candidate.zip'),$Candidate)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead((Join-Path $Root 'candidate.zip'))
    try {
      foreach ($entry in $zip.Entries) {
        Assert-True ($entry.FullName -notmatch '(^[\\/]|:|(^|[\\/])\.\.([\\/]|$))') 'archive_path_escape'
      }
    } finally { $zip.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $Root 'candidate.zip'),(Join-Path $Root 'code'))
    Native $PythonExe @('-I','-B','-m','venv','--copies',(Join-Path $Root 'runtime'))
    $approvedWheels = Approved-Wheels
    $wheelHashes = @{}
    foreach ($file in Get-ChildItem -LiteralPath $Wheelhouse -File -Filter '*.whl') {
      No-Reparse $file.FullName | Out-Null
      $expected = $approvedWheels[$file.Name]
      Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $Root 'wheels')
      Assert-True ((Hash (Join-Path $Root ('wheels\\' + $file.Name))) -ceq $expected) 'wheel_copy_changed'
      $wheelHashes[$file.Name] = $expected
    }
    Json-Write $wheelHashes (Join-Path $Root 'evidence\\wheels-sha256.json')
    Native $Py @('-I','-B','-m','pip','--isolated','install','--no-index','--only-binary=:all:','--find-links',(Join-Path $Root 'wheels'),
      '-r',(Join-Path $Root 'code\docs\requirements-windows311-accepted.txt')) |
      Out-File -LiteralPath (Join-Path $Root 'evidence\install.txt') -Encoding utf8
    Native $Py @('-I','-B','-m','pip','check') |
      Out-File -LiteralPath (Join-Path $Root 'evidence\pip-check.txt') -Encoding utf8
    Native $Py @('-I','-B','-c',
      'import importlib.metadata as m,pathlib; p=pathlib.Path(r"C:\FPFPhase2AclAcceptance-A4F20179C01\code\docs\requirements-windows311-accepted.txt"); pairs=[s.split("==") for s in p.read_text().splitlines()]; assert all(m.version(n)==v for n,v in pairs); print("ALL_FROZEN_PINS_MATCH")') |
      Out-File -LiteralPath (Join-Path $Root 'evidence\dependencies.txt') -Encoding utf8
    Native $OpenSsl @('req','-x509','-newkey','rsa:2048','-sha256','-nodes','-days','3',
      '-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1',
      '-keyout',(Join-Path $Root 'tools\localhost-key.pem'),'-out',(Join-Path $Root 'tools\localhost-cert.pem')) | Out-Null
    Assert-Processes
    Native $Py @('-I','-B',$HostPath,'configure','--sid',$MonitorSid) | Out-Null
    Assert-Processes
    # Preserve inherited SYSTEM/Admin FullControl on the ledger; add only explicit R.
    foreach ($item in Tree $Root) { Owner-Only $item.FullName }
    Set-PrivateAcl (Join-Path $Root 'code\local_config.py')
    Set-PrivateAcl (Join-Path $Root 'tools\localhost-key.pem')
    $acl = Get-Acl -LiteralPath (Join-Path $Root 'data')
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
      [Security.Principal.SecurityIdentifier]::new($MonitorSid),'ReadAndExecute','None','None','Allow'))
    Set-Acl -LiteralPath (Join-Path $Root 'data') -AclObject $acl
    $acl = Get-Acl -LiteralPath (Join-Path $Root 'data\synthetic.sqlite3')
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
      [Security.Principal.SecurityIdentifier]::new($MonitorSid),'Read','None','None','Allow'))
    Set-Acl -LiteralPath (Join-Path $Root 'data\synthetic.sqlite3') -AclObject $acl
    Tree $Root | Out-Null
    $hashes = @{}
    foreach ($base in @('code','tools')) {
      foreach ($file in Get-ChildItem -LiteralPath (Join-Path $Root $base) -Recurse -File) {
        if ($file.Name -notin @('local_config.py','localhost-key.pem')) {
          $hashes[$file.FullName.Substring($Root.Length+1)] = Hash $file.FullName
        }
      }
    }
    $receipt.source_sha256=$hashes
    $receipt.config_sha256 = Hash (Join-Path $Root 'code\local_config.py')
    $receipt.prepared=$true
    Json-Write $receipt (Join-Path $Root 'receipt.json')
    'PREPARED_ONLY_NO_SERVICE_REGISTERED'
  } finally { Inventory }
}
function Register {
  Require-Admin
  $r = Read-Receipt
  Assert-True ($r.prepared -eq $true -and $null -eq (Get-TestService)) 'prepared_new_service_required'
  Safe-Runtime $r.python_base | Out-Null
  Assert-Processes
  Native $Py @('-I','-B',$HostPath,'preflight') | Out-Null
  Assert-True (@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -eq 18879).Count -eq 0) 'port_in_use'
  New-Service -Name $Service -BinaryPathName $Command -DisplayName ('Disposable Phase 2 ACL acceptance ' + $RunId)  -Description ('Synthetic acceptance only ' + $Candidate) -StartupType Manual | Out-Null
  Assert-Processes
  Native $Py @('-I','-B',$HostPath,'preflight') | Out-Null
  Owned-Service | Select-Object Name,PathName,StartName,State,StartMode,ProcessId
}
function Run {
  Require-Admin
  Read-Receipt | Out-Null
  $svc = Owned-Service
  Assert-True ($svc.State -eq 'Stopped') 'stopped_owned_service_required'
  Assert-Processes
  Native $Py @('-I','-B',$HostPath,'preflight') | Out-Null
  try {
    Start-Service -Name $Service
    $first = Wait-Cycle
    Assert-True ($first.posts -eq 12) 'first_cycle_must_post_exactly_12_synthetic_records'
    Native $Py @('-I','-B',$HostPath,'contender','--pid',[string]$first.pid) |
      Out-File -LiteralPath (Join-Path $Root 'evidence\mutex.txt') -Encoding utf8
    # Stop during the next bounded writer window to exercise in-flight shutdown.
    $previous = (Get-Content -LiteralPath (Join-Path $Root 'evidence\writer-window.json') -Raw | ConvertFrom-Json).start
    $until = [DateTime]::UtcNow.AddSeconds(35)
    do {
      Start-Sleep -Milliseconds 100
      $window = Get-Content -LiteralPath (Join-Path $Root 'evidence\writer-window.json') -Raw | ConvertFrom-Json
    } while ($window.start -eq $previous -and [DateTime]::UtcNow -lt $until)
    Assert-True ($window.start -ne $previous) 'inflight_writer_window_missing'
    Assert-Worker
    Stop-Owned
    Assert-Processes
    Native $Py @('-I','-B',$HostPath,'snapshot') |
      Out-File -LiteralPath (Join-Path $Root 'evidence\stopped-snapshot.json') -Encoding utf8
    Assert-Processes
    Native $Py @('-I','-B',$HostPath,'preflight') | Out-Null
    Start-Service -Name $Service
    $second = Wait-Cycle $first.pid
    Assert-True ($second.posts -eq 0) 'restart_must_not_replay_synced_punches'
    foreach ($field in @('total','states','holds','checkpoints','activation','journal','recovery_tables')) {
      Assert-True (($first.$field | ConvertTo-Json -Compress -Depth 10) -ceq
        ($second.$field | ConvertTo-Json -Compress -Depth 10)) ('state_not_preserved: ' + $field)
    }
    Assert-Worker
    Json-Write @{status='PASS';first=$first;restarted=$second;inflight_stop=$true} (Join-Path $Root 'evidence\run.json')
    'RUN_PASS_SERVICE_LEFT_RUNNING_FOR_ACTUAL_MONITOR_PROBE'
  } catch {
    Stop-Owned
    throw
  }
}
function Evidence {
  Require-Admin
  $r = Read-Receipt
  $svc = Get-TestService
  if ($null -ne $svc) { $svc = Owned-Service }
  $report = $null
  if ($MonitorResultJson) {
    $report = $MonitorResultJson | ConvertFrom-Json
    Assert-True ($report.schema -ceq 'fpf-monitor-result-v1' -and $report.run -ceq $RunId -and
      $report.candidate -ceq $Candidate -and $report.status -ceq 'PASS' -and
      $report.read_only -eq $true -and $report.config_denied -eq $true -and $report.activation_denied -eq $true) 'monitor_report_invalid'
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $sidHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($r.monitor_sid))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    Assert-True ($report.sid_sha256 -ceq $sidHash) 'actual_monitor_SID_mismatch'
  }
  if ($null -ne $svc) {
    if ($svc.State -eq 'Running') { Assert-Worker }
    Stop-Owned
  }
  Enter-Quiet
  Assert-True (-not (Test-Path -LiteralPath $Evidence)) 'existing_evidence_refused'
  $staging = Join-Path $Root 'evidence-staging'
  if (-not (Test-Path -LiteralPath $staging)) {
    New-Item -ItemType Directory -Path $staging | Out-Null
    Set-PrivateAcl $staging
  }
  foreach ($item in Tree $Root) { if ($item.FullName.StartsWith($staging)) { Trusted-Object $item.FullName $false } }
  $attemptId = (Native $Py @('-I','-B',$HostPath,'begin-evidence')).Trim()
  Assert-True ($attemptId -cmatch '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') 'invalid_attempt_id'
  $attempt = Join-Path $staging $attemptId
  $target = Join-Path $attempt 'payload'
  Set-PrivateAcl $attempt
  Set-PrivateAcl $target
  Set-PrivateAcl (Join-Path $attempt 'attempt.json')
  Tree $Root | Out-Null

  Copy-Item -LiteralPath (Join-Path $Root 'receipt.json') -Destination $target
  if (Test-Path -LiteralPath (Join-Path $Root 'evidence')) { Copy-Item -LiteralPath (Join-Path $Root 'evidence') -Destination $target -Recurse }
  if ($null -ne $svc) {
    $svc | Select-Object Name,PathName,StartName,State,StartMode,ProcessId | ConvertTo-Json |
      Out-File -LiteralPath (Join-Path $target 'scm-running-registration.json') -Encoding utf8
    Native $ScExe @('qc',$Service) | Out-File -LiteralPath (Join-Path $target 'scm-qc.txt') -Encoding utf8
    Native $ScExe @('queryex',$Service) | Out-File -LiteralPath (Join-Path $target 'scm-stopped.txt') -Encoding utf8
  }
  if ((Test-Path -LiteralPath (Join-Path $Root 'data\\synthetic.sqlite3')) -and $r.prepared) {
    Native $Py @('-I','-B',$HostPath,'backup','--attempt',$attemptId) | Out-Null
    Copy-Item -LiteralPath (Join-Path $Root 'data\\synthetic.sqlite3.activation.json') -Destination $target
  }
  $acls = foreach ($path in @($Root,(Join-Path $Root 'data'),(Join-Path $Root 'data\synthetic.sqlite3'),
      (Join-Path $Root 'data\synthetic.sqlite3.activation.json'),(Join-Path $Root 'code\local_config.py'),
      (Join-Path $Root 'code'),$Py,$r.python_base,'C:\')) {
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $acl = Get-Acl -LiteralPath $path
    [pscustomobject]@{Path=$path;Sddl=$acl.Sddl;Owner=$acl.Owner;Protected=$acl.AreAccessRulesProtected}
  }
  Json-Write @($acls) (Join-Path $target 'ACLs.json')
  $overlap = $false
  if ($null -ne $report) {
    Json-Write $report (Join-Path $target 'actual-monitor.json')
    foreach ($line in Get-Content -LiteralPath (Join-Path $Root 'evidence\windows.jsonl')) {
      $w = $line | ConvertFrom-Json
      if (@($report.samples | Where-Object { $_ -gt ($w.start + .25) -and $_ -lt ($w.end - .25) }).Count -ge 3) { $overlap=$true }
    }
  }
  $failed = (Test-Path -LiteralPath (Join-Path $Root 'evidence\failure.json')) -or
    (Test-Path -LiteralPath (Join-Path $Root 'evidence\receiver-failure.json'))
  $runPassed = $false
  if (Test-Path -LiteralPath (Join-Path $Root 'evidence\run.json')) {
    $runPassed = (Get-Content -LiteralPath (Join-Path $Root 'evidence\run.json') -Raw | ConvertFrom-Json).status -ceq 'PASS'
  }
  Json-Write @{candidate=$Candidate;run=$RunId;actual_monitor_tested=($null -ne $report);
    concurrent_actual_monitor_read=$overlap;service_stopped=$true;reboot='NOT TESTED';
    result=$(if ($runPassed -and $overlap -and -not $failed) {'PASS_EXCEPT_REBOOT'} else {'NO_GO_INCOMPLETE_OR_FAILED'})} (Join-Path $target 'summary.json')
  Assert-Processes
  Native $Py @('-I','-B',$HostPath,'seal-evidence','--attempt',$attemptId) | Out-Null
  Native $Py @('-I','-B',$HostPath,'verify-evidence') | Out-Null
  'EVIDENCE_RETAINED_PRIVATELY: ' + $Evidence
}
function Cleanup {
  Require-Admin
  $r = Read-Receipt
  Assert-True (Test-Path -LiteralPath (Join-Path $Evidence 'sha256.json')) 'preserved_evidence_required_before_cleanup'
  $saved = Get-Content -LiteralPath (Join-Path $Evidence 'receipt.json') -Raw | ConvertFrom-Json
  Assert-True ($saved.candidate -ceq $Candidate -and $saved.root -ceq $Root) 'evidence_identity_mismatch'
  Trusted-Object $Evidence $false
  foreach ($file in Checked-Tree $Evidence) { Trusted-Object $file.FullName $false }
  Native $Py @('-I','-B',$HostPath,'verify-evidence') | Out-Null
  $svc = Get-TestService
  if ($null -ne $svc) {
    Owned-Service | Out-Null
    Stop-Owned
  }
  Enter-Quiet
  $inventory = @(Get-Content -LiteralPath (Join-Path $Root 'inventory.json') -Raw | ConvertFrom-Json)
  $generated = @('evidence\cycle.json','evidence\writer-window.json','evidence\windows.jsonl','evidence\failure.json',
    'evidence\receiver-failure.json','evidence\mutex.txt','evidence\stopped-snapshot.json','evidence\run.json',
    'evidence\cycle.part','evidence\writer-window.part','evidence\failure.part',
    'evidence\reboot-before.json','evidence\reboot-after.json')
  $tree = @(Tree $Root)
  foreach ($item in $tree) {
    if ($item.FullName -ne $Root) {
      $relative = $item.FullName.Substring($Root.Length+1)
      $staged = $relative -eq 'evidence-staging' -or $relative.StartsWith('evidence-staging\')
      if ($staged) { Trusted-Object $item.FullName $false }
      Assert-True ($inventory -contains $relative -or $generated -contains $relative -or $staged) ('unrecognized_cleanup_entry: ' + $relative)
    }
  }
  Assert-True (([IO.Path]::GetFullPath($Root)) -ceq 'C:\FPFPhase2AclAcceptance-A4F20179C01') 'cleanup_root_mismatch'
  if ($null -ne $svc) {
    Native $ScExe @('delete',$Service) | Out-Null
    $until = [DateTime]::UtcNow.AddSeconds(20)
    while ($null -ne (Get-TestService) -and [DateTime]::UtcNow -lt $until) { Start-Sleep -Milliseconds 250 }
    Assert-True ($null -eq (Get-TestService)) 'service_deletion_pending_close_service_management_handles'
  }
  Native $Py @('-I','-B',$HostPath,'verify-evidence') | Out-Null
  Assert-Processes
  Remove-Item -LiteralPath $Root -Recurse -Force
  Assert-True (-not (Test-Path -LiteralPath $Root) -and $null -eq (Get-TestService)) 'cleanup_incomplete'
  Assert-True (@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -eq 18879).Count -eq 0) 'test_listener_remains'
  'CLEANUP_PASS_SERVICE_AND_TEST_ROOT_ABSENT_EVIDENCE_PRESERVED'
}
Assert-True ($env:COMPUTERNAME -ieq 'ZUHAIR-PC') 'ZUHAIR_PC_only'
$operator = $null
try {
  if ($Stage -ne 'Preflight') {
    Require-Admin
    $created = $false
    $operator = [Threading.Mutex]::new($true,'Global\FPFPhase2AclAcceptance_A4F20179C01-Operator',[ref]$created)
    if (-not $created) { $operator.Dispose(); $operator=$null; throw 'another_acceptance_operator_active' }
  }
  switch ($Stage) {
    'Preflight' { Preflight }
    'Prepare' { Prepare }
    'Register' { Register }
    'Run' { Run }
    'Evidence' { Evidence }
    'Cleanup' { Cleanup }
    'EnableReboot' { Enable-Reboot }
    'VerifyReboot' { Verify-Reboot }
    'RestoreManual' { Read-Receipt | Out-Null; Restore-Manual }
  }
} finally {
  for ($index=$script:QuietLocks.Count-1; $index -ge 0; $index--) {
    $mutex=$script:QuietLocks[$index]; $mutex.ReleaseMutex(); $mutex.Dispose()
  }
  if ($null -ne $operator) { $operator.ReleaseMutex(); $operator.Dispose() }
}
