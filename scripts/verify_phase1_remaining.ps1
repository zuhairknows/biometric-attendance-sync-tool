# One offline final check run. No network, SCM operations or deployment.
param([Parameter(Mandatory=$true)][string]$StageRoot)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$stage = (Get-Item -LiteralPath $StageRoot -Force).FullName
if ((Split-Path -Parent $stage) -ne [Environment]::GetFolderPath('LocalApplicationData') -or
    (Split-Path -Leaf $stage) -notlike 'FPF-Phase1-Staging-*') {
    throw 'Expected the existing local staging directory.'
}
$item = Get-Item -LiteralPath $stage -Force
while ($null -ne $item) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Reparse path refused.' }
    $item = $item.Parent
}
$revision = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $revision -ne '4256cd6201b17dcda3f9d078552acf1d3c58b3f0') {
    throw 'Expected pinned 4256cd6 checkout. No branch change is performed.'
}
$attempt = Join-Path $stage ('final-remaining-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $attempt | Out-Null
# Apply permissions only to the new empty directory, never existing evidence.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true, $false)
foreach ($principal in @($sid.Value, 'S-1-5-18', 'S-1-5-32-544')) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($principal), 'FullControl',
        'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $attempt -AclObject $acl
$actual = Get-Acl -LiteralPath $attempt
if (-not $actual.AreAccessRulesProtected) { throw 'New report storage is not private.' }
foreach ($rule in $actual.Access) {
    if ($rule.AccessControlType -eq 'Allow' -and
        @($sid.Value, 'S-1-5-18', 'S-1-5-32-544') -notcontains $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value) {
        throw 'Unexpected report directory grant.'
    }
}
$tempRoot = Join-Path $attempt 'scratch'
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$savedTemp = $env:TEMP
$savedTmp = $env:TMP
$stdout = Join-Path $attempt 'stdout.txt'
$stderr = Join-Path $attempt 'stderr.txt'
try {
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot
    $python = (Get-Command py.exe -CommandType Application -ErrorAction Stop).Source
    # Isolated Python excludes sitecustomize, user packages and PYTHONPATH.
    # Redirected native stderr is not treated as a PS 5.1 exception.
    $process = Start-Process -FilePath $python -ArgumentList @('-3', '-I', '-S', '-B', '-m',
        'unittest', 'discover', '-s', 'tests', '-p', 'test_phase1_remaining.py', '-v') `
        -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $processHandle = $process.Handle # Preserve the native exit code on PS 5.1.
    if (-not $process.WaitForExit(30000)) {
        $process.Kill() # Only our exact offline test child; never a service/name lookup.
        $process.WaitForExit()
        throw 'Offline checks timed out. Preserve this attempt.'
    }
    $process.WaitForExit() # Drain redirected output after confirmed termination.
    $exitCode = $process.ExitCode
} finally {
    $env:TEMP = $savedTemp
    $env:TMP = $savedTmp
}
$text = [IO.File]::ReadAllText($stderr)
$passed = ($null -ne $exitCode -and $exitCode -eq 0 -and
    $text -match '(?m)^Ran 14 tests in ' -and $text -match '(?m)^OK\s*$')
$files = @('durable_sync.py', 'durable_startup.py', 'durable_delivery.py', 'sync_ledger.py',
    'punch_normalization.py', 'erpnext_sync_win.py', 'SMWinservice.py', 'sync_cli.py',
    'legacy_import.py', 'tests\test_phase1_remaining.py', 'tests\test_service_hardening.py',
    'tests\test_phase1_release.py', 'tests\test_durable_sync.py', 'tests\test_erpnext_sync.py',
    'scripts\phase1_staging_engine_https.py', 'scripts\verify_phase1_remaining.ps1')
$hashes = @{}
foreach ($file in $files) {
    $hashes[$file] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $repo $file)).Hash.ToLowerInvariant()
}
$hashes['stdout.txt'] = (Get-FileHash -LiteralPath $stdout).Hash.ToLowerInvariant()
$hashes['stderr.txt'] = (Get-FileHash -LiteralPath $stderr).Hash.ToLowerInvariant()
@{ status= $(if ($passed) { 'PASS' } else { 'FAIL' }); tests_expected=14; exit_code=$exitCode;
   revision=$revision; sha256=$hashes; activation_ready=$false; real_scm_verified=$false;
   production_frappe_verified=$false; service_operations=$false;
   scope='remaining_offline_checks_only'; recorded_at_utc=[DateTime]::UtcNow.ToString('o') } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $attempt 'result.json') -Encoding UTF8
Write-Output "Evidence directory: $attempt"
if (-not $passed) { throw 'Remaining offline checks FAIL. Preserve the logs; stop.' }
Write-Output 'REMAINING OFFLINE CHECKS PASS: 14 tests. Activation remains blocked on SCM and Frappe evidence.'
