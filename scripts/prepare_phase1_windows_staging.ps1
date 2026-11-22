# Step 2 only: new private fixture directory. No production config, devices or services.
$ErrorActionPreference = 'Stop'

function Invoke-StagingOpenSsl {
    param([string[]]$OpenSslArguments)
    # Windows PowerShell 5.1 turns normal native stderr (including "-----") into
    # error records. Handle it only in this function; the caller remains strict.
    $ErrorActionPreference = 'Continue'
    # Native commands update the global variable in Windows PowerShell 5.1.
    # A local variable would shadow it and hide the real exit status.
    $global:LASTEXITCODE = $null
    & $openssl @OpenSslArguments 2>&1 | Out-Null
    $nativeExitCode = $global:LASTEXITCODE
    if ($null -eq $nativeExitCode -or $nativeExitCode -ne 0) {
        # Never echo native output: future OpenSSL diagnostics may contain paths.
        throw 'Local OpenSSL operation failed. Stop; do not use the generated files.'
    }
}

function Test-StagingCertificate {
    param([string]$Certificate, [string]$PrivateKey, [string]$StageRoot)
    foreach ($file in @($Certificate, $PrivateKey)) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file).Length -eq 0) {
            throw 'Generated certificate or private key is missing or empty.'
        }
    }
    Invoke-StagingOpenSsl -OpenSslArguments @('x509', '-in', $Certificate, '-noout', '-checkend', '0')
    Invoke-StagingOpenSsl -OpenSslArguments @('pkey', '-in', $PrivateKey, '-check', '-noout')
    # Compare public keys only; private key material is never sent to stdout.
    $certificatePublicKey = Join-Path $StageRoot 'certificate-public.pem'
    $derivedPublicKey = Join-Path $StageRoot 'key-public.pem'
    Invoke-StagingOpenSsl -OpenSslArguments @('x509', '-in', $Certificate, '-pubkey', '-noout', '-out', $certificatePublicKey)
    Invoke-StagingOpenSsl -OpenSslArguments @('pkey', '-in', $PrivateKey, '-pubout', '-out', $derivedPublicKey)
    if ((Get-FileHash -LiteralPath $certificatePublicKey).Hash -ne (Get-FileHash -LiteralPath $derivedPublicKey).Hash) {
        throw 'Generated certificate and private key do not match.'
    }
}
$repo = Split-Path -Parent $PSScriptRoot
$revision = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $revision -ne '4256cd6201b17dcda3f9d078552acf1d3c58b3f0') {
    throw 'Expected the pinned 4256cd6 checkout. Stop.'
}
$openssl = Join-Path (Split-Path -Parent (Split-Path -Parent (Get-Command git -ErrorAction Stop).Source)) 'usr\bin\openssl.exe'
if (-not (Test-Path -LiteralPath $openssl -PathType Leaf)) { throw 'Git OpenSSL not available. Stop.' }
$localRoot = [Environment]::GetFolderPath('LocalApplicationData')
if (-not [IO.Path]::IsPathRooted($localRoot) -or $localRoot.StartsWith('\\')) { throw 'Local staging root required.' }
$stage = Join-Path $localRoot ('FPF-Phase1-Staging-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls $stage /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Private staging ACL creation failed.' }
$acl = Get-Acl -LiteralPath $stage
$allowed = @($sid, 'S-1-5-18', 'S-1-5-32-544')
if (-not $acl.AreAccessRulesProtected) { throw 'Staging ACL inheritance is not protected.' }
foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -eq 'Allow' -and $allowed -notcontains $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value) {
        throw 'Unexpected staging ACL grant. Stop.'
    }
}
$fixture = Join-Path $stage 'phase1_staging_https.py'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'phase1_staging_https.py') -Destination $fixture -ErrorAction Stop
if ((Get-FileHash $fixture).Hash -ne (Get-FileHash (Join-Path $PSScriptRoot 'phase1_staging_https.py')).Hash) {
    throw 'Fixture transfer hash mismatch.'
}
$configuration = Join-Path $stage 'certificate-request.cnf'
@('[req]', 'distinguished_name=dn', '[dn]', 'CN=localhost') | Set-Content -LiteralPath $configuration -Encoding ASCII
$certificate = Join-Path $stage 'localhost-cert.pem'
$privateKey = Join-Path $stage 'localhost-key.pem'
Invoke-StagingOpenSsl -OpenSslArguments @('req', '-quiet', '-x509', '-newkey', 'rsa:2048', '-noenc',
    '-days', '2', '-config', $configuration, '-subj', '/CN=localhost',
    '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost', '-addext', 'basicConstraints=critical,CA:TRUE',
    '-keyout', $privateKey, '-out', $certificate)
Test-StagingCertificate -Certificate $certificate -PrivateKey $privateKey -StageRoot $stage
Write-Output "Staging directory: $stage"
& py -3 -I -S -B $fixture --stage-root $stage
if ($LASTEXITCODE -ne 0) { throw 'HTTPS isolation self-test failed; retain private results for review.' }
Write-Output 'STEP 2 PASS: localhost HTTPS and process-local isolation verified. Mock has stopped.'
