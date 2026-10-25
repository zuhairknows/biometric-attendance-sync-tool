# Portable Windows installation candidate

Review/acceptance candidate, not deployment authorization. Review the package and
driver before elevation. Do not use legacy install.py for this workflow.

## Prerequisites

Windows 11 x64, Windows PowerShell 5.1+ x64, local NTFS storage and protected
official CPython 3.11.9 x64. An administrator separately approves runtime provenance,
owner/ACL/ancestor replacement safety and the package SHA256. User-owned base
Python and base venvs are rejected. No Python installation, PATH/association change,
Windows account, firewall/trust-store change or reboot is performed by these tools.
Git/OpenSSL/compiler are not target-runtime requirements.

Nineteen pins are frozen in docs/requirements-windows311-accepted.txt. Build consumes
independently approved wheels; it never downloads/rebuilds them. Retain PyPI and
local pyzk source/content provenance approval outside the ZIP. The local pyzk
build-history log is absent: an evidence limitation, not proof of compromise.

Prepare a NEW private config from deployment/local_config.py.template, outside Git,
in a SYSTEM/Administrators-only trusted directory. Fill actual device IDs/addresses/
ports/passwords, HTTPS ERPNext endpoint/API credentials, import boundary, shift
mapping and absolute state paths. New installation uses DATA_ROOT\data\sync.sqlite3
and DATA_ROOT\logs. Defaults derive from ProgramFiles/ProgramData, not a PC name.
Recovery/coordination stay False. None selects strict storage. Optional monitor SID
needs separate ordinary-identity approval and actual token acceptance.

Dynamic/duplicate Python config is rejected for separate review, never executed by
the installer. Existing service configuration still executes at normal import.

## Reproducible local build

Create dist beforehand. Use the accepted development interpreter; no downloads:
~~~powershell
$Root='C:\GitHub\biometric-attendance-sync-tool'
$Py=Join-Path $Root '.acceptance\acl-compat\.venv\Scripts\python.exe'
& $Py -B "$Root\deployment\portable_release.py" build --root $Root --wheels "$Root\.acceptance\scm-wheels" --wheel-manifest-sha256 18082a6431702dfd3c193e4b4a78afa451c6025fbf65cc39ac5a58448ffbeb6e --version 2.0.0-rc1 --output "$Root\dist\fpf-biometric-sync-2.0.0-rc1.zip"
~~~
Fixed ZIP dates/order/mode and stored bytes produce identical output for identical
input bytes. Manifest records source commit and content identity; dirty candidates
are marked and cannot be activated/installed. Build a reviewed clean commit for
Windows acceptance. Existing output is never overwritten. Hashes identify bytes,
not publisher authenticity: independently approve the external ZIP digest.

## Explicit target stages

Review/extract only the driver initially, verify it against the approved package,
and use no-profile elevated PowerShell. Package bytes are copied into fresh protected
storage, rehashed and validated before execution. No machine change in Preflight.

~~~powershell
$Driver='C:\ApprovedMedia\deployment\Install-FPFBiometricSync.ps1'
$Common=@{
  Package='C:\ApprovedMedia\fpf-biometric-sync-2.0.0-rc1.zip'
  PackageSha256='<independently approved lowercase SHA256>'
  PythonExe='C:\Program Files\ApprovedPython311\python.exe'
  Config='C:\ApprovedPrivateConfig\local_config.py'
  AppRoot="$env:ProgramFiles\FPFBiometricSync"
  DataRoot="$env:ProgramData\FPFBiometricSync"
}
& $Driver -Stage Preflight @Common
# Separate approval starts mutations:
& $Driver -Stage Prepare @Common -Approve
~~~

Prepare makes a unique version directory, copies source/private config, creates a
copies venv, installs offline binary wheels with --require-hashes, runs pip check,
verifies 19 pins and exact SpeedFace patch bytes. Store the printed receipt privately.
It does not provision a ledger, register/start services or contact networks.
Failed slots remain for investigation; no automatic recursive deletion/reuse.

~~~powershell
$Receipt='<printed absolute installation.json>'
& $Driver -Stage Activate -Receipt $Receipt -Approve -ExclusiveHostWindow -Cutover '<future local YYYY-MM-DD HH:MM:SS>' -ReviewReference '<approved reference>'
# If configured, additionally supply independently approved SID as -MonitorSidApproval.
& $Driver -Stage Install -Receipt $Receipt -Approve -ExclusiveHostWindow -CheckNetwork
~~~

Activate is only for NEW empty installations; service/data/log directory collisions
are refused. It reuses explicit activation and grants an approved optional monitor
only directory RX/ledger R, without propagation. Other private data stays SYSTEM/
Administrators-only. Code/runtime allow Users RX; private config is protected
separately. No existing data ACL is repaired or changed.

CheckNetwork explicitly authorizes TCP checks to device ports and one verified-TLS
GET /api/method/ping. No device commands, redirects or POSTs in this probe.
TCP/ping success is not business-permission acceptance. The actual installed
service may POST legitimate attendance as ordinary synchronization.

Install registers only ERPNextBiometricPushService, LocalSystem, delayed automatic.
Health requires stable actual PID/token, fresh captures for every configured device,
durable_health and HTTP 200 ordinary delivery within 600s (allowed 30..1800s).
Have legitimate traffic available. Do not invent production punches to pass.
Failure stops the recognized service, verifies Manual startup and retains all data.

Installer controller mutex is separate from the unchanged application mutex.
An exclusive host-use window is mandatory: other administrators/schedulers can
race between stages. Unreadable Python process metadata, another worker/mutex or
stop timeout is NO-GO. No forced service-worker kill is provided.

~~~powershell
& $Driver -Stage Uninstall -Receipt $Receipt -Approve -ExclusiveHostWindow
~~~
Only the recognized stopped service registration is removed. Database, activation,
private config, all releases and backups stay on disk. Deletion pending needs review
of management handles; never delete data to bypass it.

## Ownership and interrupted preparation

Prepare normalizes owners/ACLs on only its fresh package/code/runtime trees before
privileged execution. It tests the elevated token's default file owner with one
empty, uniquely named probe. An untrusted default owner is NO-GO; do not change
machine security policy to force acceptance. Successful probes alone are removed;
failed empty probes and incomplete version slots remain for review.

A native child timeout can leave a venv redirector's child or partial preparation
behind. The tool never deletes that tree or adopts it on retry. Obtain a readable
process inventory, identify the owned operation and review recovery independently.
It never force-kills a synchronization worker.
