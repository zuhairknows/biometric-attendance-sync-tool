# Read-only staging inspection. Optional report is a NEW workspace file only.
param([Parameter(Mandatory=$true)][string]$StageRoot,[string]$Report)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator read access required; no ACL changes are performed.' }
$stage=(Get-Item -LiteralPath $StageRoot -Force).FullName
if ((Split-Path -Parent $stage) -ne [Environment]::GetFolderPath('LocalApplicationData') -or (Split-Path -Leaf $stage) -notmatch '^FPF-Phase1-Staging-[0-9a-f]{32}$') { throw 'Unexpected staging root.' }
$cursor=Get-Item -LiteralPath $stage -Force
while ($null -ne $cursor) {
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
    $cursor=$cursor.Parent
}
$code=Join-Path $stage 'service-boundary\code'
$runtime=Join-Path $code 'runtime'
$service=Get-CimInstance Win32_Service -Filter "Name='FPFPhase1Staging'"
$serviceSafe=($null -ne $service -and $service.State -eq 'Stopped' -and $service.StartMode -eq 'Manual' -and
    $service.StartName -eq 'NT SERVICE\FPFPhase1Staging' -and $service.PathName -eq ('"'+(Join-Path $code 'FPFPhase1Staging.exe')+'"') -and
    $service.ProcessId -eq 0 -and $service.ExitCode -eq 0 -and $service.ServiceSpecificExitCode -eq 0)
$provenance=Get-Content -LiteralPath (Join-Path $code 'probe-provenance.json') -Raw | ConvertFrom-Json
if ($provenance.service_sid -notmatch '^S-1-5-80-(?:[0-9]+-){4}[0-9]+$') { throw 'Invalid retained SID.' }
$probeMatches=((Get-FileHash -LiteralPath (Join-Path $code 'FPFPhase1Staging.exe')).Hash -eq $provenance.executable_sha256)
$allowed=@($identity.User.Value,'S-1-5-18','S-1-5-32-544',$provenance.service_sid)
$files=@{}; $private=$true
$pending=[Collections.Generic.Stack[string]]::new(); $pending.Push($runtime)
while ($pending.Count -gt 0) {
    $path=$pending.Pop()
    $item=Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Runtime reparse entry refused.' }
    $acl=Get-Acl -LiteralPath $path
    if ($allowed -notcontains $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value) { $private=$false }
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -eq 'Allow' -and $allowed -notcontains $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value) { $private=$false }
    }
    if ($item.PSIsContainer) {
        foreach ($child in @(Get-ChildItem -LiteralPath $path -Force)) { $pending.Push($child.FullName) }
    } else {
        $relative=$path.Substring($runtime.Length+1)
        $files[$relative]=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant()
    }
}
$manifestPath=Join-Path $runtime 'runtime-manifest.json'
$manifestValid=$false; $missing=0; $mismatches=0; $unexpected=0
$classification=if ($files.Count -eq 0) {'EMPTY_RESERVED_DIRECTORY'} else {'PARTIAL_OR_UNATTESTED'}
if (Test-Path -LiteralPath $manifestPath) {
    $manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $entries=@($manifest.sha256.PSObject.Properties)
    if ($entries.Count -eq 0) { throw 'Empty runtime manifest.' }
    foreach ($entry in $entries) {
        # Manifest paths are compared as inventory keys, never opened directly.
        if ([IO.Path]::IsPathRooted($entry.Name) -or $entry.Name -match '(^|[\\/])\.\.([\\/]|$)' -or $entry.Value -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid runtime manifest entry.' }
        if (-not $files.ContainsKey($entry.Name)) { $missing++ }
        elseif ($files[$entry.Name] -ne $entry.Value) { $mismatches++ }
    }
    $expected=@($entries | ForEach-Object {$_.Name})+@('runtime-manifest.json')
    $unexpected=@($files.Keys | Where-Object {$expected -notcontains $_}).Count
    $manifestValid=($missing -eq 0 -and $mismatches -eq 0 -and $unexpected -eq 0)
    $classification=if ($manifestValid) {'COMPLETE_MANIFEST_HASH_MATCH'} else {'PARTIAL_OR_CHANGED_MANIFEST_COPY'}
}
$running=@(Get-CimInstance Win32_Process -Property ExecutablePath | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($runtime+'\',[StringComparison]::OrdinalIgnoreCase)
}).Count
$result=[ordered]@{service_safe=$serviceSafe;probe_hash_matches=$probeMatches;runtime_classification=$classification;
    runtime_files=$files.Count;manifest_present=(Test-Path -LiteralPath $manifestPath);manifest_hashes_valid=$manifestValid;
    missing_files=$missing;hash_mismatches=$mismatches;unexpected_files=$unexpected;restricted_acl_principals=$private;
    running_old_runtime_processes=$running;safe_for_fresh_runtime=($serviceSafe -and $probeMatches -and $private -and $running -eq 0);
    runtime_inventory_sha256=$files;service_state=$service.State;recorded_at_utc=[DateTime]::UtcNow.ToString('o')}
$json=$result | ConvertTo-Json -Depth 5
if ($Report) {
    $repo=Split-Path -Parent $PSScriptRoot
    if ((Split-Path -Parent ([IO.Path]::GetFullPath($Report))) -ne $repo) { throw 'Report must be a new workspace file.' }
    $stream=[IO.File]::Open($Report,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
    try { $bytes=[Text.Encoding]::UTF8.GetBytes($json);$stream.Write($bytes,0,$bytes.Length) } finally {$stream.Dispose()}
} else { $json }
