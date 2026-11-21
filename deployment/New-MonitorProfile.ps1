# Ordinary-user operation. Existing profiles and ledger ACLs are never edited.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$Ledger,
      [string]$Directory=(Join-Path $env:LOCALAPPDATA 'FPFBiometricSyncManager'),
      [string]$DeviceConfig)
$ErrorActionPreference='Stop'
if(-not [IO.Path]::IsPathRooted($Ledger) -or $Ledger.StartsWith('\\') -or
   -not [IO.Path]::IsPathRooted($Directory) -or $Directory.StartsWith('\\') -or
   ($DeviceConfig -and (-not [IO.Path]::IsPathRooted($DeviceConfig) -or $DeviceConfig.StartsWith('\\')))){
    throw 'Absolute local paths required'
}
if(Test-Path -LiteralPath $Directory){throw 'Profile directory exists; preserve and review it'}
$parent=Get-Item -LiteralPath (Split-Path -Parent $Directory)
while($parent){
    if($parent.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse parent refused'}
    $parent=$parent.Parent
}
[void][IO.Directory]::CreateDirectory($Directory)
$acl=[Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true,$false)
$current=[Security.Principal.WindowsIdentity]::GetCurrent().User
$acl.SetOwner($current)
foreach($sid in @($current.Value,'S-1-5-18','S-1-5-32-544')|Select-Object -Unique){
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
}
Set-Acl -LiteralPath $Directory -AclObject $acl
$path=Join-Path $Directory 'web-profile.json'
$values=@{monitor_ledger=$Ledger;diagnostics_directory=$Directory}
# Optional literal-only device configuration for the Devices page and Shift sync panel.
if($DeviceConfig){$values.device_config=$DeviceConfig}
[IO.File]::WriteAllText($path,($values|ConvertTo-Json),
    [Text.UTF8Encoding]::new($false))
Write-Output $path
