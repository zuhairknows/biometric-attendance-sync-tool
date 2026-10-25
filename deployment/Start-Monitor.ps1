# Independent monitoring launcher; never starts a synchronization worker.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ProfilePath)
$ErrorActionPreference='Stop'
if(-not [IO.Path]::IsPathRooted($ProfilePath) -or -not(Test-Path -LiteralPath $ProfilePath -PathType Leaf)){
    throw 'An existing absolute private profile is required; see portable-manager.md'
}
$Code=Split-Path -Parent $PSScriptRoot
$Python=Join-Path (Split-Path -Parent $Code) 'runtime\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $Python -PathType Leaf)){throw 'Installed manager runtime missing'}
# Keep normal user's token. The application validates profile storage and binds loopback.
& $Python -B (Join-Path $Code 'scripts\biometric_manager.py') --monitor-only --profile $ProfilePath --no-browser
if($LASTEXITCODE -ne 0){throw 'Manager stopped with an error; review its private diagnostics'}
