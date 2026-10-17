# Narrow staging prerequisite only. Default is read-only; -Apply is explicit.
param([switch]$Apply,[switch]$Resume,[string]$OriginalBackup)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator PowerShell required to inspect protected ancestor ACLs.'
}
$stagingUserProfile=[Environment]::GetFolderPath('UserProfile')
if ($stagingUserProfile -ne 'C:\Users\zuhair-fpf') { throw 'Reviewed ZUHAIR-PC profile required.' }
$stage=Join-Path $stagingUserProfile 'AppData\Local\FPF-Phase1-Staging-3173c90eb55245ce8c96737d68ded019'
$sid='S-1-5-80-3239607251-981885122-1049059141-1040181771-757005368'
$resolved=([Security.Principal.NTAccount]::new('NT SERVICE\FPFPhase1Staging')).Translate([Security.Principal.SecurityIdentifier]).Value
if ($resolved -ne $sid) { throw 'Staging SID mismatch.' }
function New-MetadataOnlyRule {
    # Allow rules add Synchronize automatically. No ListDirectory/ReadData,
    # ReadPermissions, write rights or inheritance are requested.
    [Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($sid),
        [Security.AccessControl.FileSystemRights]::ReadAttributes,
        [Security.AccessControl.InheritanceFlags]::None,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow)
}
function Test-AceSequence($Expected,$Actual) {
    if ($null -eq $Expected -or $null -eq $Actual) { return ($null -eq $Expected -and $null -eq $Actual) }
    if ($Expected.Revision -ne $Actual.Revision -or $Expected.Count -ne $Actual.Count) { return $false }
    for ($index=0;$index -lt $Expected.Count;$index++) {
        $left=[byte[]]::new($Expected[$index].BinaryLength)
        $right=[byte[]]::new($Actual[$index].BinaryLength)
        $Expected[$index].GetBinaryForm($left,0);$Actual[$index].GetBinaryForm($right,0)
        if ([Convert]::ToBase64String($left) -ne [Convert]::ToBase64String($right)) { return $false }
    }
    return $true
}
function Test-ExactAcl([string]$Expected,[string]$Actual) {
    $want=[Security.AccessControl.RawSecurityDescriptor]::new($Expected)
    $have=[Security.AccessControl.RawSecurityDescriptor]::new($Actual)
    if ($want.Owner.Value -ne $have.Owner.Value -or $want.Group.Value -ne $have.Group.Value) { return $false }
    # Windows may ADD D:AI when persisting an unprotected DACL. Do not
    # ignore protection changes, AI removal, or any other control flag.
    $ai=[int][Security.AccessControl.ControlFlags]::DiscretionaryAclAutoInherited
    $difference=[int]$want.ControlFlags -bxor [int]$have.ControlFlags
    if ($difference -ne 0 -and -not ($difference -eq $ai -and ([int]$have.ControlFlags -band $ai))) { return $false }
    return ((Test-AceSequence $want.DiscretionaryAcl $have.DiscretionaryAcl) -and
            (Test-AceSequence $want.SystemAcl $have.SystemAcl))
}
$paths=@($stage,(Join-Path $stagingUserProfile 'AppData\Local'),(Join-Path $stagingUserProfile 'AppData'),$stagingUserProfile)
$original=@();$originalHash=$null
if ($Resume) {
    if (-not $OriginalBackup) { throw 'Resume requires the preserved original ACL backup.' }
    $originalHash=(Get-FileHash -LiteralPath $OriginalBackup).Hash
    $original=[IO.File]::ReadAllText((Get-Item -LiteralPath $OriginalBackup).FullName) | ConvertFrom-Json
    if ($original -isnot [array] -or $original.Count -ne 4) { throw 'Original backup must contain exactly four reviewed directories.' }
    for ($index=0;$index -lt 4;$index++) {
        if ($original[$index].path -ne $paths[$index]) { throw 'Original backup directory mismatch.' }
    }
} elseif ($OriginalBackup) { throw 'OriginalBackup is only valid with Resume.' }
$plan=@()
# These are exactly the four inaccessible ancestors. Do not recurse or grant
# anything on code/data, other staging files, certificates or private keys.
for ($index=0;$index -lt $paths.Count;$index++) {
    $path=$paths[$index]
    $item=Get-Item -LiteralPath $path -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unexpected ancestor type.' }
    $acl=Get-Acl -LiteralPath $path
    if ($Resume) {
        # Derive the permitted result from the ORIGINAL descriptor and exact
        # metadata rule; never trust a backup's after_sddl to authorize rights.
        $expected=[Security.AccessControl.DirectorySecurity]::new()
        $expected.SetSecurityDescriptorSddlForm($original[$index].before_sddl)
        $originalRules=$expected.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])
        if (@($originalRules | Where-Object {$_.IdentityReference.Value -eq $sid}).Count) { throw 'Original backup already contains staging rights.' }
        $expected.AddAccessRule((New-MetadataOnlyRule))
        if (-not (Test-ExactAcl $expected.Sddl $original[$index].after_sddl)) { throw 'Original backup planned permissions mismatch.' }
        if (Test-ExactAcl $expected.Sddl $acl.Sddl) {
            Write-Output ("VERIFIED ONLY: {0}; no ACL write" -f $path)
            continue
        }
        if ($index -lt 2 -or -not (Test-ExactAcl $original[$index].before_sddl $acl.Sddl)) {
            throw 'Unexpected resume ACL; no changes made.'
        }
    }
    $rules=$acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])
    if (@($rules | Where-Object {$_.IdentityReference.Value -eq $sid}).Count) { throw 'Existing staging ACE requires review; no changes made.' }
    $before=$acl.Sddl
    $acl.AddAccessRule((New-MetadataOnlyRule))
    $plan+=@{path=$path;before_sddl=$before;after_sddl=$acl.Sddl;acl=$acl}
}
if ($plan.Count -eq 0) {
    if ($Resume -and (Get-FileHash -LiteralPath $OriginalBackup).Hash -ne $originalHash) { throw 'Original ACL backup changed.' }
    Write-Output 'METADATA CORRECTION ALREADY COMPLETE. No ACL writes or SCM session executed.'
    return
}
if (-not $Apply) {
    $plan | ForEach-Object { Write-Output ("READ-ONLY PLAN: {0}: service SID ReadAttributes/Synchronize, this folder only" -f $_.path) }
    return
}
if ($Resume -and (Get-FileHash -LiteralPath $OriginalBackup).Hash -ne $originalHash) { throw 'Original ACL backup changed before apply.' }
# Preserve the exact old descriptors before the first write. Nothing in the
# original evidence directory is used for operator-owned backup/report files.
$backup=Join-Path (Split-Path -Parent $PSScriptRoot) ('.scm-ancestor-acl-backup-'+[guid]::NewGuid().ToString('N')+'.json')
$value=@($plan | ForEach-Object {@{path=$_.path;before_sddl=$_.before_sddl;after_sddl=$_.after_sddl}}) | ConvertTo-Json -Depth 3
$stream=[IO.File]::Open($backup,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
try {$bytes=[Text.Encoding]::UTF8.GetBytes($value);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)} finally {$stream.Dispose()}
Write-Output "Original ACL evidence: $backup"
foreach ($entry in $plan) {
    if (-not (Test-ExactAcl $entry.before_sddl (Get-Acl -LiteralPath $entry.path).Sddl)) { throw 'ACL changed since review; stop and preserve backup.' }
    Set-Acl -LiteralPath $entry.path -AclObject $entry.acl
    if (-not (Test-ExactAcl $entry.after_sddl (Get-Acl -LiteralPath $entry.path).Sddl)) { throw 'Unexpected ACL result; stop and preserve backup.' }
}
if ($Resume -and (Get-FileHash -LiteralPath $OriginalBackup).Hash -ne $originalHash) { throw 'Original ACL backup changed; preserve evidence.' }
if ($Resume) {
    foreach ($entry in $original) {
        if (-not (Test-ExactAcl $entry.after_sddl (Get-Acl -LiteralPath $entry.path).Sddl)) { throw 'Final resume ACL verification failed.' }
    }
}
Write-Output 'METADATA CORRECTION COMPLETE. No SCM session executed.'
