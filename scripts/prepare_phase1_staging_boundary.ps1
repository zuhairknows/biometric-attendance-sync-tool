# Step 3 prepares a boundary only. It NEVER installs or starts a service.
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$StageRoot, [switch]$Resume)
$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Step 3 requires PowerShell Run as administrator. Nothing has been changed.'
}
$stage = (Get-Item -LiteralPath $StageRoot -ErrorAction Stop).FullName
$expectedParent = [Environment]::GetFolderPath('LocalApplicationData')
if ((Split-Path -Parent $stage) -ne $expectedParent -or (Split-Path -Leaf $stage) -notmatch '^FPF-Phase1-Staging-[0-9a-f]{32}$') {
    throw 'Use the existing Step 2 directory under this same user account.'
}
# Reject junctions anywhere in the path before making administrator-owned files.
$cursor = Get-Item -LiteralPath $stage -Force
while ($null -ne $cursor) {
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
    $cursor = $cursor.Parent
}
$drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($stage))
if ($drive.DriveType -ne 'Fixed' -or $drive.DriveFormat -ne 'NTFS') { throw 'Local NTFS required.' }
$step2 = Join-Path $stage 'https-self-test.json'
if ((Get-Item -LiteralPath $step2 -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse report refused.' }
$report = Get-Content -LiteralPath $step2 -Raw | ConvertFrom-Json
$checks = @($report.checks.PSObject.Properties)
if ($report.schema -ne 'phase1-local-https-preflight-v1' -or $report.status -ne 'PASS' -or
    $report.mock_records -ne 1 -or $checks.Count -ne 13 -or
    @($checks | Where-Object { $_.Value -isnot [bool] -or $_.Value -ne $true }).Count -ne 0 -or
    $report.service_operations -ne $false -or $report.production_access -ne $false) {
    throw 'A complete passing Step 2 report is required.'
}
$repo = Split-Path -Parent $PSScriptRoot
$revision = & git -C $repo rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $revision -ne '4256cd6201b17dcda3f9d078552acf1d3c58b3f0') { throw 'Pinned 4256cd6 checkout required.' }

$serviceName = 'FPFPhase1Staging'
$boundary = Join-Path $stage 'service-boundary'
if ((Test-Path -LiteralPath $boundary) -and -not $Resume) { throw 'Boundary already exists. Use -Resume for validation without overwriting.' }
if ($Resume -and -not (Test-Path -LiteralPath $boundary)) { throw 'Resume requires the existing boundary.' }
# Query only the dedicated staging name, never the production service.
if (@(Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'").Count -ne 0) { throw 'Staging service already exists. Stop.' }
foreach ($name in @('BFE', 'MpsSvc')) {
    if ((Get-Service -Name $name).Status -ne 'Running') { throw 'Required firewall service is not running; no automatic changes allowed.' }
}
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
if ($profiles.Count -ne 3 -or @($profiles | Where-Object {
    $_.Enabled.ToString() -ne 'True' -or $_.AllowLocalFirewallRules.ToString() -eq 'False'
}).Count -ne 0) { throw 'All effective firewall profiles must be enabled and permit local rules. Do not change policy automatically.' }
# SHOWSID calculates an identity without creating a service/account or setting a password.
$sidText = & sc.exe showsid $serviceName
if ($LASTEXITCODE -ne 0) { throw 'Cannot calculate staging service SID.' }
$sidMatches = [regex]::Matches(($sidText -join "`n"), 'S-1-5-80-(?:[0-9]+-){4}[0-9]+')
if ($sidMatches.Count -ne 1) { throw 'Unexpected staging service SID result.' }
$serviceSid = $sidMatches[0].Value
$rulePrefix = 'FPF-Phase1-' + (Split-Path -Leaf $stage).Substring(19)

function New-PrivateStagingDirectory {
    param([string]$Path, [Security.AccessControl.FileSystemRights]$ServiceRights)
    New-Item -ItemType Directory -Path $Path -ErrorAction Stop | Out-Null
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544', $serviceSid)) {
        $rights = if ($sid -eq $serviceSid) { $ServiceRights } else { [Security.AccessControl.FileSystemRights]::FullControl }
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid), $rights,
            [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
            [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
    Test-PrivateStagingDirectory $Path $ServiceRights
}

function Test-PrivateStagingDirectory {
    param([string]$Path, [Security.AccessControl.FileSystemRights]$ServiceRights)
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Unexpected staging directory type or reparse point.'
    }
    $actual = Get-Acl -LiteralPath $Path
    $rules = @($actual.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if (-not $actual.AreAccessRulesProtected -or $rules.Count -ne 3 -or
        $actual.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-32-544') {
        throw 'Staging ACL verification failed.'
    }
    foreach ($rule in $rules) {
        $sid = $rule.IdentityReference.Value
        # Allow ACEs add Synchronize automatically in the .NET constructor.
        $expectedRights = if ($sid -eq $serviceSid) {
            $ServiceRights -bor [Security.AccessControl.FileSystemRights]::Synchronize
        } else { [Security.AccessControl.FileSystemRights]::FullControl }
        if ($sid -notin @('S-1-5-18', 'S-1-5-32-544', $serviceSid) -or $rule.IsInherited -or
            $rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne $expectedRights -or
            $rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' -or
            $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) { throw 'Unexpected staging ACL grant.' }
    }
}

$code = Join-Path $boundary 'code'
$data = Join-Path $boundary 'data'

# Reserve unique executable paths. NEVER scope rules to shared Python or the host.
# Future launch must use exactly these paths, with no unprotected child processes.
$programs = @((Join-Path $code 'FPFPhase1Staging.exe'), (Join-Path $code 'runtime\python.exe'))
$nonLoopback = @('0.0.0.0-126.255.255.255', '128.0.0.0-255.255.255.255',
    '::-::', '::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
$names = @()
for ($index = 0; $index -lt $programs.Count; $index++) {
    foreach ($direction in @('Outbound', 'Inbound')) {
        $names += "$rulePrefix-$index-$direction"
    }
    $names += "$rulePrefix-$index-UDP"
}
function Test-StagingFirewallRule {
    param([object[]]$Rule, [string]$Name, [switch]$Active)
    if ($rule.Count -ne 1 -or $rule[0].Enabled.ToString() -ne 'True' -or $rule[0].Action.ToString() -ne 'Block' -or
        $rule[0].Name -ne $name -or $rule[0].Group -ne $rulePrefix -or $rule[0].DisplayName -ne $name -or
        ($Active -and $rule[0].PrimaryStatus.ToString() -ne 'OK')) { throw 'Unexpected or inactive staging firewall rule.' }
    $expectedProgram = $programs[[int]($name.Substring($rulePrefix.Length + 1, 1))]
    $filter = $rule[0] | Get-NetFirewallApplicationFilter
    if ($filter.Program -ne $expectedProgram) { throw 'Unexpected firewall program scope.' }
    $address = $rule[0] | Get-NetFirewallAddressFilter
    $port = $rule[0] | Get-NetFirewallPortFilter
    $service = $rule[0] | Get-NetFirewallServiceFilter
    $interface = $rule[0] | Get-NetFirewallInterfaceFilter
    $interfaceType = $rule[0] | Get-NetFirewallInterfaceTypeFilter
    $security = $rule[0] | Get-NetFirewallSecurityFilter
    $isUdp = $name.EndsWith('-UDP')
    $expectedAddresses = if ($isUdp) { @('Any') } else { $nonLoopback }
    # Windows canonicalizes the IPv6 singleton range "::-::" to "::".
    # Accept only this equivalent spelling; changed/missing address coverage still fails.
    $actualAddresses = @($address.RemoteAddress | ForEach-Object { if ($_ -eq '::-::') { '::' } else { $_ } })
    $expectedAddresses = @($expectedAddresses | ForEach-Object { if ($_ -eq '::-::') { '::' } else { $_ } })
    $expectedDirection = if ($name.EndsWith('-Inbound')) { 'Inbound' } else { 'Outbound' }
    $expectedProtocols = if ($isUdp) { @('UDP', '17') } else { @('Any', '256') }
    if (@(Compare-Object $actualAddresses $expectedAddresses).Count -ne 0 -or
        @($address.LocalAddress).Count -ne 1 -or $address.LocalAddress -ne 'Any' -or
        $port.LocalPort -ne 'Any' -or $port.RemotePort -ne 'Any' -or $service.Service -ne 'Any' -or
        $interface.InterfaceAlias -ne 'Any' -or $interfaceType.InterfaceType -ne 'Any' -or
        $security.Authentication -ne 'NotRequired' -or $security.Encryption -ne 'NotRequired' -or
        $security.OverrideBlockRules.ToString() -ne 'False' -or $security.LocalUser -ne 'Any' -or
        $security.RemoteUser -ne 'Any' -or $security.RemoteMachine -ne 'Any' -or
        $expectedProtocols -notcontains $port.Protocol.ToString() -or
        $rule[0].Direction.ToString() -ne $expectedDirection -or $rule[0].Profile.ToString() -ne 'Any') {
        throw 'Unexpected effective firewall filter. No service may be started.'
    }
}
function Complete-StagingBoundary {
    # Validate ALL reusable state before the first mutation. Resume never repairs ACLs.
    if ($Resume) {
        Test-PrivateStagingDirectory $boundary ReadAndExecute
        Test-PrivateStagingDirectory $code ReadAndExecute
        Test-PrivateStagingDirectory $data Modify
        foreach ($directory in @($code, $data)) {
            if (@(Get-ChildItem -LiteralPath $directory -Force).Count -ne 0) { throw 'Unexpected files in reserved staging directories.' }
        }
        foreach ($item in @(Get-ChildItem -LiteralPath $boundary -Force)) {
            if ($item.FullName -in @($code, $data)) { continue }
            if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                $item.Name -notmatch '^boundary-preparation(?:-[0-9a-f]{32})?\.json$') { throw 'Unexpected staging artifact.' }
            $fileAcl = Get-Acl -LiteralPath $item.FullName
            $fileRules = @($fileAcl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
            if ($fileRules.Count -ne 3 -or $fileAcl.AreAccessRulesProtected -or
                $fileAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18', 'S-1-5-32-544', $identity.User.Value)) {
                throw 'Unexpected existing report permissions.'
            }
            foreach ($entry in $fileRules) {
                $expected = if ($entry.IdentityReference.Value -eq $serviceSid) {
                    [Security.AccessControl.FileSystemRights]::ReadAndExecute -bor [Security.AccessControl.FileSystemRights]::Synchronize
                } else { [Security.AccessControl.FileSystemRights]::FullControl }
                if (-not $entry.IsInherited -or $entry.AccessControlType -ne 'Allow' -or
                    $entry.IdentityReference.Value -notin @('S-1-5-18', 'S-1-5-32-544', $serviceSid) -or
                    $entry.FileSystemRights -ne $expected) { throw 'Unexpected existing report grant.' }
            }
            $previous = Get-Content -LiteralPath $item.FullName -Raw | ConvertFrom-Json
            if ($previous.status -ne 'PREPARED_NOT_RUNTIME_VERIFIED' -or $previous.service_name -ne $serviceName -or
                $previous.service_sid -ne $serviceSid -or $previous.service_installed -ne $false -or $previous.service_started -ne $false -or
                $previous.step2_report_sha256 -ne (Get-FileHash -LiteralPath $step2 -Algorithm SHA256).Hash -or
                $previous.preparation_script_sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
                $previous.code_directory -ne $code -or $previous.data_directory -ne $data -or
                @($previous.protected_program_paths).Count -ne 2 -or
                @(Compare-Object @($previous.protected_program_paths) $programs).Count -ne 0 -or
                @($previous.firewall_rule_names).Count -ne 6 -or
                @(Compare-Object @($previous.firewall_rule_names) $names).Count -ne 0) { throw 'Existing staging report does not match this boundary.' }
        }
    }
    $persistent = @(Get-NetFirewallRule -PolicyStore PersistentStore | Where-Object { $_.Name -like "$rulePrefix-*" -or $_.Group -eq $rulePrefix })
    $active = @(Get-NetFirewallRule -PolicyStore ActiveStore | Where-Object { $_.Name -like "$rulePrefix-*" -or $_.Group -eq $rulePrefix })
    if (-not $Resume -and ($persistent.Count -ne 0 -or $active.Count -ne 0)) { throw 'Existing rules require a validated boundary and -Resume.' }
    foreach ($rule in @($persistent) + @($active)) {
        if ($rule.Name -notin $names) { throw 'Unexpected staging rule name or group member.' }
    }
    $missing = @()
    foreach ($name in $names) {
        $stored = @($persistent | Where-Object { $_.Name -eq $name })
        $effective = @($active | Where-Object { $_.Name -eq $name })
        if ($stored.Count -eq 0 -and $effective.Count -eq 0) { $missing += $name; continue }
        Test-StagingFirewallRule -Rule $stored -Name $name
        Test-StagingFirewallRule -Rule $effective -Name $name -Active
    }
    if (-not $Resume) {
        New-PrivateStagingDirectory $boundary ReadAndExecute
        New-PrivateStagingDirectory $code ReadAndExecute
        New-PrivateStagingDirectory $data Modify
    }
    foreach ($name in $missing) {
        $index = [int]($name.Substring($rulePrefix.Length + 1, 1))
        $direction = if ($name.EndsWith('-Inbound')) { 'Inbound' } else { 'Outbound' }
        $protocol = if ($name.EndsWith('-UDP')) { 'UDP' } else { 'Any' }
        $addresses = if ($name.EndsWith('-UDP')) { @('Any') } else { $nonLoopback }
        New-NetFirewallRule -PolicyStore PersistentStore -Name $name -DisplayName $name -Group $rulePrefix `
            -Enabled True -Profile Any -Direction $direction -Action Block -Program $programs[$index] `
            -Protocol $protocol -RemoteAddress $addresses | Out-Null
        # A creation failure stops immediately; validated earlier rules are retained.
        Test-StagingFirewallRule -Rule @(Get-NetFirewallRule -PolicyStore PersistentStore -Name $name) -Name $name
        Test-StagingFirewallRule -Rule @(Get-NetFirewallRule -PolicyStore ActiveStore -Name $name) -Name $name -Active
    }
}
Complete-StagingBoundary
$result = [ordered]@{
    status = 'PREPARED_NOT_RUNTIME_VERIFIED'
    service_name = $serviceName
    service_sid = $serviceSid
    service_installed = $false
    service_started = $false
    step2_report_sha256 = (Get-FileHash -LiteralPath $step2 -Algorithm SHA256).Hash
    preparation_script_sha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash
    code_directory = $code
    data_directory = $data
    protected_program_paths = $programs
    firewall_rule_names = $names
    pending = @('actual_service_token_permissions', 'service_process_network_denial', 'localhost_TLS_under_service_token')
}
# New evidence only; CreateNew also refuses accidental overwrite if a path races us.
$reportName = if ($Resume) { 'boundary-preparation-' + [guid]::NewGuid().ToString('N') + '.json' } else { 'boundary-preparation.json' }
$reportPath = Join-Path $boundary $reportName
$stream = [IO.File]::Open($reportPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($result | ConvertTo-Json -Depth 4))
    $stream.Write($bytes, 0, $bytes.Length)
} finally { $stream.Dispose() }
Write-Output 'STEP 3 PREPARED: private service ACLs and six staging-only firewall blocks verified in ActiveStore.'
Write-Output 'No service installed or started. Runtime isolation and token access remain UNVERIFIED. Stop here.'
